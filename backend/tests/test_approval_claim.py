"""审批认领的并发契约：原子认领保证一张单只被决一次。"""

from __future__ import annotations

import uuid

import pytest

from app.models_orm.approval import Approval
from app.services import approval_service
from app.services.approval_service import _claim_pending_approval


def _approval_row(session_id: str) -> Approval:
    return Approval(
        id=f"apr_{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        confirmation_fc_id=f"fc_{uuid.uuid4().hex[:12]}",
        tool_call_id=f"call_{uuid.uuid4().hex[:8]}",
        tool_name="run_workflow",
        kind="tool",
        title="测试审批",
        summary="单元测试",
        payload="{}",
        hint="",
        status="pending",
    )


def _new_sessionmaker():
    """每个用例独立建 engine：模块级 engine 会跨事件循环复用连接而报错。"""

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_claim_pending_approval_is_one_shot() -> None:
    """第一次认领成功，第二次必须失败：并发决策不会执行两次工具。"""

    engine, sessionmaker = _new_sessionmaker()
    try:
        async with sessionmaker() as db:
            row = _approval_row(session_id=f"s_{uuid.uuid4().hex[:8]}")
            db.add(row)
            await db.commit()

            first = await _claim_pending_approval(
                db, approval_id=row.id, confirmed=True, reason="ok"
            )
            assert first is True
            await db.commit()

            second = await _claim_pending_approval(
                db, approval_id=row.id, confirmed=True, reason="ok"
            )
            assert second is False

            await db.refresh(row)
            assert row.status == "approved"
            assert row.decision_reason == "ok"
            await db.delete(row)
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_claim_rejects_already_decided() -> None:
    """已经决过的单不可再认领：拒绝态同样返回 False。"""

    engine, sessionmaker = _new_sessionmaker()
    try:
        async with sessionmaker() as db:
            row = _approval_row(session_id=f"s_{uuid.uuid4().hex[:8]}")
            row.status = "rejected"
            db.add(row)
            await db.commit()

            claimed = await _claim_pending_approval(
                db, approval_id=row.id, confirmed=False, reason="再拒一次"
            )
            assert claimed is False
            await db.delete(row)
            await db.commit()
    finally:
        await engine.dispose()


def test_approval_status_contract_is_shared() -> None:
    """状态集合与前端 lib/approvalStatus 的口径一致（六个状态、顺序无关）。"""

    assert set(approval_service.APPROVAL_STATUSES) == {
        "pending",
        "approved",
        "executed",
        "rejected",
        "failed",
        "expired",
    }
