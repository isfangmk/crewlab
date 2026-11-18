"""从 FastAPI OpenAPI schema 反射全部端点，分类并生成最小合法请求体。

不手工枚举 212 个端点——app.openapi() 是唯一事实源，路由增删自动跟随。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.main import create_app

# 这些端点不能进常规压测：SSE 长连接、真调 LLM、烧钱或造脏数据、外部网络。
# 用 operationId 关键词匹配（比路径更稳），逐条在报告里说明排除原因。
# 写操作里能安全压测的：只作用于 __perf__ 专属会话/队列/记忆，可用 tag 隔离并清理。
# 用 operationId 精确白名单（比路径前缀稳，避免误放行 batch-delete/share/fork）。
# 其他写操作（用户/密钥/知识库/工作流/分享等）要么会造不可清理的配置垃圾，
# 要么参数用占位 id 除了 404 没有压测价值，统一排除并在报告说明。
_WRITE_SAFE_OIDS = {
    "create_session",          # 建会话
    "patch_session",           # 改会话标题等
    "delete_session",          # 删 __perf__ 会话
    "enqueue_message",         # 入队
    "update_queued_message",   # 改队列项
    "interject_queued_message",# 插话
    "delete_queued_message",   # 删队列项
    "create_memory",           # 记忆 CRUD
    "update_memory",
    "delete_memory",
}

_EXCLUDE_OID_TOKENS = (
    "stream",          # SSE / 流式：/events、messages/stream、approve/stream、playground
    "playground",      # 真调 LLM
    "run_tool",        # 执行 MCP 工具
    "run_builtin_tool",# 执行内置工具
    "run_sandbox_code",# 沙盒执行
    "reindex",         # 全量重建索引
    "sync_source",     # 外部源同步（网络）
    "install_skill",   # 从网址安装技能（网络+落盘）
    "test_server",     # MCP 连通性测试（网络）
    "test_llm",        # LLM 连通性测试
    "validate_and_save",  # 探测远程模型（网络）
    "validate_workflow",  # 工作流校验（可能跑图）
    "start_run",       # 启动 eval run（真调 LLM）
    "fire_trigger",    # 触发器真正触发
    "fire_webhook",    # webhook 触发
    "resume_run",      # 恢复工作流运行
    "stop_run",        # 停止运行
    "start_workflow_run",  # 启动工作流
    "run_single_node", # 单节点执行
)

# 即便 operationId 没命中，这些路径片段也要排除
_EXCLUDE_PATH_TOKENS = (
    "/events",
    "/messages/stream",
    "/approve/stream",
    "/fire",
    "/shares/{token}",  # 分享令牌是公开读、非压测目标
)


@dataclass
class Endpoint:
    method: str
    path: str
    op_id: str
    kind: str  # read | write | write_excluded | excluded
    reason: str = ""
    body: dict[str, Any] = field(default_factory=dict)
    query: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}"


def _resolve_ref(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    """解析 $ref 到实际 schema（只处理本地组件引用）。"""

    ref = schema.get("$ref")
    if not ref or not ref.startswith("#/components/schemas/"):
        return schema
    name = ref.split("/")[-1]
    return components.get(name, {})


def _pick_any(subs: list[dict[str, Any]], components: dict[str, Any]) -> dict[str, Any]:
    """从 anyOf/oneOf 里挑第一个非空类型（跳过 null）。"""

    for sub in subs:
        resolved = _resolve_ref(sub, components)
        t = resolved.get("type")
        if t and t != "null":
            return resolved
    return subs[0] if subs else {}


def _sample_value(schema: dict[str, Any], components: dict[str, Any], key: str = "") -> Any:
    """按 schema 生成最小合法样例值：必填字段给合法值，类型嵌套递归。"""

    if "anyOf" in schema or "oneOf" in schema:
        return _sample_value(_pick_any(schema.get("anyOf") or schema.get("oneOf"), components), components, key)
    resolved = _resolve_ref(schema, components)
    if "enum" in resolved:
        return resolved["enum"][0]
    t = resolved.get("type")
    if t == "object" or "properties" in resolved:
        return _sample_object(resolved, components)
    if t == "array":
        item = _sample_value(resolved.get("items", {}), components, key)
        return [item]
    if t == "integer" or t == "number":
        return resolved.get("minimum", 1)
    if t == "boolean":
        return False
    # string：按字段名/格式给有辨识度的合法值
    fmt = resolved.get("format", "")
    if fmt == "email":
        return "perf@example.invalid"
    if fmt == "date-time":
        return "2026-01-01T00:00:00Z"
    if fmt == "uuid":
        return "00000000-0000-0000-0000-000000000001"
    lower = key.lower()
    if lower in ("content", "title", "name", "question", "description", "prompt"):
        return "__perf__ 压测样例内容"
    if lower.endswith("_id") or lower == "id":
        return "__perf_id__"
    return "__perf__"


def _sample_object(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    out: dict[str, Any] = {}
    for key, sub in props.items():
        # 只生成必填字段 + 少量安全的可选字段，保持请求体最小
        if key in required:
            out[key] = _sample_value(sub, components, key)
    return out


def _classify(method: str, path: str, op_id: str) -> tuple[str, str]:
    oid = op_id.lower()
    low = path.lower()
    for token in _EXCLUDE_OID_TOKENS:
        if token in oid:
            return "excluded", f"operationId 命中排除词: {token}"
    for token in _EXCLUDE_PATH_TOKENS:
        if token in low:
            return "excluded", f"路径命中排除词: {token}"
    if "login" in oid:
        return "excluded", "登录端点不纳入压测"
    if method == "get":
        return "read", ""
    # 写操作：仅 tag 可隔离的白名单纳入，其余排除
    if any(oid.startswith(safe) for safe in _WRITE_SAFE_OIDS):
        return "write", ""
    return "write_excluded", "写操作无法安全隔离（用户/密钥/配置等），未纳入并发压测"


def discover() -> list[Endpoint]:
    schema = create_app().openapi()
    components = schema.get("components", {}).get("schemas", {})
    endpoints: list[Endpoint] = []
    for path, ops in schema["paths"].items():
        for method, op in ops.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            op_id = op.get("operationId", "")
            kind, reason = _classify(method, path, op_id)
            body: dict[str, Any] = {}
            req_body = op.get("requestBody", {}).get("content", {}).get("application/json", {})
            body_schema = req_body.get("schema")
            if body_schema:
                body = _sample_value(body_schema, components)
            # 必填 query 参数也要生成合法样例，否则 422 会让端点走不到业务逻辑，
            # 计时只反映校验失败路径（不是接口性能）。可选参数留空保持请求最小。
            query: dict[str, Any] = {}
            for param in op.get("parameters", []):
                if param.get("in") != "query" or not param.get("required"):
                    continue
                query[param["name"]] = _sample_value(
                    param.get("schema", {}), components, param["name"]
                )
            endpoints.append(
                Endpoint(method=method.upper(), path=path, op_id=op_id,
                         kind=kind, reason=reason, body=body if isinstance(body, dict) else {},
                         query=query)
            )
    return endpoints


if __name__ == "__main__":
    eps = discover()
    from collections import Counter
    counts = Counter(e.kind for e in eps)
    print(
        f"端点总数={len(eps)} read={counts['read']} write={counts['write']} "
        f"write_excluded={counts['write_excluded']} excluded={counts['excluded']}"
    )
    for e in eps:
        print(f"  {e.kind:8} {e.method:6} {e.path}")
