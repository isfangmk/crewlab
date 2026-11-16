"""助理配置版本快照与回滚。

每次创建/更新助理都把整份配置落成快照；回滚 = 读旧快照覆盖当前配置，
再落一版记录来源版本，保证回滚本身也可回滚。
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_version import UserAgentVersion

logger = logging.getLogger(__name__)

# 变更摘要长度上限：与模型列 String(256) 对齐，超长显式报错
MAX_CHANGE_SUMMARY_CHARS = 256


def _validate_change_summary(value: str) -> str:
    if len(value) > MAX_CHANGE_SUMMARY_CHARS:
        raise ValueError(f"change_summary 超过 {MAX_CHANGE_SUMMARY_CHARS} 字符")
    return value


async def snapshot_agent_version(
    db: AsyncSession,
    agent_name: str,
    snapshot: object,
    *,
    created_by: int | None,
    change_summary: str = "",
    restored_from_version: int | None = None,
) -> int:
    """落一份配置快照，返回新版本号（单个助理内从 1 开始递增）。"""

    clean_summary = _validate_change_summary(change_summary)
    if not isinstance(snapshot, dict):
        raise TypeError("snapshot 必须是对象")
    last_version = await db.scalar(
        select(func.max(UserAgentVersion.version)).where(
            UserAgentVersion.agent_name == agent_name
        )
    )
    version = int(last_version or 0) + 1
    db.add(
        UserAgentVersion(
            agent_name=agent_name,
            version=version,
            snapshot_json=json.dumps(snapshot, ensure_ascii=False),
            change_summary=clean_summary,
            created_by=created_by,
            restored_from_version=restored_from_version,
        )
    )
    await db.commit()
    logger.info(
        "助理配置快照 name=%s version=%s restored_from=%s",
        agent_name,
        version,
        restored_from_version,
    )
    return version


async def list_versions(db: AsyncSession, agent_name: str) -> list[dict]:
    """版本列表（新版本在前）；不含快照正文。"""

    rows = list(
        (
            await db.scalars(
                select(UserAgentVersion)
                .where(UserAgentVersion.agent_name == agent_name)
                .order_by(UserAgentVersion.version.desc())
            )
        ).all()
    )
    return [
        {
            "version": row.version,
            "change_summary": row.change_summary,
            "created_by": row.created_by,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "restored_from_version": row.restored_from_version,
        }
        for row in rows
    ]


async def get_version(db: AsyncSession, agent_name: str, version: int) -> dict:
    """单个版本详情，含完整配置快照。"""

    row = await db.scalar(
        select(UserAgentVersion).where(
            UserAgentVersion.agent_name == agent_name,
            UserAgentVersion.version == version,
        )
    )
    if row is None:
        raise LookupError(f"助理版本不存在: {agent_name} v{version}")
    snapshot = json.loads(row.snapshot_json)
    if not isinstance(snapshot, dict):
        raise TypeError(f"快照结构异常: {agent_name} v{version}")
    return {
        "version": row.version,
        "change_summary": row.change_summary,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "restored_from_version": row.restored_from_version,
        "snapshot": snapshot,
    }


async def current_version(db: AsyncSession, agent_name: str) -> int:
    """当前最新版本号；无快照时返回 0（内置助理或历史数据）。"""

    value = await db.scalar(
        select(func.max(UserAgentVersion.version)).where(
            UserAgentVersion.agent_name == agent_name
        )
    )
    return int(value or 0)


async def list_current_meta(db: AsyncSession) -> dict[str, dict]:
    """批量取每个助理的最新版本号与更新时间（列表页避免 N+1 查询）。"""

    rows = list(
        (
            await db.execute(
                select(
                    UserAgentVersion.agent_name,
                    func.max(UserAgentVersion.version),
                    func.max(UserAgentVersion.created_at),
                ).group_by(UserAgentVersion.agent_name)
            )
        ).all()
    )
    return {
        row[0]: {
            "version": int(row[1]),
            "updated_at": row[2].isoformat() if row[2] is not None else None,
        }
        for row in rows
    }
