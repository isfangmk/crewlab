"""子 Agent 运行时装配：把父助理委托的子助理递归装配成 ADK LlmAgent。

v1 口径：
  - 子助理继承父回合的 provider/model（换模型留待后续）；
  - 子助理拥有自己的人设（compose_persona）与知识范围（query_knowledge 收口），
    技能按子助理自身勾选装配；不挂 MCP（v1 边界）；
  - 内置工具只由根 Agent 挂载：委托后父子工具会进同一 ADK 注册表，
    重复同名工具会被上游以「Tool names must be unique」拒绝；
  - ADK agent 名由业务名净化成合法 Python 标识符（委托工具名由它派生），
    撞名时追加序号；委托链已在保存时防环，运行期按树递归安全。
"""

from __future__ import annotations

import re

from google.adk.agents import LlmAgent

from app.services.agent_service import compose_persona, get_agent
from app.services.knowledge_scope import KnowledgeScope


def adk_identifier(name: str) -> str:
    """业务名 → 合法 Python 标识符（ADK 委托工具名由它派生）。"""

    cleaned = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    if cleaned == "" or cleaned[0].isdigit():
        cleaned = f"agent_{cleaned}"
    return cleaned


def dedupe_identifiers(names: list[str]) -> list[str]:
    """净化后撞名时追加序号，保证 ADK agent 树内唯一。"""

    used: dict[str, int] = {}
    out: list[str] = []
    for name in names:
        final = name
        if final in used:
            used[name] = used.get(name, 0) + 1
            final = f"{name}_{used[name]}"
        else:
            used[name] = 0
        out.append(final)
    return out


async def build_sub_agents_for_agent(
    db,
    parent,
    *,
    provider_id: str,
    model: str,
    base_url: str,
    api_key: str,
    model_prefix: str = "",
    provider_extra_body: dict | None = None,
    thinking_enabled: bool = False,
    depth: int = 0,
) -> list[LlmAgent]:
    """装配父助理委托的子 Agent 列表（递归，深度上限 3）。"""

    if depth >= 3 or len(parent.sub_agents) == 0:
        return []

    from app.adk_runtime.agent_factory import build_chat_agent
    from app.services.skills_service import resolve_skills

    raw_names = [adk_identifier(name) for name in parent.sub_agents]
    adk_names = dedupe_identifiers(raw_names)

    agents: list[LlmAgent] = []
    for sub_name, adk_name in zip(parent.sub_agents, adk_names, strict=True):
        sub = await get_agent(db, sub_name)
        sub_skills = await resolve_skills(db, sub.skills)
        sub_persona = compose_persona(sub)
        agents.append(
            build_chat_agent(
                provider_id=provider_id,
                model=model,
                base_url=base_url,
                api_key=api_key,
                agent_name=sub_name,
                agent_description=sub.description,
                persona_instruction=sub_persona,
                thinking_enabled=thinking_enabled,
                model_prefix=model_prefix,
                provider_extra_body=provider_extra_body,
                adk_name=adk_name,
                max_turns=sub.max_turns,
                knowledge_scope=(
                    sub.knowledge_scope
                    if isinstance(sub.knowledge_scope, KnowledgeScope)
                    else None
                ),
                extra_tools=None,
                skills=sub_skills,
                include_builtin_tools=False,
                # MCP toolset 只在父回合按父助理白名单装配，子助理不挂（v1 边界）
                sub_agents=await build_sub_agents_for_agent(
                    db,
                    sub,
                    provider_id=provider_id,
                    model=model,
                    base_url=base_url,
                    api_key=api_key,
                    model_prefix=model_prefix,
                    provider_extra_body=provider_extra_body,
                    thinking_enabled=thinking_enabled,
                    depth=depth + 1,
                ),
            )
        )
    return agents
