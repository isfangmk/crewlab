"""技能目录：磁盘 SKILL.md（内置）+ 用户自定义技能（DB），统一装成 ADK Skill。

磁盘技能只读；用户技能可增删改，支持 zip 导入导出。
运行时按助理勾选的技能集合装配，未勾选的技能不进 SkillToolset。
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Union, cast

from google.adk.skills import Script, Skill, load_skills_from_dir
from google.adk.tools.skill_toolset import SkillDiscoveryMode, SkillToolset
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app as _app_pkg
from app.adk_runtime.sandbox_code_executor import SandboxCodeExecutor
from app.models.skill import UserSkill
from app.services.skills import skill_bundle, skill_groups
from app.services.skills.skill_bundle import RISK_LEVELS, SkillPayload

logger = logging.getLogger(__name__)

# 内置技能目录锚定 app 包内（app/skills）：按 app 包位置推导，不随本模块所在层级搬动失效
_SKILLS_DIR = Path(_app_pkg.__file__).resolve().parent / "skills"

SkillSource = Literal["disk", "user"]
# 技能来源类型：builtin = 随包发布的内置技能；created = 平台内新建；imported = zip 导入。
SkillOrigin = Literal["builtin", "created", "imported"]


@dataclass(frozen=True)
class SkillEntry:
    """一条技能目录项：ADK Skill + 来源。"""

    skill: Skill
    source: SkillSource
    origin: SkillOrigin


def skills_dir() -> Path:
    if not _SKILLS_DIR.is_dir():
        raise FileNotFoundError(f"技能目录不存在: {_SKILLS_DIR}")
    return _SKILLS_DIR


def load_local_skills() -> list[Skill]:
    path = skills_dir()
    skills = load_skills_from_dir(path)
    logger.info("已装载本地技能 dir=%s skills=%s", path, sorted(s.name for s in skills))
    return skills


def build_skill_toolset(
    skills: list[Skill],
) -> SkillToolset:
    """EAGER：目录写入系统提示，避免无差别 list_skills；真正正文仍靠 load_skill 按需注入。

    skills 是已解析好的技能集合（可为空 = 不挂任何技能）。
    本 toolset 只装配技能；知识检索由内置工具集常驻装配。
    """

    resolved = list(skills)
    logger.info(
        "装配技能集 skills=%s",
        [skill.name for skill in resolved],
    )
    return SkillToolset(
        skills=resolved,
        discovery_mode=SkillDiscoveryMode.EAGER,
        # 技能脚本在沙盒里跑：技能可以来自用户 zip 导入，
        # 直接在宿主进程执行等于把宿主机交给第三方脚本。
        code_executor=SandboxCodeExecutor(),
    )


def _require_meta_str(meta: dict, key: str, skill_name: str) -> str:
    value = meta.get(key)
    if not isinstance(value, str) or value == "":
        raise ValueError(f"skill metadata.{key} 必须是非空字符串: {skill_name}")
    return value


def _resource_wire(
    files: Mapping[str, Union[str, bytes, Script]], include_contents: bool
) -> list[dict[str, Any]]:
    """资源目录出参：默认只给路径与字节数；详情/编辑时才带正文。

    scripts/ 里的值在 ADK 里是 Script(src=...) 而不是裸字符串，统一取 src。
    """

    items: list[dict[str, Any]] = []
    for path, value in sorted(files.items()):
        if isinstance(value, Script):
            value = value.src
        # 直接判断 bytes 而不是先存成布尔量：pyright 只认 isinstance 在条件里的收窄
        if isinstance(value, bytes):
            item: dict[str, Any] = {"path": path, "size": len(value), "binary": True}
            if include_contents:
                item["base64"] = base64.b64encode(value).decode("ascii")
        else:
            item = {"path": path, "size": len(value.encode("utf-8")), "binary": False}
            if include_contents:
                item["text"] = value
        items.append(item)
    return items


def skill_to_wire(
    entry: SkillEntry,
    include_contents: bool = False,
) -> dict:
    """映射为前端 SkillSummary。

    风险等级与参数从 SKILL.md frontmatter 读取，不写死：写死会让前端把任意
    技能都当成可安全执行。工具能力由助理统一授予。
    """

    skill = entry.skill
    fm = skill.frontmatter
    meta = fm.metadata
    if not isinstance(meta, dict):
        raise TypeError(f"skill metadata 必须是对象: {fm.name}")
    group = skill_groups.resolve_skill_group(meta, fm.name)
    risk = _require_meta_str(meta, "risk", fm.name)
    if risk not in RISK_LEVELS:
        raise ValueError(f"skill metadata.risk 非法: {fm.name} risk={risk}")

    return {
        "key": fm.name,
        "name": fm.name,
        "description": fm.description,
        "class": risk,
        "group": group,
        "preapproved_tools": fm.allowed_tools or "",
        "inject_state": meta.get("adk_inject_state") is True,
        "instructions": skill.instructions,
        "source": entry.source,
        "origin": entry.origin,
        "references": _resource_wire(skill.resources.references, include_contents),
        "assets": _resource_wire(skill.resources.assets, include_contents),
        "scripts": _resource_wire(skill.resources.scripts, include_contents),
    }


# ---------------------------------------------------------------- DB 读写


def _row_to_payload(row: UserSkill) -> SkillPayload:
    metadata = json.loads(row.metadata_json)
    # 分组以列为准，metadata 是给 SKILL.md frontmatter 用的同一份值
    metadata["group"] = row.skill_group
    return SkillPayload(
        key=row.key,
        description=row.description,
        instructions=row.instructions,
        preapproved_tools=row.preapproved_tools,
        metadata=metadata,
        references=skill_bundle.decode_files(row.references_json),
        assets=skill_bundle.decode_files(row.assets_json),
        scripts={
            str(path): str(value)
            for path, value in skill_bundle.decode_files(row.scripts_json).items()
        },
    )


def _row_to_skill(row: UserSkill) -> Skill:
    return skill_bundle.payload_to_skill(_row_to_payload(row))


async def _user_skill_rows(db: AsyncSession) -> list[UserSkill]:
    result = await db.execute(select(UserSkill).order_by(UserSkill.key))
    return list(result.scalars().all())


def _row_origin(row: UserSkill) -> SkillOrigin:
    """库里记录的来源类型；取值由数据库 CHECK 约束保证合法。"""

    origin = row.origin
    if origin not in ("created", "imported"):
        raise ValueError(f"技能来源类型非法: {row.key}={origin!r}")
    return cast(SkillOrigin, origin)


async def list_skill_entries(db: AsyncSession) -> list[SkillEntry]:
    """磁盘技能 + 用户技能，按 key 排序；重复 key 直接报错（不静默覆盖）。"""

    entries: list[SkillEntry] = [
        SkillEntry(skill=skill, source="disk", origin="builtin")
        for skill in load_local_skills()
    ]
    seen = {entry.skill.name for entry in entries}
    for row in await _user_skill_rows(db):
        if row.key in seen:
            raise ValueError(f"技能名与内置技能冲突: {row.key}")
        entries.append(
            SkillEntry(
                skill=_row_to_skill(row),
                source="user",
                origin=_row_origin(row),
            )
        )
    entries.sort(key=lambda entry: entry.skill.name)
    return entries


async def resolve_skills(db: AsyncSession, keys: list[str]) -> list[Skill]:
    """按助理勾选的 key 取技能；空列表 = 不装配任何技能。未知 key 显式失败。"""

    entries = await list_skill_entries(db)
    by_key = {entry.skill.name: entry.skill for entry in entries}
    missing = [key for key in keys if key not in by_key]
    if missing:
        raise LookupError(f"技能不存在: {missing}")
    return [by_key[key] for key in keys]


async def get_skill_entry(db: AsyncSession, key: str) -> SkillEntry:
    if key == "":
        raise ValueError("skill key 不能为空")
    for entry in await list_skill_entries(db):
        if entry.skill.name == key:
            return entry
    raise LookupError(f"技能不存在: {key}")


def _validate_save_payload(
    payload: SkillPayload,
    risk: str,
    group: str,
) -> None:
    if risk not in RISK_LEVELS:
        raise ValueError(f"风险等级必须是 {'/'.join(RISK_LEVELS)}: {risk}")
    skill_groups.validate_skill_group(group)
    skill_bundle.validate_payload(payload)
    # 平台侧属性写进 metadata，导出的 SKILL.md 才能原样往返
    payload.metadata["risk"] = risk
    payload.metadata["group"] = group


async def create_user_skill(
    db: AsyncSession,
    payload: SkillPayload,
    risk: str,
    group: str,
    origin: SkillOrigin = "created",
) -> UserSkill:
    _validate_save_payload(payload, risk, group)
    if await db.get(UserSkill, payload.key) is not None:
        raise ValueError(f"技能已存在: {payload.key}")
    for entry in await list_skill_entries(db):
        if entry.skill.name == payload.key:
            raise ValueError(f"技能名与内置技能冲突: {payload.key}")
    row = UserSkill(
        key=payload.key,
        description=payload.description,
        preapproved_tools=payload.preapproved_tools,
        risk=risk,
        skill_group=group,
        origin=origin,
        instructions=payload.instructions,
        metadata_json=json.dumps(payload.metadata, ensure_ascii=False),
        references_json=skill_bundle.encode_files(payload.references),
        assets_json=skill_bundle.encode_files(payload.assets),
        scripts_json=skill_bundle.encode_files(payload.scripts),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info(
        "创建技能 key=%s origin=%s risk=%s references=%s assets=%s scripts=%s",
        row.key,
        origin,
        risk,
        len(payload.references),
        len(payload.assets),
        len(payload.scripts),
    )
    return row


async def update_user_skill(
    db: AsyncSession,
    key: str,
    payload: SkillPayload,
    risk: str,
    group: str,
) -> UserSkill:
    if payload.key != key:
        raise ValueError(f"技能名不能修改: {key} -> {payload.key}")
    _validate_save_payload(payload, risk, group)
    row = await db.get(UserSkill, key)
    if row is None:
        raise LookupError(f"技能不存在或为内置技能: {key}")
    row.description = payload.description
    row.preapproved_tools = payload.preapproved_tools
    row.risk = risk
    row.skill_group = group
    row.instructions = payload.instructions
    row.metadata_json = json.dumps(payload.metadata, ensure_ascii=False)
    row.references_json = skill_bundle.encode_files(payload.references)
    row.assets_json = skill_bundle.encode_files(payload.assets)
    row.scripts_json = skill_bundle.encode_files(payload.scripts)
    await db.commit()
    await db.refresh(row)
    logger.info("更新技能 key=%s", row.key)
    return row


async def delete_user_skill(db: AsyncSession, key: str) -> None:
    row = await db.get(UserSkill, key)
    if row is None:
        raise LookupError(f"技能不存在或为内置技能: {key}")
    await db.delete(row)
    await db.commit()
    logger.info("删除技能 key=%s", key)


async def export_skill_zip(db: AsyncSession, key: str) -> bytes:
    entry = await get_skill_entry(db, key)
    payload = _payload_from_entry(entry)
    return skill_bundle.payload_to_zip(payload)


def _payload_from_entry(entry: SkillEntry) -> SkillPayload:
    skill = entry.skill
    meta = dict(skill.frontmatter.metadata)
    return SkillPayload(
        key=skill.frontmatter.name,
        description=skill.frontmatter.description,
        instructions=skill.instructions,
        preapproved_tools=skill.frontmatter.allowed_tools or "",
        metadata=meta,
        references=dict(skill.resources.references),
        assets=dict(skill.resources.assets),
        scripts={path: script.src for path, script in skill.resources.scripts.items()},
    )


async def list_skill_summaries(db: AsyncSession) -> list[dict]:
    return [skill_to_wire(entry) for entry in await list_skill_entries(db)]


async def get_skill_summary(db: AsyncSession, key: str) -> dict:
    """详情：带资源正文，供详情查看与在线编辑回填（避免编辑丢资源）。"""

    entry = await get_skill_entry(db, key)
    return skill_to_wire(entry, include_contents=True)


async def disk_skill_summaries() -> list[dict]:
    """只读磁盘技能（重载校验用）。"""

    return [
        skill_to_wire(SkillEntry(skill=skill, source="disk", origin="builtin"))
        for skill in load_local_skills()
    ]
