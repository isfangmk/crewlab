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
from functools import lru_cache

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

    def parameter_specs(self) -> list[dict]:
        """从 OpenAPI 原生信息整理出参数清单，供模型构造 arguments。

        名字、位置、是否必填、描述、默认值全部来自 FastAPI/Pydantic 生成的
        OpenAPI（路由 docstring / Query/Path/Field(description=) / default），
        这里只做格式整理，不自行维护任何描述或默认值。
        """
        specs: list[dict] = []
        # path 参数：从 URL 占位符解析（required 恒为 true）
        for name in re.findall(r"\{(\w+)\}", self.path):
            specs.append(
                {"name": name, "in": "path", "required": True, "description": "", "default": None}
            )
        # query 参数：OpenAPI 已声明（内部头已剔除）
        for p in self.parameters:
            if p.get("in") == "query":
                schema = p.get("schema", {}) or {}
                specs.append(
                    {
                        "name": p.get("name", ""),
                        "in": "query",
                        "required": bool(p.get("required", False)),
                        "description": str(p.get("description", "") or ""),
                        "default": schema.get("default"),
                    }
                )
        # body 字段：required 来自 schema.required，default/description 来自字段 schema
        if self.body_schema is not None:
            required = set(self.body_schema.get("required", []))
            for name, prop in (self.body_schema.get("properties", {}) or {}).items():
                if not isinstance(prop, dict):
                    prop = {}
                specs.append(
                    {
                        "name": name,
                        "in": "body",
                        "required": name in required,
                        "description": str(prop.get("description", "") or ""),
                        "default": prop.get("default"),
                    }
                )
        return specs


def _openapi_schema() -> dict:
    """延迟导入 app.main 取 OpenAPI 视图，避免 catalog 装配期的循环导入。"""

    from app.main import app

    return app.openapi()


def _resolve_ref(schema: dict) -> dict:
    """解析 application/json 的顶层 $ref 到实际 schema（只解一层即可覆盖 body 模型）。"""
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/components/schemas/"):
        return schema
    name = ref.rsplit("/", 1)[-1]
    resolved = _openapi_schema().get("components", {}).get("schemas", {}).get(name)
    return resolved if isinstance(resolved, dict) else schema


@lru_cache(maxsize=1)
def _catalog_entries() -> tuple[PlatformApiEntry, ...]:
    """路由在启动时注册完即固定，目录是纯派生结果，缓存一次避免每次调用重复遍历 198 个路由。"""

    return tuple(_build_api_catalog())


def build_api_catalog() -> list[PlatformApiEntry]:
    """遍历 FastAPI OpenAPI 生成目录（缓存结果）；返回结果稳定排序，便于向量同步对比。"""

    return list(_catalog_entries())


def _build_api_catalog() -> list[PlatformApiEntry]:
    schema = _openapi_schema()
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


@lru_cache(maxsize=1)
def _entries_by_id() -> dict[str, PlatformApiEntry]:
    return {entry.api_id.upper(): entry for entry in _catalog_entries()}


def entry_by_id(api_id: str) -> PlatformApiEntry | None:
    """按 api_id 精确查目录项（调用入口用，避免模型构造任意路径越权）。"""

    return _entries_by_id().get(api_id.strip().upper())
