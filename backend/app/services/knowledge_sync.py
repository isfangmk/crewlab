"""外部知识源同步执行器：URL 单页抓取与 Git 仓库文档扫描。

同步在后台任务中执行，管理接口立即返回；每篇文档按内容哈希跳过未变化项，
因此重复同步只重新嵌入真正改过的内容。同步完成后清理来源中已删除的文档。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models_orm.knowledge import KnowledgeDoc
from app.services import knowledge_service, knowledge_source_service
from app.services.audit_service import write_audit

logger = logging.getLogger(__name__)

_tasks: set[asyncio.Task[None]] = set()

MAX_URL_BYTES = 2 * 1024 * 1024
MAX_GIT_FILE_BYTES = 2 * 1024 * 1024
MAX_GIT_DOCS = 200
GIT_TIMEOUT_SECONDS = 180
GIT_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".html", ".htm"})
_SKIP_TAGS = frozenset(
    {"script", "style", "noscript", "svg", "nav", "footer", "aside", "form"}
)
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "br",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
        "pre",
        "blockquote",
        "table",
        "ul",
        "ol",
    }
)


@dataclass(frozen=True)
class SourceSnapshot:
    id: str
    name: str
    kind: str
    location: str
    branch: str
    path_prefix: str
    knowledge_path: str
    etag: str
    last_modified: str
    enabled: bool
    created_by: int | None


@dataclass(frozen=True)
class FetchedDoc:
    key: str
    title: str
    content: str
    url: str
    path: str
    content_hash: str


@dataclass(frozen=True)
class FetchResult:
    docs: list[FetchedDoc]
    etag: str = ""
    last_modified: str = ""
    not_modified: bool = False


@dataclass(frozen=True)
class SyncStats:
    docs: int
    changed: int
    skipped: int
    removed: int
    not_modified: bool


class _HtmlTextExtractor(HTMLParser):
    """只取正文与 title；跳过脚本、导航等噪声，块级标签保留换行。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if name in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if name == "title":
            self._in_title = True
            return
        if name in _BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.lower() == "br":
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in _SKIP_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if name == "title":
            self._in_title = False
            return
        if name in _BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._skip_depth == 0:
            self.text_parts.append(data)


def _normalize_text(value: str) -> str:
    lines: list[str] = []
    for raw in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = " ".join(raw.replace("\t", " ").split())
        if line != "":
            lines.append(line)
    return "\n".join(lines)


def _html_to_text(raw: str) -> tuple[str, str]:
    parser = _HtmlTextExtractor()
    parser.feed(raw)
    parser.close()
    title = _normalize_text(" ".join(parser.title_parts))
    body = _normalize_text("".join(parser.text_parts))
    return title, body


def _fallback_title(key: str) -> str:
    name = key.rsplit("/", 1)[-1].rsplit("?", 1)[0]
    stem = Path(name).stem if name else ""
    return stem or key


def _extract_document(
    raw: str, *, key: str, content_type: str = ""
) -> tuple[str, str]:
    """把 HTML / Markdown / 纯文本统一成 (标题, 正文)。"""

    lower_key = key.lower()
    lowered_type = content_type.lower()
    stripped = raw.lstrip()
    is_html = (
        "html" in lowered_type
        or lower_key.endswith((".html", ".htm"))
        or stripped.startswith(("<!doctype html", "<html"))
    )
    if is_html:
        title, body = _html_to_text(raw)
        if body == "":
            raise ValueError("页面没有可索引的正文")
        return title[:512] or _fallback_title(key)[:512], body

    body = _normalize_text(raw)
    if body == "":
        raise ValueError("文档内容为空")
    title = ""
    for line in body.split("\n")[:20]:
        if line.startswith("# "):
            title = line[2:].strip()
            break
    return title[:512] or _fallback_title(key)[:512], body


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _safe_key(key: str) -> str:
    """external_key 列宽 1024；超长地址用哈希保证稳定且不截断碰撞。"""

    if len(key) <= 1024:
        return key
    return f"sha256:{hashlib.sha256(key.encode('utf-8')).hexdigest()}"


def _error_text(exc: BaseException) -> str:
    message = f"{type(exc).__name__}: {exc}".replace("\r", " ").replace("\n", " ")
    return message[:1000]


def start_sync(source_id: str) -> None:
    task = asyncio.create_task(_sync_detached(source_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _load_snapshot(source_id: str) -> SourceSnapshot:
    async with SessionLocal() as db:
        row = await knowledge_source_service.get_source(db, source_id)
        return SourceSnapshot(
            id=row.id,
            name=row.name,
            kind=row.kind,
            location=row.location,
            branch=row.branch,
            path_prefix=row.path_prefix,
            knowledge_path=row.knowledge_path,
            etag=row.etag,
            last_modified=row.last_modified,
            enabled=row.enabled,
            created_by=row.created_by,
        )


async def _fetch_url(source: SourceSnapshot) -> FetchResult:
    headers = {"User-Agent": "Ongrid-KnowledgeSync/1.0"}
    if source.etag != "":
        headers["If-None-Match"] = source.etag
    if source.last_modified != "":
        headers["If-Modified-Since"] = source.last_modified
    try:
        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=True, headers=headers
        ) as client:
            response = await client.get(source.location)
    except httpx.HTTPError as exc:
        raise ValueError(f"抓取 URL 失败: {exc}") from exc
    if response.status_code == 304:
        return FetchResult(
            docs=[],
            etag=source.etag,
            last_modified=source.last_modified,
            not_modified=True,
        )
    if response.status_code >= 400:
        raise ValueError(f"抓取 URL 失败: HTTP {response.status_code}")
    raw = response.content
    if len(raw) > MAX_URL_BYTES:
        raise ValueError(f"页面超过 {MAX_URL_BYTES // 1024 // 1024}MB，拒绝入库")
    content_type = response.headers.get("content-type", "")
    if content_type != "" and not any(
        token in content_type.lower()
        for token in ("text/", "html", "markdown", "xml")
    ):
        raise ValueError(f"不支持的页面类型: {content_type}")
    try:
        text = response.text
    except UnicodeDecodeError as exc:
        raise ValueError(f"页面编码无法识别: {exc}") from exc
    final_url = str(response.url)
    title, body = _extract_document(text, key=final_url, content_type=content_type)
    doc = FetchedDoc(
        key=_safe_key(source.location),
        title=title,
        content=body,
        url=final_url,
        path=source.knowledge_path,
        content_hash=_content_hash(body),
    )
    return FetchResult(
        docs=[doc],
        etag=response.headers.get("etag", ""),
        last_modified=response.headers.get("last-modified", ""),
    )


async def _run_git(args: list[str], *, cwd: Path | None = None) -> str:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("未找到 git 命令，无法同步 Git 来源")
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    proc = await asyncio.create_subprocess_exec(
        git,
        *args,
        cwd=str(cwd) if cwd is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=GIT_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise ValueError(f"git 操作超时（{GIT_TIMEOUT_SECONDS}s）") from exc
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"git 操作失败: {detail[-500:]}")
    return stdout.decode("utf-8", errors="replace")


def _scan_git_docs(repo_dir: Path, source: SourceSnapshot) -> list[FetchedDoc]:
    root = repo_dir
    if source.path_prefix != "":
        root = (repo_dir / source.path_prefix).resolve()
        try:
            root.relative_to(repo_dir.resolve())
        except ValueError as exc:
            raise ValueError("仓库子目录越界") from exc
    if not root.is_dir():
        raise ValueError(f"仓库子目录不存在: {source.path_prefix}")
    repo_root = repo_dir.resolve()
    docs: list[FetchedDoc] = []
    for item in sorted(root.rglob("*")):
        if item.is_symlink() or not item.is_file():
            continue
        if item.suffix.lower() not in GIT_SUFFIXES:
            continue
        try:
            resolved = item.resolve()
            resolved.relative_to(repo_root)
        except ValueError:
            continue
        if any(part.startswith(".") for part in item.relative_to(root).parts):
            continue
        size = item.stat().st_size
        if size > MAX_GIT_FILE_BYTES:
            logger.warning("跳过超大仓库文档 path=%s bytes=%s", item, size)
            continue
        raw = item.read_bytes().decode("utf-8", errors="replace")
        repo_rel = item.relative_to(repo_dir).as_posix()
        relative_parent = item.parent.relative_to(root).as_posix()
        doc_path = source.knowledge_path
        if relative_parent != ".":
            doc_path = f"{doc_path}/{relative_parent}"
        title, body = _extract_document(
            raw,
            key=item.name,
            content_type="text/html" if item.suffix.lower() in (".html", ".htm") else "",
        )
        docs.append(
            FetchedDoc(
                key=_safe_key(repo_rel),
                title=title,
                content=body,
                url=source.location,
                path=doc_path,
                content_hash=_content_hash(body),
            )
        )
        if len(docs) > MAX_GIT_DOCS:
            raise ValueError(
                f"仓库文档超过 {MAX_GIT_DOCS} 个，请用仓库子目录收窄同步范围"
            )
    if len(docs) == 0:
        raise ValueError("仓库范围内没有 .md / .txt / .html 文档")
    return docs


async def _fetch_git(source: SourceSnapshot) -> FetchResult:
    temp_dir = Path(tempfile.mkdtemp(prefix="ongrid-knowledge-git-"))
    repo_dir = temp_dir / "repo"
    try:
        args = ["clone", "--depth", "1", "--single-branch", "--no-tags"]
        if source.branch != "":
            args.extend(["--branch", source.branch])
        args.extend(["--", source.location, str(repo_dir)])
        await _run_git(args)
        docs = _scan_git_docs(repo_dir, source)
        return FetchResult(docs=docs)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


async def _apply_docs(source: SourceSnapshot, docs: list[FetchedDoc]) -> SyncStats:
    changed = 0
    skipped = 0
    async with SessionLocal() as db:
        for doc in docs:
            _, did_change = await knowledge_service.upsert_external_doc(
                db,
                source_id=source.id,
                source_type=source.kind,
                external_key=doc.key,
                external_hash=doc.content_hash,
                title=doc.title,
                content=doc.content,
                url=doc.url,
                path=doc.path,
                enabled=source.enabled,
            )
            if did_change:
                changed += 1
            else:
                skipped += 1
        removed = await knowledge_source_service.remove_stale_docs(
            db, source.id, {doc.key for doc in docs}
        )
        return SyncStats(
            docs=len(docs),
            changed=changed,
            skipped=skipped,
            removed=removed,
            not_modified=False,
        )


async def _doc_count(source_id: str) -> int:
    async with SessionLocal() as db:
        count = await db.scalar(
            select(func.count(KnowledgeDoc.id)).where(
                KnowledgeDoc.external_source_id == source_id
            )
        )
        return int(count or 0)


async def _write_finish_audit(
    source: SourceSnapshot,
    *,
    status: str,
    stats: SyncStats | None,
    duration_ms: int,
    error: str = "",
) -> None:
    detail: dict[str, Any] = {
        "kind": source.kind,
        "name": source.name,
        "status": status,
        "duration_ms": duration_ms,
        "document_count": await _doc_count(source.id),
    }
    if stats is not None:
        detail.update(
            {
                "changed": stats.changed,
                "skipped": stats.skipped,
                "removed": stats.removed,
                "not_modified": stats.not_modified,
            }
        )
    if error != "":
        detail["error"] = error[:500]
    await write_audit(
        actor_user_id=source.created_by,
        action="knowledge.source_sync_finish",
        resource_type="knowledge_source",
        resource_id=source.id,
        detail=detail,
    )


async def _sync_detached(source_id: str) -> None:
    started = time.monotonic()
    snapshot: SourceSnapshot | None = None
    stats: SyncStats | None = None
    try:
        snapshot = await _load_snapshot(source_id)
        if snapshot.kind == knowledge_source_service.KIND_URL:
            fetched = await _fetch_url(snapshot)
        else:
            fetched = await _fetch_git(snapshot)
        if fetched.not_modified:
            stats = SyncStats(
                docs=0, changed=0, skipped=0, removed=0, not_modified=True
            )
        else:
            stats = await _apply_docs(snapshot, fetched.docs)
        duration_ms = int((time.monotonic() - started) * 1000)
        async with SessionLocal() as db:
            await knowledge_source_service.mark_sync_ok(
                db,
                source_id,
                duration_ms=duration_ms,
                etag=fetched.etag,
                last_modified=fetched.last_modified,
            )
        await _write_finish_audit(
            snapshot, status="ok", stats=stats, duration_ms=duration_ms
        )
        logger.info(
            "外部源同步完成 source=%s kind=%s docs=%s changed=%s removed=%s",
            source_id,
            snapshot.kind,
            stats.docs,
            stats.changed,
            stats.removed,
        )
    except Exception as exc:
        error = _error_text(exc)
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.exception("外部源同步失败 source=%s", source_id)
        if snapshot is not None:
            try:
                async with SessionLocal() as db:
                    await knowledge_source_service.mark_sync_error(
                        db, source_id, error=error, duration_ms=duration_ms
                    )
                await _write_finish_audit(
                    snapshot,
                    status="error",
                    stats=stats,
                    duration_ms=duration_ms,
                    error=error,
                )
            except Exception:
                logger.exception("记录外部源同步失败状态时出错 source=%s", source_id)
