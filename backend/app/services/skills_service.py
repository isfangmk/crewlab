"""技能目录：磁盘 SKILL.md（内置）+ 用户自定义技能（DB），统一装成 ADK Skill。

磁盘技能只读；用户技能可增删改，支持 zip 导入导出。
运行时按助理勾选的技能集合装配，未勾选的技能不进 SkillToolset。
"""

from __future__ import annotations

import base64
import inspect
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from google.adk.skills import Script, Skill, load_skills_from_dir
from google.adk.tools.skill_toolset import SkillDiscoveryMode, SkillToolset
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adk_runtime.builtin_tools import build_knowledge_tools, tool_param_specs
from app.models_orm.skill import UserSkill
from app.services import mcp_service, skill_bundle
from app.services.knowledge_scope import KnowledgeScope
from app.services.mcp_service import McpToolRef
from app.services.skill_bundle import RISK_LEVELS, SkillPayload

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"

SkillSource = Literal["disk", "user"]


@dataclass(frozen=True)
class SkillEntry:
    """一条技能目录项：ADK Skill + 来源。"""

    skill: Skill
    source: SkillSource


def skills_dir() -> Path:
    if not _SKILLS_DIR.is_dir():
        raise FileNotFoundError(f"技能目录不存在: {_SKILLS_DIR}")
    return _SKILLS_DIR


def load_local_skills() -> list[Skill]:
    path = skills_dir()
    skills = load_skills_from_dir(path)
    logger.info("已装载本地技能 dir=%s count=%s", path, len(skills))
    return skills


def build_skill_toolset(
    scope: KnowledgeScope | None = None,
    skills: list[Skill] | None = None,
) -> SkillToolset:
    """EAGER：目录写入系统提示，避免无差别 list_skills；真正正文仍靠 load_skill 按需注入。

    scope 是助理的知识范围，注入给 query_knowledge（空 = 全库）。
    skills 是助理勾选的技能集合；None 表示继承全部（磁盘 + 用户技能）。
    """

    resolved = list(load_local_skills()) if skills is None else list(skills)
    knowledge_tools = build_knowledge_tools(scope)
    logger.info(
        "装配技能集 skills=%s knowledge_tools=%s",
        [skill.name for skill in resolved],
        len(knowledge_tools),
    )
    return SkillToolset(
        skills=resolved,
        discovery_mode=SkillDiscoveryMode.EAGER,
        additional_tools=knowledge_tools,
    )


def _require_meta_str(meta: dict, key: str, skill_name: str) -> str:
    value = meta.get(key)
    if not isinstance(value, str) or value == "":
        raise ValueError(f"skill metadata.{key} 必须是非空字符串: {skill_name}")
    return value


def _skill_declared_tools(skill: Skill) -> list[str]:
    """技能声明的工具；不是每个技能都要挂工具，所以允许缺省与空数组。"""

    meta = skill.frontmatter.metadata
    if not isinstance(meta, dict):
        raise TypeError(f"skill metadata 必须是对象: {skill.frontmatter.name}")
    extra = meta.get("adk_additional_tools")
    if extra is None:
        return []
    if not isinstance(extra, list):
        raise TypeError(f"adk_additional_tools 必须是数组: {skill.frontmatter.name}")
    for i, name in enumerate(extra):
        if not isinstance(name, str) or name == "":
            raise TypeError(f"adk_additional_tools[{i}] 必须是非空字符串: {skill.frontmatter.name}")
    return list(extra)


def declared_tools_of_skills(skills: list[Skill]) -> set[str]:
    """一组已装配技能声明的工具名（内置工具名或 MCP wire 名）。

    运行时用它把技能声明的 MCP 工具补进白名单：技能可以先建后改，
    只依赖保存助理时并入的那一份会漏掉后来新增的声明。
    """

    names: set[str] = set()
    for skill in skills:
        names.update(_skill_declared_tools(skill))
    return names


def _resource_wire(
    files: dict[str, str | bytes | Script], include_contents: bool
) -> list[dict[str, Any]]:
    """资源目录出参：默认只给路径与字节数；详情/编辑时才带正文。

    scripts/ 里的值在 ADK 里是 Script(src=...) 而不是裸字符串，统一取 src。
    """

    items: list[dict[str, Any]] = []
    for path, value in sorted(files.items()):
        if isinstance(value, Script):
            value = value.src
        is_bytes = isinstance(value, bytes)
        item: dict[str, Any] = {
            "path": path,
            "size": len(value) if is_bytes else len(value.encode("utf-8")),
            "binary": is_bytes,
        }
        if include_contents:
            if is_bytes:
                item["base64"] = base64.b64encode(value).decode("ascii")
            else:
                item["text"] = value
        items.append(item)
    return items


def skill_to_wire(
    entry: SkillEntry,
    include_contents: bool = False,
    mcp_tools: dict[str, McpToolRef] | None = None,
) -> dict:
    """映射为前端 SkillSummary。

    风险等级与参数从 SKILL.md frontmatter 与工具签名读取，
    不写死：写死会让前端把任意技能都当成可安全执行。

    声明的工具可以是两类：内置工具（注册表里的函数）或 MCP 工具（wire 名
    mcp__<server>__<tool>，参数来自探测缓存的 input_schema）。
    """

    skill = entry.skill
    fm = skill.frontmatter
    meta = fm.metadata
    if not isinstance(meta, dict):
        raise TypeError(f"skill metadata 必须是对象: {fm.name}")
    category = _require_meta_str(meta, "category", fm.name)
    risk = _require_meta_str(meta, "risk", fm.name)
    if risk not in RISK_LEVELS:
        raise ValueError(f"skill metadata.risk 非法: {fm.name} risk={risk}")
    tools = _skill_declared_tools(skill)
    mcp_catalog = mcp_tools if mcp_tools is not None else {}

    registry = builtin_tool_registry()
    params: list[dict] = []
    tool_refs: list[dict[str, Any]] = []
    for tool_name in tools:
        func = registry.get(tool_name)
        if func is not None:
            doc = inspect.getdoc(func) or ""
            tool_refs.append(
                {
                    "name": tool_name,
                    "kind": "builtin",
                    "available": True,
                    "description": doc.splitlines()[0] if doc != "" else "",
                }
            )
            params.extend(tool_param_specs(func))
            continue
        ref = mcp_catalog.get(tool_name)
        if ref is not None:
            # Server 停用 / 探测失败时工具仍在目录里，但运行时不会挂载
            tool_refs.append(
                {
                    "name": tool_name,
                    "kind": "mcp",
                    "server": ref.server_name,
                    "available": ref.available,
                    "description": ref.description,
                }
            )
            params.extend(mcp_service.skill_params_from_schema(ref.input_schema))
            continue
        # 技能声明了平台没有的工具：显式暴露为不可执行，
        # 不能在技能页显示一个必然失败的「执行」按钮。
        tool_refs.append(
            {"name": tool_name, "kind": "unknown", "available": False, "description": ""}
        )

    executable = all(bool(ref["available"]) for ref in tool_refs)

    return {
        "key": fm.name,
        "name": fm.name,
        "description": fm.description,
        "class": risk,
        "category": category,
        "license": fm.license or "",
        "compatibility": fm.compatibility or "",
        "preapproved_tools": fm.allowed_tools or "",
        "inject_state": meta.get("adk_inject_state") is True,
        "instructions": skill.instructions,
        "params": params,
        "source": entry.source,
        "inventory_only": not executable,
        "tools": tools,
        "tool_refs": tool_refs,
        "references": _resource_wire(skill.resources.references, include_contents),
        "assets": _resource_wire(skill.resources.assets, include_contents),
        "scripts": _resource_wire(skill.resources.scripts, include_contents),
    }


def builtin_tool_registry() -> dict[str, Any]:
    """内置工具注册表（延迟导入避免循环依赖）。"""

    from app.adk_runtime.builtin_tools import builtin_tool_registry as _registry

    return _registry()


# ---------------------------------------------------------------- DB 读写


def _row_to_payload(row: UserSkill) -> SkillPayload:
    return SkillPayload(
        key=row.key,
        description=row.description,
        instructions=row.instructions,
        license=row.license,
        compatibility=row.compatibility,
        preapproved_tools=row.preapproved_tools,
        metadata=json.loads(row.metadata_json),
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


async def list_skill_entries(db: AsyncSession) -> list[SkillEntry]:
    """磁盘技能 + 用户技能，按 key 排序；重复 key 直接报错（不静默覆盖）。"""

    entries: list[SkillEntry] = [
        SkillEntry(skill=skill, source="disk") for skill in load_local_skills()
    ]
    seen = {entry.skill.name for entry in entries}
    for row in await _user_skill_rows(db):
        if row.key in seen:
            raise ValueError(f"技能名与内置技能冲突: {row.key}")
        entries.append(SkillEntry(skill=_row_to_skill(row), source="user"))
    entries.sort(key=lambda entry: entry.skill.name)
    return entries


async def resolve_skills(db: AsyncSession, keys: list[str] | None) -> list[Skill]:
    """按助理勾选的 key 取技能；None = 继承全部。未知 key 显式失败。"""

    entries = await list_skill_entries(db)
    if keys is None:
        return [entry.skill for entry in entries]
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


async def load_tool_catalog(
    db: AsyncSession,
) -> tuple[dict[str, Any], dict[str, McpToolRef]]:
    """技能可以声明的工具全集：内置工具注册表 + MCP 探测缓存。"""

    return builtin_tool_registry(), await mcp_service.mcp_tool_catalog(db)


def _validate_save_payload(
    payload: SkillPayload,
    risk: str,
    category: str,
    forbid_unknown_tools: bool,
    known_tools: set[str],
) -> None:
    if risk not in RISK_LEVELS:
        raise ValueError(f"风险等级必须是 {'/'.join(RISK_LEVELS)}: {risk}")
    if category.strip() == "":
        raise ValueError("分类不能为空")
    if forbid_unknown_tools:
        declared = payload.declared_tools()
        unknown = [name for name in declared if name not in known_tools]
        if unknown:
            hint = ""
            if any(name.startswith("mcp__") for name in unknown):
                hint = (
                    "；mcp__ 开头的工具来自 MCP Server 的探测缓存，"
                    "请先在 MCP 页「测试连接」并确认工具名"
                )
            raise ValueError(
                f"技能声明的工具不在工具目录（内置工具 + MCP 工具）: {unknown}{hint}"
            )
    skill_bundle.validate_payload(payload)
    # 平台侧属性写进 metadata，导出的 SKILL.md 才能原样往返
    payload.metadata["risk"] = risk
    payload.metadata["category"] = category


async def create_user_skill(
    db: AsyncSession,
    payload: SkillPayload,
    risk: str,
    category: str,
    forbid_unknown_tools: bool = True,
) -> UserSkill:
    registry, mcp_catalog = await load_tool_catalog(db)
    _validate_save_payload(
        payload,
        risk,
        category,
        forbid_unknown_tools,
        known_tools=set(registry) | set(mcp_catalog),
    )
    if await db.get(UserSkill, payload.key) is not None:
        raise ValueError(f"技能已存在: {payload.key}")
    for entry in await list_skill_entries(db):
        if entry.skill.name == payload.key:
            raise ValueError(f"技能名与内置技能冲突: {payload.key}")
    row = UserSkill(
        key=payload.key,
        description=payload.description,
        license=payload.license,
        compatibility=payload.compatibility,
        preapproved_tools=payload.preapproved_tools,
        risk=risk,
        category=category,
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
        "创建技能 key=%s risk=%s tools=%s references=%s assets=%s scripts=%s",
        row.key,
        risk,
        payload.declared_tools(),
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
    category: str,
    forbid_unknown_tools: bool = True,
) -> UserSkill:
    if payload.key != key:
        raise ValueError(f"技能名不能修改: {key} -> {payload.key}")
    registry, mcp_catalog = await load_tool_catalog(db)
    _validate_save_payload(
        payload,
        risk,
        category,
        forbid_unknown_tools,
        known_tools=set(registry) | set(mcp_catalog),
    )
    row = await db.get(UserSkill, key)
    if row is None:
        raise LookupError(f"技能不存在或为内置技能: {key}")
    row.description = payload.description
    row.license = payload.license
    row.compatibility = payload.compatibility
    row.preapproved_tools = payload.preapproved_tools
    row.risk = risk
    row.category = category
    row.instructions = payload.instructions
    row.metadata_json = json.dumps(payload.metadata, ensure_ascii=False)
    row.references_json = skill_bundle.encode_files(payload.references)
    row.assets_json = skill_bundle.encode_files(payload.assets)
    row.scripts_json = skill_bundle.encode_files(payload.scripts)
    await db.commit()
    await db.refresh(row)
    logger.info("更新技能 key=%s tools=%s", row.key, payload.declared_tools())
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
        license=skill.frontmatter.license or "",
        compatibility=skill.frontmatter.compatibility or "",
        preapproved_tools=skill.frontmatter.allowed_tools or "",
        metadata=meta,
        references=dict(skill.resources.references),
        assets=dict(skill.resources.assets),
        scripts={path: script.src for path, script in skill.resources.scripts.items()},
    )


async def list_skill_summaries(db: AsyncSession) -> list[dict]:
    mcp_tools = await mcp_service.mcp_tool_catalog(db)
    return [skill_to_wire(entry, mcp_tools=mcp_tools) for entry in await list_skill_entries(db)]


async def get_skill_summary(db: AsyncSession, key: str) -> dict:
    """详情：带资源正文，供详情查看与在线编辑回填（避免编辑丢资源）。"""

    mcp_tools = await mcp_service.mcp_tool_catalog(db)
    entry = await get_skill_entry(db, key)
    return skill_to_wire(entry, include_contents=True, mcp_tools=mcp_tools)


async def disk_skill_summaries(db: AsyncSession) -> list[dict]:
    """只读磁盘技能（重载校验用）。MCP 目录一并带入，声明的 MCP 工具才判得准。"""

    mcp_tools = await mcp_service.mcp_tool_catalog(db)
    return [
        skill_to_wire(SkillEntry(skill=skill, source="disk"), mcp_tools=mcp_tools)
        for skill in load_local_skills()
    ]
