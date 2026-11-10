"""助理知识范围：目录前缀 + 文档集，检索在 SQL 层收口。

口径：
  - 范围为空 = 继承全部知识库，不加任何条件
  - 目录按「本级或子级」匹配（`网络` 命中 `网络/DNS`，不命中 `网络安全`）
  - 目录之间、文档之间都是 OR；范围自身与请求里的 path/tags 过滤是 AND
  - 保存前逐项校验，命中 0 篇的目录/文档直接报错（不做静默降级）
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_orm.knowledge import KnowledgeDoc

MAX_SCOPE_PATHS = 20
MAX_SCOPE_DOC_IDS = 100


@dataclass(frozen=True)
class KnowledgeScope:
    """助理可检索的知识范围。"""

    paths: tuple[str, ...]
    doc_ids: tuple[str, ...]

    @property
    def is_empty(self) -> bool:
        return len(self.paths) == 0 and len(self.doc_ids) == 0


def empty_scope() -> KnowledgeScope:
    return KnowledgeScope(paths=(), doc_ids=())


def _parse_str_tuple(raw: object, field: str, limit: int) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise TypeError(f"knowledge_scope.{field} 必须是数组")
    if len(raw) > limit:
        raise ValueError(f"knowledge_scope.{field} 最多 {limit} 项")
    out: list[str] = []
    for i, item in enumerate(raw):
        if not isinstance(item, str):
            raise TypeError(f"knowledge_scope.{field}[{i}] 必须是字符串")
        if item == "":
            raise ValueError(f"knowledge_scope.{field}[{i}] 不能为空")
        if item not in out:
            out.append(item)
    return tuple(out)


def parse_scope(raw: object) -> KnowledgeScope:
    """解析请求/库里存的范围；字段缺失视同空范围，类型不符显式失败。"""

    if raw is None:
        return empty_scope()
    if isinstance(raw, KnowledgeScope):
        return raw
    if not isinstance(raw, dict):
        raise TypeError("knowledge_scope 必须是对象")
    unknown = set(raw.keys()) - {"paths", "doc_ids"}
    if unknown:
        raise ValueError(f"knowledge_scope 含未知字段: {sorted(unknown)}")
    return KnowledgeScope(
        paths=_parse_str_tuple(raw.get("paths"), "paths", MAX_SCOPE_PATHS),
        doc_ids=_parse_str_tuple(raw.get("doc_ids"), "doc_ids", MAX_SCOPE_DOC_IDS),
    )


def scope_to_wire(scope: KnowledgeScope) -> dict:
    return {"paths": list(scope.paths), "doc_ids": list(scope.doc_ids)}


def _path_condition(path: str) -> ColumnElement[bool]:
    """本级或子级：`p` 与 `p/...`；避免 `网络` 误命中 `网络安全`。"""

    return or_(
        KnowledgeDoc.path == path,
        KnowledgeDoc.path.startswith(f"{path}/"),
    )


def scope_condition(scope: KnowledgeScope | None) -> ColumnElement[bool] | None:
    """把范围转成 SQL 条件；空范围返回 None（不限制）。"""

    if scope is None or scope.is_empty:
        return None
    conds: list[ColumnElement[bool]] = [_path_condition(p) for p in scope.paths]
    if len(scope.doc_ids) > 0:
        conds.append(KnowledgeDoc.id.in_(list(scope.doc_ids)))
    return or_(*conds)


async def _count_docs(db: AsyncSession, cond: ColumnElement[bool]) -> int:
    stmt = select(func.count()).select_from(KnowledgeDoc).where(cond)
    return int((await db.execute(stmt)).scalar_one())


async def scope_preview(db: AsyncSession, scope: KnowledgeScope) -> dict:
    """编辑器实时预览：命中多少篇、哪些目录/文档已不存在。"""

    cond = scope_condition(scope)
    doc_count = 0 if cond is None else await _count_docs(db, cond)

    matched_paths: list[dict] = []
    unknown_paths: list[str] = []
    for path in scope.paths:
        count = await _count_docs(db, _path_condition(path))
        if count == 0:
            unknown_paths.append(path)
            continue
        matched_paths.append({"path": path, "count": count})

    known_doc_ids: set[str] = set()
    if len(scope.doc_ids) > 0:
        rows = await db.execute(
            select(KnowledgeDoc.id).where(KnowledgeDoc.id.in_(list(scope.doc_ids)))
        )
        known_doc_ids = {row[0] for row in rows.all()}
    unknown_doc_ids = [doc_id for doc_id in scope.doc_ids if doc_id not in known_doc_ids]

    return {
        "doc_count": doc_count,
        "matched_paths": matched_paths,
        "unknown_paths": unknown_paths,
        "unknown_doc_ids": unknown_doc_ids,
    }


async def validate_scope(db: AsyncSession, scope: KnowledgeScope) -> None:
    """保存前校验：不存在的目录/文档一次报全，避免用户反复试。"""

    if scope.is_empty:
        return
    preview = await scope_preview(db, scope)
    problems: list[str] = []
    unknown_paths = preview["unknown_paths"]
    unknown_doc_ids = preview["unknown_doc_ids"]
    if len(unknown_paths) > 0:
        problems.append("目录不存在或没有文档: " + "、".join(unknown_paths))
    if len(unknown_doc_ids) > 0:
        problems.append("文档不存在: " + "、".join(unknown_doc_ids))
    if len(problems) > 0:
        raise ValueError("知识范围无效：" + "；".join(problems))
