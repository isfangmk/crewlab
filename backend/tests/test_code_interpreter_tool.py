"""代码解释器工具：真实沙盒执行 + Artifact 落库（无 mock）。"""

from __future__ import annotations

import json

import pytest

from app.adk_runtime.workflow_node_impl import SANDBOX_RESULT_MARKER
from app.config import get_settings
from app.tools.code_interpreter import (
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACTS,
    _build_chat_script,
    _parse_chat_payload,
    remember_pending,
    run_python,
    take_pending,
    validate_artifacts,
)


def test_build_chat_script_contains_code_and_marker() -> None:
    script = _build_chat_script("print('hi')")
    assert "print('hi')" in script
    assert SANDBOX_RESULT_MARKER in script
    # emit 助手与图表结构构建器在脚本里预置
    assert "emit_chart" in script and "emit_file" in script
    assert "build_chart_option" in script and "def chart(" in script


def test_parse_chat_payload_takes_last_envelope() -> None:
    stdout = "用户 print 的内容\n" + SANDBOX_RESULT_MARKER + '{"artifacts": [], "error": ""}'
    payload = _parse_chat_payload(stdout)
    assert payload["error"] == ""
    with pytest.raises(RuntimeError, match="结果信封"):
        _parse_chat_payload("没有任何信封")


async def test_run_python_executes_and_collects_chart() -> None:
    """真实沙盒（local 执行器）跑真实代码：print 回传、图表产出结构正确。"""

    code = (
        "print('开始计算')\n"
        "result = {'total': 1 + 2}\n"
        "emit_chart({'xAxis': {'type': 'category', 'data': ['a', 'b']},"
        " 'yAxis': {'type': 'value'}, 'series': [{'type': 'bar', 'data': [1, 2]}]}, '示例图')\n"
        "emit_table({'columns': ['项', '值'], 'rows': [['total', 3]]}, '汇总')\n"
        "print('结束', result['total'])\n"
    )
    result = await run_python(code, purpose="图表产出验证", tool_context=None)
    assert result["status"] == "succeeded", result["error"]
    assert "开始计算" in result["stdout_tail"] and "结束 3" in result["stdout_tail"]
    assert result["error"] == ""
    # 无 tool_context（手动运行口径）：payload 直接进返回值
    assert result["artifacts"][0]["kind"] == "chart"
    assert result["artifacts"][0]["payload"]["series"][0]["data"] == [1, 2]
    assert result["artifacts"][1]["kind"] == "table"


async def test_run_python_chart_helper_builds_structure() -> None:
    """chart() 助手在沙盒里产出图表产出物：结构映射真实跑通，不进输出 token。"""

    code = (
        "option = chart({'x': ['一', '二'], 'series': [{'name': '销量', 'data': [3, 5]}]},"
        " 'line', '月度销量')\n"
        "print(option['series'][0]['type'])\n"
    )
    result = await run_python(code, purpose="chart 助手验证", tool_context=None)
    assert result["status"] == "succeeded", result["error"]
    artifact = result["artifacts"][0]
    assert artifact["kind"] == "chart"
    assert artifact["title"] == "月度销量"
    assert artifact["payload"]["series"][0]["type"] == "line"
    assert artifact["payload"]["series"][0]["data"] == [3, 5]
    # 结构零配色：颜色由前端主题注入
    assert '"color"' not in json.dumps(artifact["payload"], ensure_ascii=False)


async def test_run_python_file_artifact_bytes_roundtrip() -> None:
    """emit_file 的 base64 在落库侧还原为原始字节。"""

    import base64

    raw = "列A,列B\n1,2\n".encode("utf-8")
    code = (
        f"emit_file('result.csv', '{base64.b64encode(raw).decode()}', 'text/csv')\n"
    )
    result = await run_python(code, tool_context=None)
    assert result["status"] == "succeeded"
    decoded = base64.b64decode(result["artifacts"][0]["payload"]["data"])
    assert decoded == raw


async def test_run_python_error_returns_traceback() -> None:
    """用户代码抛异常：error 带回 traceback，status=failed，沙盒仍留执行记录。"""

    code = "raise ValueError('故意失败')"
    result = await run_python(code, tool_context=None)
    assert result["status"] == "failed"
    assert "ValueError" in result["error"] and "故意失败" in result["error"]


def test_pending_registry_roundtrip() -> None:
    """暂存区按 tool_call_id 取走并清除；未知 id 返回空。"""

    remember_pending("fc-1", [{"kind": "chart", "payload": {"a": 1}}])
    assert take_pending("fc-1") == [{"kind": "chart", "payload": {"a": 1}}]
    assert take_pending("fc-1") == []


def test_validate_artifacts_rejects_invalid_kind_and_oversize() -> None:
    """产出物类型与体积都要在进内存/落库前显式拒绝。"""

    with pytest.raises(ValueError, match="kind 非法"):
        validate_artifacts([{"kind": "exe", "payload": {}}])
    with pytest.raises(ValueError, match="最多"):
        validate_artifacts([{"kind": "text", "payload": "x"}] * (MAX_ARTIFACTS + 1))
    import base64

    oversize = base64.b64encode(b"x" * (MAX_ARTIFACT_BYTES + 1)).decode()
    with pytest.raises(ValueError, match="体积超限"):
        validate_artifacts(
            [{"kind": "file", "payload": {"name": "big.bin", "data": oversize}}]
        )
    with pytest.raises(ValueError, match="base64"):
        validate_artifacts(
            [{"kind": "file", "payload": {"name": "bad.bin", "data": "!!!"}}]
        )


async def test_run_python_failure_recorded_in_sandbox_executions() -> None:
    """执行记录进 sandbox_executions（source=chat），失败也留痕。"""

    import sqlite3

    await run_python("print('ok')", tool_context=None)
    db_path = get_settings().database_url.replace("sqlite+aiosqlite:///", "")
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT source, status FROM sandbox_executions ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "chat"
