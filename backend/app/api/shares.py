"""分享链接管理：统一会话分享与评测分享的列表与撤销。

安全口径：库里只存 token 哈希，原链接无法回显——要链接就调创建端点
再生成一条新分享（旧链接可单独撤销）。撤销 = 删行（审计留历史），
撤销后 resolve_share 立即失效；过期链接读取时本来就会拒。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth, require_not_viewer
from app.db import get_db
from app.models.chat import ChatSession
from app.models.eval import EvalRun
from app.models.eval_share import EvalShareToken
from app.models.share import ShareToken
from app.models.user import User
from app.services.observability.audit_service import write_audit
from app.services.platform.auth_service import AuthUser

logger = logging.getLogger(__name__)
router = APIRouter(tags=["shares"])


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符；不转义会让用户输入的 % 变成全表匹配。"""

    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@router.get("/shares")
async def list_shares(
    kind: str = Query(default="all", pattern="^(all|session|eval)$"),
    status: str = Query(default="all", pattern="^(all|active|expired)$"),
    q: str = Query(default=""),
    sort_by: str = Query(default="created_at", pattern="^(created_at|expires_at)$"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    """分享链接列表（会话 + 评测统一出参）。

    非 admin 只看自己创建的；EvalShareToken.created_by 可空的历史行仅 admin 可见。
    两组令牌量级很小，SQL 各自过滤后在服务端合并排序分页。
    """

    now = datetime.now(timezone.utc)
    keyword = q.strip()
    rows: list[dict] = []

    if kind in ("all", "session"):
        stmt = select(ShareToken, ChatSession.title).join(
            ChatSession, ChatSession.id == ShareToken.session_id
        )
        if keyword != "":
            stmt = stmt.where(
                ChatSession.title.ilike(f"%{_escape_like(keyword)}%", escape="\\")
            )
        if status == "active":
            stmt = stmt.where(ShareToken.expires_at > now)
        elif status == "expired":
            stmt = stmt.where(ShareToken.expires_at <= now)
        if user.role != "admin":
            stmt = stmt.where(ShareToken.created_by == user.id)
        for token, title in (await db.execute(stmt)).all():
            rows.append(
                {
                    "id": token.id,
                    "kind": "session",
                    "target_id": token.session_id,
                    "title": title,
                    "created_by": token.created_by,
                    "created_at": token.created_at,
                    "expires_at": token.expires_at,
                }
            )

    if kind in ("all", "eval"):
        stmt = select(EvalShareToken, EvalRun.dataset_name).join(
            EvalRun, EvalRun.run_uid == EvalShareToken.run_uid
        )
        if keyword != "":
            stmt = stmt.where(
                EvalRun.dataset_name.ilike(f"%{_escape_like(keyword)}%", escape="\\")
            )
        if status == "active":
            stmt = stmt.where(EvalShareToken.expires_at > now)
        elif status == "expired":
            stmt = stmt.where(EvalShareToken.expires_at <= now)
        if user.role != "admin":
            stmt = stmt.where(EvalShareToken.created_by == user.id)
        for token, title in (await db.execute(stmt)).all():
            rows.append(
                {
                    "id": token.id,
                    "kind": "eval",
                    "target_id": token.run_uid,
                    "title": title,
                    "created_by": token.created_by,
                    "created_at": token.created_at,
                    "expires_at": token.expires_at,
                }
            )

    # 创建人展示用邮箱；created_by 为空的历史行不回填
    creator_ids = {r["created_by"] for r in rows if r["created_by"] is not None}
    emails: dict[int, str] = {}
    if creator_ids:
        for u in (await db.execute(select(User).where(User.id.in_(creator_ids)))).scalars():
            emails[u.id] = u.email
    for r in rows:
        r["creator"] = emails.get(r["created_by"]) if r["created_by"] is not None else None
        r["expired"] = r["expires_at"] <= now

    # 只开时间列排序；同刻按 id 兜底保证顺序稳定
    rows.sort(
        key=lambda r: (r[sort_by], r["kind"], r["id"]),
        reverse=(sort_dir == "desc"),
    )
    total = len(rows)
    page = [
        {
            **r,
            "created_at": _iso(r["created_at"]),
            "expires_at": _iso(r["expires_at"]),
        }
        for r in rows[offset : offset + limit]
    ]
    return {"items": page, "total": total}


@router.delete("/shares/{kind}/{share_id}")
async def revoke_share(
    kind: str,
    share_id: int,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """撤销分享链接（删行）；仅创建者或管理员可撤。"""

    if kind == "session":
        model = ShareToken
        action = "session.share_revoke"
        resource_type = "chat_session"
    elif kind == "eval":
        model = EvalShareToken
        action = "eval.share_revoke"
        resource_type = "eval_run"
    else:
        raise HTTPException(status_code=400, detail="kind 只能是 session 或 eval")

    row = await db.get(model, share_id)
    if row is None:
        raise HTTPException(status_code=404, detail="分享不存在")
    if user.role != "admin" and row.created_by != user.id:
        raise HTTPException(status_code=403, detail="仅创建者或管理员可撤销")

    # 两种分享行字段不同：按行类型取各自的资源 id
    target_id = row.session_id if isinstance(row, ShareToken) else row.run_uid
    await db.delete(row)
    await db.commit()
    await write_audit(
        actor_user_id=user.id,
        action=action,
        resource_type=resource_type,
        resource_id=target_id,
        detail={"share_id": share_id},
    )
    logger.info("撤销分享 kind=%s share_id=%s target=%s by=%s", kind, share_id, target_id, user.id)
    return {"ok": True}
