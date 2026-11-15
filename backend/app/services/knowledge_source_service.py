"""外部知识源配置：校验、CRUD、同步状态与文档归属统计。

真正的抓取/克隆在 knowledge_sync 中执行；本模块不发起网络请求，
保证管理接口在创建或编辑来源时不会被外部站点拖慢。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeDoc
from app.models.knowledge_source import KnowledgeSource
from app.services import knowledge_service

SYNC_IDLE = "idle"
SYNC_SYNCING = "syncing"
SYNC_OK = "ok"
SYNC_ERROR = "error"

KIND_URL = "url"
KIND_GIT = "git"
SOURCE_KINDS = frozenset({KIND_URL, KIND_GIT})

_BRANCH_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._/-]*$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def validate_name(name: str) -> str:
    value = name.strip()
    if value == "":
        raise ValueError("来源名称不能为空")
    if len(value) > 128:
        raise ValueError("来源名称最长 128 个字符")
    return value


def validate_kind(kind: str) -> str:
    value = kind.strip().lower()
    if value not in SOURCE_KINDS:
        raise ValueError("来源类型必须是 url 或 git")
    return value


def validate_location(kind: str, location: str) -> str:
    value = location.strip()
    if value == "":
        raise ValueError("来源地址不能为空")
    if len(value) > 2048:
        raise ValueError("来源地址最长 2048 个字符")
    if _CONTROL_RE.search(value) or any(char.isspace() for char in value):
        raise ValueError("来源地址不能包含空白或控制字符")
    if kind == KIND_URL:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or parsed.netloc == "":
            raise ValueError("URL 来源必须是 http(s) 地址")
        return value
    if value.startswith("git@"):
        return value
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https", "ssh", "file"):
        raise ValueError("Git 来源支持 http(s) / ssh / file 地址")
    if parsed.scheme in ("http", "https", "ssh") and parsed.netloc == "":
        raise ValueError("Git 来源地址缺少主机名")
    if parsed.scheme == "file" and parsed.path.strip() == "":
        raise ValueError("Git file 地址缺少路径")
    return value


def validate_branch(branch: str) -> str:
    value = branch.strip()
    if value == "":
        return ""
    if len(value) > 128 or not _BRANCH_RE.match(value) or ".." in value:
        raise ValueError("分支名包含非法字符")
    return value


def validate_path_prefix(prefix: str) -> str:
    value = prefix.strip().replace("\\", "/").strip("/")
    if value == "":
        return ""
    if len(value) > 512:
        raise ValueError("仓库子目录最长 512 个字符")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("仓库子目录必须是相对路径，不能包含 ..")
    if _CONTROL_RE.search(value):
        raise ValueError("仓库子目录不能包含控制字符")
    return value


def validate_knowledge_path(path: str, fallback_name: str) -> str:
    value = path.strip().replace("\\", "/").strip("/")
    if value == "":
        value = f"外部源/{fallback_name.strip()}".strip("/")
    if len(value) > 512:
        raise ValueError("知识库目录最长 512 个字符")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("知识库目录必须是相对路径，不能包含 ..")
    if _CONTROL_RE.search(value):
        raise ValueError("知识库目录不能包含控制字符")
    return value


def source_to_wire(source: KnowledgeSource, document_count: int) -> dict[str, Any]:
    return {
        "id": source.id,
        "name": source.name,
        "kind": source.kind,
        "location": source.location,
        "branch": source.branch,
        "path_prefix": source.path_prefix,
        "knowledge_path": source.knowledge_path,
        "enabled": source.enabled,
        "sync_status": source.sync_status,
        "last_error": source.last_error,
        "last_synced_at": (
            source.last_synced_at.isoformat() if source.last_synced_at else None
        ),
        "last_duration_ms": source.last_duration_ms,
        "document_count": document_count,
        "created_at": source.created_at.isoformat() if source.created_at else None,
        "updated_at": source.updated_at.isoformat() if source.updated_at else None,
    }


async def _ensure_name_free(
    db: AsyncSession, name: str, exclude_id: str | None = None
) -> None:
    row = await db.scalar(select(KnowledgeSource).where(KnowledgeSource.name == name))
    if row is not None and row.id != exclude_id:
        raise ValueError(f"来源名称已存在: {name}")


async def get_source(db: AsyncSession, source_id: str) -> KnowledgeSource:
    row = await db.get(KnowledgeSource, source_id)
    if row is None:
        raise LookupError(f"外部源不存在: {source_id}")
    return row


async def _doc_counts(db: AsyncSession, source_ids: list[str]) -> dict[str, int]:
    if not source_ids:
        return {}
    rows = (
        await db.execute(
            select(KnowledgeDoc.external_source_id, func.count(KnowledgeDoc.id))
            .where(KnowledgeDoc.external_source_id.in_(source_ids))
            .group_by(KnowledgeDoc.external_source_id)
        )
    ).all()
    return {str(source_id): int(count) for source_id, count in rows}


async def list_sources(
    db: AsyncSession, *, q: str | None = None, kind: str | None = None
) -> list[dict[str, Any]]:
    stmt = select(KnowledgeSource)
    if q is not None and q.strip() != "":
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(KnowledgeSource.name.ilike(pattern))
    if kind is not None:
        stmt = stmt.where(KnowledgeSource.kind == validate_kind(kind))
    rows = list(
        (
            await db.scalars(
                stmt.order_by(
                    KnowledgeSource.updated_at.desc(), KnowledgeSource.id.desc()
                )
            )
        ).all()
    )
    counts = await _doc_counts(db, [row.id for row in rows])
    return [source_to_wire(row, counts.get(row.id, 0)) for row in rows]


async def create_source(
    db: AsyncSession,
    *,
    name: str,
    kind: str,
    location: str,
    branch: str,
    path_prefix: str,
    knowledge_path: str,
    enabled: bool,
    created_by: int | None,
) -> KnowledgeSource:
    clean_name = validate_name(name)
    clean_kind = validate_kind(kind)
    clean_location = validate_location(clean_kind, location)
    clean_branch = validate_branch(branch)
    clean_prefix = validate_path_prefix(path_prefix)
    clean_path = validate_knowledge_path(knowledge_path, clean_name)
    if clean_kind == KIND_URL:
        # URL 源没有分支/子目录概念，静默归零避免前端切换类型后残留脏字段
        clean_branch = ""
        clean_prefix = ""
    await _ensure_name_free(db, clean_name)
    row = KnowledgeSource(
        id=str(uuid4()),
        name=clean_name,
        kind=clean_kind,
        location=clean_location,
        branch=clean_branch,
        path_prefix=clean_prefix,
        knowledge_path=clean_path,
        enabled=enabled,
        sync_status=SYNC_IDLE,
        created_by=created_by,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def update_source(
    db: AsyncSession,
    source_id: str,
    *,
    name: str | None,
    kind: str | None,
    location: str | None,
    branch: str | None,
    path_prefix: str | None,
    knowledge_path: str | None,
    enabled: bool | None,
) -> KnowledgeSource:
    row = await get_source(db, source_id)
    if row.sync_status == SYNC_SYNCING:
        # 同步中的快照仍在写状态与 ETag，此时改配置会把结果写到错误的目标上
        raise ValueError("外部源正在同步，请等待完成后再修改")
    if kind is not None and validate_kind(kind) != row.kind:
        raise ValueError("来源类型不可修改；请新建一个来源")
    if name is not None:
        clean_name = validate_name(name)
        if clean_name != row.name:
            await _ensure_name_free(db, clean_name, exclude_id=row.id)
            row.name = clean_name
    if location is not None:
        clean_location = validate_location(row.kind, location)
        if clean_location != row.location:
            row.location = clean_location
            # 地址变了，旧站点的 ETag/Last-Modified 不能再用
            row.etag = ""
            row.last_modified = ""
            row.sync_status = SYNC_IDLE
            row.last_error = ""
            row.last_synced_at = None
            row.last_duration_ms = None
    if branch is not None:
        row.branch = validate_branch(branch) if row.kind == KIND_GIT else ""
    if path_prefix is not None:
        row.path_prefix = (
            validate_path_prefix(path_prefix) if row.kind == KIND_GIT else ""
        )
    if knowledge_path is not None:
        row.knowledge_path = validate_knowledge_path(knowledge_path, row.name)
    if enabled is not None:
        if row.enabled != enabled:
            row.enabled = enabled
            # 来源启停立即作用于已同步文档，不必等下一次同步
            await db.execute(
                update(KnowledgeDoc)
                .where(KnowledgeDoc.external_source_id == row.id)
                .values(enabled=enabled)
            )
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)
    return row


async def delete_source(db: AsyncSession, source_id: str) -> None:
    row = await get_source(db, source_id)
    if row.sync_status == SYNC_SYNCING:
        raise ValueError("外部源正在同步，请等待完成后再删除")
    doc_ids = list(
        (
            await db.scalars(
                select(KnowledgeDoc.id).where(
                    KnowledgeDoc.external_source_id == source_id
                )
            )
        ).all()
    )
    # 逐篇走知识库删除流程，确保切块向量与文档一起清理
    for doc_id in doc_ids:
        await knowledge_service.delete_doc(db, doc_id)
    await db.delete(row)
    await db.commit()


async def mark_syncing(db: AsyncSession, source_id: str) -> KnowledgeSource:
    row = await get_source(db, source_id)
    if row.sync_status == SYNC_SYNCING:
        raise ValueError("该外部源正在同步")
    row.sync_status = SYNC_SYNCING
    row.last_error = ""
    await db.commit()
    await db.refresh(row)
    return row


async def mark_sync_ok(
    db: AsyncSession,
    source_id: str,
    *,
    duration_ms: int,
    etag: str = "",
    last_modified: str = "",
) -> None:
    row = await get_source(db, source_id)
    row.sync_status = SYNC_OK
    row.last_error = ""
    row.last_synced_at = datetime.now(timezone.utc)
    row.last_duration_ms = duration_ms
    # 无条件覆盖：站点取消 ETag/Last-Modified 时不能继续带旧条件头
    row.etag = etag
    row.last_modified = last_modified
    await db.commit()


async def mark_sync_error(
    db: AsyncSession, source_id: str, *, error: str, duration_ms: int
) -> None:
    row = await get_source(db, source_id)
    row.sync_status = SYNC_ERROR
    row.last_error = error[:2000]
    row.last_duration_ms = duration_ms
    await db.commit()


async def remove_stale_docs(
    db: AsyncSession, source_id: str, keep_keys: set[str]
) -> int:
    """删除来源中已不存在的文档；返回删除数量。"""

    rows = list(
        (
            await db.scalars(
                select(KnowledgeDoc).where(
                    KnowledgeDoc.external_source_id == source_id
                )
            )
        ).all()
    )
    removed = 0
    for doc in rows:
        if (doc.external_key or "") in keep_keys:
            continue
        await knowledge_service.delete_doc(db, doc.id)
        removed += 1
    return removed


async def recover_interrupted_syncs(db: AsyncSession) -> int:
    """进程重启后把卡在 syncing 的来源标为失败，避免按钮永久禁用。"""

    rows = list(
        (
            await db.scalars(
                select(KnowledgeSource).where(
                    KnowledgeSource.sync_status == SYNC_SYNCING
                )
            )
        ).all()
    )
    for row in rows:
        row.sync_status = SYNC_ERROR
        row.last_error = "服务重启导致同步中断，请重新同步"
    if rows:
        await db.commit()
    return len(rows)
