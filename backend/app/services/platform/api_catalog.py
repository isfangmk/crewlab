"""平台接口目录：从 FastAPI OpenAPI 自动派生，供平台助手检索与调用。

不手工维护接口清单——新增/删除接口后重新同步即可自动生效（根源做法）。
每条目录项记录调用所需的最小信息：method + path + 参数 schema + 风险等级。
描述优先用 OpenAPI description（多数是中文），再拼 summary / path / 参数名，
兼顾中文语义检索与英文/路径关键词命中。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.main import app
from app.services.platform.internal_call import ACTOR_HEADER, SECRET_HEADER

logger = logging.getLogger(__name__)

# 非业务接口：健康检查、登录、SSE 事件流（长连接，不适合助手调用）、文件下载。
# 下载类接口返回原始字节，助手无法消费，排除。
_SKIP_PATH_RE = re.compile(
    r"/healthz$|/auth/login$|/events$|/docs$|/openapi\.json$"
)
_SKIP_DOWNLOAD_RE = re.compile(r"/download$")

# FastAPI 把 Bearer 依赖展开成 authorization header 参数；身份由内部调用注入，
# 不给模型暴露，避免它伪造或填错。
_INTERNAL_PARAM_NAMES = {"authorization", SECRET_HEADER, ACTOR_HEADER}

_HTTP_METHODS = ("get", "post", "put", "patch", "delete")


@dataclass(frozen=True)
class PlatformApiEntry:
    """一个可被调用的平台接口。"""

    api_id: str  # "METHOD /api/v1/..." 全局唯一，作为检索 ref_id
    method: str  # 大写
    path: str
    summary: str
    description: str
    parameters: list[dict] = field(default_factory=list)  # query/path/header 参数（已剔除内部参数）
    body_schema: dict | None = None  # requestBody application/json 的 schema（可含 $ref）
    risk: str = "safe"  # safe / mutating

    @property
    def search_text(self) -> str:
        """检索文本：中文描述 + 英文 summary + 路径 + 参数名/描述。

        与平台 API 向量入库文本必须一致（见 platform_api_index.sync_platform_api_embeddings）。
        """
        parts = [self.summary, self.description, self.path.replace("/api/v1", "").replace("/", " ")]
        for param in self.parameters:
            name = param.get("name", "")
            desc = param.get("description", "") or ""
            if name:
                parts.append(f"{name} {desc}".strip())
        if self.body_schema:
            # body 的字段名也进检索文本：模型常按"创建技能要给哪些字段"来查
            props = self.body_schema.get("properties", {})
            if isinstance(props, dict):
                parts.extend(f"{k} {v.get('description', '')}".strip() for k, v in props.items())
        return " ".join(p for p in parts if p)


def _resolve_ref(schema: dict) -> dict:
    """解析 application/json 的顶层 $ref 到实际 schema（只解一层即可覆盖 body 模型）。"""
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/components/schemas/"):
        return schema
    name = ref.rsplit("/", 1)[-1]
    resolved = app.openapi().get("components", {}).get("schemas", {}).get(name)
    return resolved if isinstance(resolved, dict) else schema


def build_api_catalog() -> list[PlatformApiEntry]:
    """遍历 FastAPI OpenAPI 生成目录。返回结果稳定排序，便于向量同步对比。"""
    schema = app.openapi()
    entries: list[PlatformApiEntry] = []
    for path, path_item in schema.get("paths", {}).items():
        if not path.startswith("/api"):
            continue
        if _SKIP_PATH_RE.search(path) or _SKIP_DOWNLOAD_RE.search(path):
            continue
        for method in _HTTP_METHODS:
            op = path_item.get(method)
            if not isinstance(op, dict):
                continue
            params = [
                p for p in op.get("parameters", [])
                if isinstance(p, dict) and p.get("name") not in _INTERNAL_PARAM_NAMES
            ]
            body_schema: dict | None = None
            body = op.get("requestBody")
            if isinstance(body, dict):
                content = body.get("content", {})
                json_part = content.get("application/json")
                if isinstance(json_part, dict) and isinstance(json_part.get("schema"), dict):
                    body_schema = _resolve_ref(json_part["schema"])
            entries.append(
                PlatformApiEntry(
                    api_id=f"{method.upper()} {path}",
                    method=method.upper(),
                    path=path,
                    summary=str(op.get("summary") or ""),
                    description=str(op.get("description") or ""),
                    parameters=params,
                    body_schema=body_schema,
                    risk="mutating" if method in ("post", "put", "patch", "delete") else "safe",
                )
            )
    entries.sort(key=lambda e: e.api_id)
    logger.info("平台接口目录已生成 apis=%s", len(entries))
    return entries


def entry_by_id(api_id: str) -> PlatformApiEntry | None:
    """按 api_id 精确查目录项（调用入口用，避免模型构造任意路径越权）。"""
    normalized = api_id.strip().upper()
    for entry in build_api_catalog():
        if entry.api_id.upper() == normalized:
            return entry
    return None
