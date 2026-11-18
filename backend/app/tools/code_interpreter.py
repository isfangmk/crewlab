"""对话内代码解释器：模型生成 Python，沙盒执行，结构化产出物带出。

产出物通过脚本内注入的 emit_* 助手收集，随结果信封（JSON，走 stdout 标记行）
带回宿主机；payload 会落 artifacts 表，供消息卡与 Artifacts 侧栏渲染。
"""

from __future__ import annotations

import base64
import json
import threading
from pathlib import Path
from typing import Any

from app.adk_runtime.headless import HEADLESS_SESSION_ID_KEY
from app.adk_runtime.workflow_node_impl import SANDBOX_RESULT_MARKER
from app.services.sandbox import sandbox_service

# 产出物上限：模型生成的代码不可信，base64 文件与图表 JSON 都可能在宿主内存里膨胀
ARTIFACT_KINDS = ("chart", "table", "file", "text", "mermaid")
MAX_ARTIFACTS = 20
MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
MAX_ARTIFACT_TOTAL_BYTES = 10 * 1024 * 1024

# chart_spec.py 源码内嵌进沙盒脚本：沙盒只跑单文件，
# 图表数据→结构的转换逻辑与后端单测同源（后端直接 import，沙盒侧文本内嵌）。
# 源码含模块 docstring 与 from __future__，必须拼在脚本最前面才合法。
_CHART_SPEC_SOURCE = (Path(__file__).with_name("chart_spec.py")).read_text(encoding="utf-8")

# 模型可见的工具描述写在 docstring；这里只放脚本模板。
# 占位符用 __CODE__/__MARKER__ 替换而不是 str.format：
# 模板里的 dict 字面量含大括号，format 转义极易写错。
CHAT_INTERPRETER_TEMPLATE = _CHART_SPEC_SOURCE + '''
import json as _json
import traceback as _traceback

_ARTIFACTS = []


def chart(data, chart_type="line", title="图表"):
    """收集一个图表产出物：data 传数据契约 dict，返回生成的 ECharts option。"""
    option = build_chart_option(data, chart_type)
    emit_chart(option, title)
    return option


def emit_chart(option, title="图表"):
    """收集一个 ECharts option（dict），宿主机以图表渲染。"""
    _ARTIFACTS.append({"kind": "chart", "title": str(title), "mime": "application/json", "payload": option})


def emit_table(rows, title="表格"):
    """收集表格：rows 为 {"columns": [...], "rows": [[...], ...]} 或 list[dict]。"""
    _ARTIFACTS.append({"kind": "table", "title": str(title), "mime": "application/json", "payload": rows})


def emit_file(name, content_b64, mime="application/octet-stream"):
    """收集文件：内容为 base64 字符串（如 csv/png），宿主机还原字节供下载。"""
    _ARTIFACTS.append({"kind": "file", "title": str(name), "mime": mime,
                       "payload": {"name": str(name), "data": str(content_b64)}})


def emit_mermaid(source, title="关系图"):
    """收集 Mermaid 结构图：source 为 mermaid 图定义文本（如 flowchart TD ...）。

    流程图 / 架构图 / 时序图 / 状态图等结构关系用这个，不要画图表的 chart()。
    """
    _ARTIFACTS.append({"kind": "mermaid", "title": str(title), "mime": "text/plain",
                       "payload": str(source)})


_ERROR = ""
try:
    _namespace = {"__name__": "__main__", "chart": chart, "emit_chart": emit_chart,
                  "emit_table": emit_table, "emit_file": emit_file,
                  "emit_mermaid": emit_mermaid}
    exec(compile(__CODE__, "<chat-interpreter>", "exec"), _namespace, _namespace)
except BaseException as _exc:  # noqa: BLE001 — 用户代码任意异常都要带回宿主机
    _ERROR = _traceback.format_exc()

print(__MARKER__ + _json.dumps({"artifacts": _ARTIFACTS, "error": _ERROR},
                               ensure_ascii=False, default=str))
'''

_SCRIPT_HEAD = CHAT_INTERPRETER_TEMPLATE.split("__CODE__")[0]
_SCRIPT_TAIL = CHAT_INTERPRETER_TEMPLATE.split("__MARKER__")[1]
_SCRIPT_MID = CHAT_INTERPRETER_TEMPLATE.split("__CODE__")[1].split("__MARKER__")[0]

# 工具执行到落库器消费之间的产物资助站（同事件循环内传递，锁只防极端并发）。
# 有上限：落库器异常时残留条目按先进先出淘汰，内存不无界增长。
_PENDING_MAX = 64
_PENDING: dict[str, list[dict[str, Any]]] = {}
_PENDING_LOCK = threading.Lock()


def remember_pending(tool_call_id: str, artifacts: list[dict[str, Any]]) -> None:
    """把本次执行的产出物按 tool_call_id 暂存，等落库器在 tool_end 帧取走。"""

    with _PENDING_LOCK:
        if len(_PENDING) >= _PENDING_MAX:
            oldest = next(iter(_PENDING))
            del _PENDING[oldest]
        _PENDING[tool_call_id] = artifacts


def take_pending(tool_call_id: str) -> list[dict[str, Any]]:
    """取走并清除暂存的产出物；没有则返回空列表。"""

    with _PENDING_LOCK:
        return _PENDING.pop(tool_call_id, [])


def _build_chat_script(code: str) -> str:
    """把模型代码包进解释器脚本：产出经 emit_* 收集，随标记行带回。"""

    return _SCRIPT_HEAD + repr(code) + _SCRIPT_MID + repr(SANDBOX_RESULT_MARKER) + _SCRIPT_TAIL


def _parse_chat_payload(stdout: str) -> dict[str, Any]:
    """从 stdout 取信封；缺失视为脚本未跑完，显式失败。"""

    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if not stripped.startswith(SANDBOX_RESULT_MARKER):
            continue
        raw = stripped[len(SANDBOX_RESULT_MARKER) :]
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"解释器结果解析失败: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("解释器结果格式非法：不是对象")
        return payload
    raise RuntimeError("解释器没有返回结果信封（脚本可能被强制终止）")


def validate_artifacts(artifacts: list[dict[str, Any]]) -> None:
    """校验产出物数量、类型与体积；超限显式失败，不静默丢弃。

    文件按解码后的字节计，图表/表格按 JSON 序列化后的 UTF-8 字节计。
    """

    if len(artifacts) > MAX_ARTIFACTS:
        raise ValueError(f"单次执行最多产出 {MAX_ARTIFACTS} 个 Artifact")
    total = 0
    for index, item in enumerate(artifacts):
        kind = item.get("kind", "text")
        if kind not in ARTIFACT_KINDS:
            raise ValueError(f"artifacts[{index}].kind 非法: {kind!r}")
        payload = item.get("payload")
        if kind == "file":
            if not isinstance(payload, dict):
                raise ValueError(f"artifacts[{index}].payload 必须是对象")
            raw_b64 = "".join(str(payload.get("data", "")).split())
            try:
                size = len(base64.b64decode(raw_b64, validate=True))
            except Exception as exc:  # noqa: BLE001 — 用户代码产出的内容不可信
                raise ValueError(f"artifacts[{index}] 文件内容不是合法 base64") from exc
        else:
            size = len(
                json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            )
        if size > MAX_ARTIFACT_BYTES:
            raise ValueError(
                f"artifacts[{index}] 体积超限: {size} > {MAX_ARTIFACT_BYTES} 字节"
            )
        total += size
    if total > MAX_ARTIFACT_TOTAL_BYTES:
        raise ValueError(
            f"Artifact 总体积超限: {total} > {MAX_ARTIFACT_TOTAL_BYTES} 字节"
        )


async def run_python(
    code: str,
    purpose: str = "通用计算与数据处理",
    tool_context: Any = None,
) -> dict:
    """在沙盒中执行一段 Python 代码，返回 stdout 摘要与结构化产出物。

    画图用 chart(data, chart_type="line", title="图表") 助手，不要手写 ECharts 配置：
    data 按图型给 dict——折线/面积 {"x": [...], "series": [{"name", "data": [...]}]}；
    柱/条/堆叠/分组/漏斗/箱线/瀑布 {"categories" 或 "x": [...], "series": [...]}（瀑布首项为起点、其后为增量）；
    饼/环形 {"categories": [...], "series": [{"name", "data": [...]}]}；
    散点 {"points": [{"x", "y"}]}；雷达 {"indicators": [{"name", "max"}], "series": [...]}；
    仪表盘 {"value": 0-100, "name"?}；热力图 {"x", "y", "values": 二维数组}；
    矩形树图 {"data": [{"name", "value", "children"?}]}。
    chart_type 支持 line/area/bar/hbar/stacked_bar/grouped_bar/pie/donut/scatter/
    radar/gauge/funnel/heatmap/treemap/boxplot/waterfall。
    emit_table(rows, title)、emit_file(name, content_b64, mime) 收集表格与文件；
    emit_chart(option, title) 可直接提交现成 ECharts option（高级用法）。
    流程图 / 架构图 / 时序图 / 状态图等结构关系用 emit_mermaid(source, title)，
    source 是 mermaid 图定义文本（如 "flowchart TD\\nA-->B"），由界面渲染成图；
    这类结构关系不要用 chart()（那是数据图表）。产出物会保存到会话的 Artifacts 并在消息卡中渲染。
    code 是普通 Python 顶层代码（不需要定义 run 函数），print 输出会回传。
    purpose: 这段代码要做什么的一句话说明（会记录在执行与产出物元数据里）。
    """

    if not isinstance(code, str) or code.strip() == "":
        raise ValueError("code 不能为空")

    session_id = ""
    function_call_id = ""
    if tool_context is not None:
        session = getattr(tool_context, "session", None)
        session_id = getattr(session, "id", "") or ""
        function_call_id = getattr(tool_context, "function_call_id", "") or ""
        # 无人值守 agent 的 session 是临时会话；产出物要归属真实聊天会话。
        state = getattr(tool_context, "state", None)
        if state is not None:
            real_id = state.get(HEADLESS_SESSION_ID_KEY) if hasattr(state, "get") else None
            if isinstance(real_id, str) and real_id:
                session_id = real_id

    script = _build_chat_script(code)
    result = await sandbox_service.run_code(
        code=script,
        ctx=sandbox_service.SandboxContext(
            source=sandbox_service.SOURCE_CHAT,
            source_ref=(purpose or "chat")[:120],
            session_id=session_id,
        ),
    )

    payload = _parse_chat_payload(result.stdout)
    error = payload.get("error", "")
    artifacts = payload.get("artifacts", [])
    if not isinstance(artifacts, list):
        raise RuntimeError("解释器产出的 artifacts 格式非法：不是数组")
    for index, item in enumerate(artifacts):
        if not isinstance(item, dict):
            raise RuntimeError(f"artifacts[{index}] 格式非法：不是对象")
    validate_artifacts(artifacts)

    stdout_lines = result.stdout.splitlines()
    user_stdout = "\n".join(
        line for line in stdout_lines if not line.startswith(SANDBOX_RESULT_MARKER)
    )
    stdout_tail = user_stdout[-4000:]

    status = result.status
    if status == "succeeded" and error:
        status = "failed"

    # 完整 payload 走进程内暂存（按 tool_call_id 由落库器取走），
    # 不进模型上下文：base64 文件与整份图表 JSON 会白白占用 token。
    # 手动运行（无 function_call_id）没有落库器，payload 直接进返回值供页面渲染。
    summary = [
        {
            "kind": item.get("kind", "text"),
            "title": item.get("title", ""),
            "mime": item.get("mime", "application/json"),
        }
        for item in artifacts
    ]
    if function_call_id:
        remember_pending(function_call_id, artifacts)
        for item in summary:
            item["pending"] = True
    else:
        for item, raw in zip(summary, artifacts, strict=True):
            item["payload"] = raw.get("payload")

    return {
        "status": status,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "purpose": purpose,
        "stdout_tail": stdout_tail,
        "error": error or result.error,
        "artifacts": summary,
        "sandbox_execution_id": result.execution_id,
    }
