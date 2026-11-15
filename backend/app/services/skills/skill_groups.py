"""技能分组：技能像内置工具一样按组归类。

分组是固定枚举（key → 中英文标题），不是自由文本：
技能目录页的筛选条与表格分组都按它渲染。
技能不再有独立的自由文本分类字段——分组就是唯一的归类维度。
"""

from __future__ import annotations

from typing import Any

# 技能分组 → 中英文标题。顺序即展示顺序，与内置工具分组保持同一套语义。
SKILL_GROUP_TITLES: dict[str, dict[str, str]] = {
    "observe": {"title": "观测", "title_en": "Observability"},
    "device": {"title": "设备管理", "title_en": "Devices"},
    "fleet": {"title": "集群与拓扑", "title_en": "Fleet & topology"},
    "incident": {"title": "告警与事件", "title_en": "Incidents & alerts"},
    "knowledge": {"title": "知识", "title_en": "Knowledge"},
    "messaging": {"title": "消息", "title_en": "Messaging"},
    "cloud": {"title": "云端管理", "title_en": "Cloud"},
    "chart": {"title": "可视化", "title_en": "Visualization"},
    "other": {"title": "其他", "title_en": "Other"},
}

# 允许落库的分组 key（含 other：其他是合法选择，不是兜底默认值）
SKILL_GROUP_KEYS: frozenset[str] = frozenset(SKILL_GROUP_TITLES.keys())

# 展示顺序固定，前端不再自己排。
SKILL_GROUP_ORDER: tuple[str, ...] = tuple(SKILL_GROUP_TITLES.keys())


def validate_skill_group(group: str) -> str:
    """校验分组 key；非法值显式失败，不静默落到 other。"""

    if group not in SKILL_GROUP_KEYS:
        raise ValueError(
            f"技能分组必须是 {'/'.join(SKILL_GROUP_ORDER)} 之一: {group!r}"
        )
    return group


def skill_group_wire() -> list[dict[str, str]]:
    """分组目录：供前端渲染筛选条与分组标题。"""

    return [
        {
            "key": key,
            "title": meta["title"],
            "title_en": meta["title_en"],
        }
        for key, meta in SKILL_GROUP_TITLES.items()
    ]


def resolve_skill_group(meta: dict[str, Any], skill_name: str) -> str:
    """从 SKILL.md 的 metadata 取分组；未声明或非法都显式失败。"""

    group = meta.get("group")
    if group is None:
        raise ValueError(f"skill metadata 缺少 group: {skill_name}")
    if not isinstance(group, str) or group == "":
        raise ValueError(f"skill metadata.group 必须是非空字符串: {skill_name}")
    if group not in SKILL_GROUP_TITLES:
        raise ValueError(
            f"skill metadata.group 非法: {skill_name} group={group!r}"
            f"（允许 {'/'.join(SKILL_GROUP_ORDER)}）"
        )
    return group
