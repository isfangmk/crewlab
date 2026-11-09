"""本地 SKILL.md 目录装载；Skill 通过 adk_additional_tools 挂真实内置 Tool。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from google.adk.skills import Skill, load_skills_from_dir
from google.adk.tools.skill_toolset import SkillDiscoveryMode, SkillToolset

from app.adk_runtime.builtin_tools import build_knowledge_tools, tool_param_specs

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"


def skills_dir() -> Path:
    if not _SKILLS_DIR.is_dir():
        raise FileNotFoundError(f"技能目录不存在: {_SKILLS_DIR}")
    return _SKILLS_DIR


def load_local_skills() -> list[Skill]:
    path = skills_dir()
    skills = load_skills_from_dir(path)
    logger.info("已装载本地技能 dir=%s count=%s", path, len(skills))
    return skills


def build_skill_toolset() -> SkillToolset:
    """EAGER：目录写入系统提示，避免无差别 list_skills；真正正文仍靠 load_skill 按需注入。"""

    knowledge_tools = build_knowledge_tools()
    return SkillToolset(
        skills=load_local_skills(),
        discovery_mode=SkillDiscoveryMode.EAGER,
        additional_tools=knowledge_tools,
    )


def _require_meta_str(meta: dict, key: str, skill_name: str) -> str:
    value = meta.get(key)
    if not isinstance(value, str) or value == "":
        raise ValueError(f"skill metadata.{key} 必须是非空字符串: {skill_name}")
    return value


def _skill_declared_tools(skill: Skill) -> list[str]:
    meta = skill.frontmatter.metadata
    if not isinstance(meta, dict):
        raise TypeError(f"skill metadata 必须是对象: {skill.frontmatter.name}")
    if "adk_additional_tools" not in meta:
        raise ValueError(f"skill 缺少 metadata.adk_additional_tools: {skill.frontmatter.name}")
    extra = meta["adk_additional_tools"]
    if not isinstance(extra, list) or len(extra) == 0:
        raise ValueError(f"adk_additional_tools 必须是非空数组: {skill.frontmatter.name}")
    for i, name in enumerate(extra):
        if not isinstance(name, str) or name == "":
            raise TypeError(f"adk_additional_tools[{i}] 必须是非空字符串: {skill.frontmatter.name}")
    return list(extra)


def skill_to_wire(skill: Skill) -> dict:
    """映射为前端 SkillSummary。

    风险等级与参数从 SKILL.md frontmatter 与工具签名读取，
    不写死：写死会让前端把任意技能都当成可安全执行。
    """

    fm = skill.frontmatter
    meta = fm.metadata
    if not isinstance(meta, dict):
        raise TypeError(f"skill metadata 必须是对象: {fm.name}")
    category = _require_meta_str(meta, "category", fm.name)
    tools = _skill_declared_tools(skill)

    registry = builtin_tool_registry()
    params: list[dict] = []
    for tool_name in tools:
        func = registry.get(tool_name)
        if func is None:
            # 技能声明了平台没有的工具：显式暴露为不可执行，
            # 不能在技能页显示一个必然失败的「执行」按钮。
            params = []
            break
        params.extend(tool_param_specs(func))

    executable = all(name in registry for name in tools)
    risk = _require_meta_str(meta, "risk", fm.name)
    if risk not in ("safe", "mutating", "dangerous"):
        raise ValueError(f"skill metadata.risk 非法: {fm.name} risk={risk}")

    return {
        "key": fm.name,
        "name": fm.name,
        "description": fm.description,
        "class": risk,
        "category": category,
        "params": params,
        "source": "builtin",
        "inventory_only": not executable,
        "tools": tools,
    }


def builtin_tool_registry() -> dict[str, Any]:
    """内置工具注册表（延迟导入避免循环依赖）。"""

    from app.adk_runtime.builtin_tools import builtin_tool_registry as _registry

    return _registry()


def list_skill_summaries() -> list[dict]:
    return [skill_to_wire(s) for s in load_local_skills()]


def reload_skill_summaries() -> list[dict]:
    """强制重读磁盘并校验全部技能；任一非法则整批失败。"""

    skills = load_local_skills()
    items = [skill_to_wire(s) for s in skills]
    logger.info("技能目录重载完成 count=%s", len(items))
    return items


def get_skill_summary(key: str) -> dict:
    if key == "":
        raise ValueError("skill key 不能为空")
    for skill in load_local_skills():
        if skill.frontmatter.name == key:
            return skill_to_wire(skill)
    raise LookupError(f"技能不存在: {key}")
