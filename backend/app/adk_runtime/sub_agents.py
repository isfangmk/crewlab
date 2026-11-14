"""子 Agent 运行时装配：把父助理委托的子助理递归装配成 ADK LlmAgent。

口径：
  - 子助理继承父回合的 provider/model（换模型留待后续）；
  - 子助理拥有自己的人设、知识范围、技能、内置工具与 MCP 白名单，
    禁用名单 / 只读审批同样独立生效；
  - ADK agent 名由业务名净化成合法标识符，并在整棵委托树内去重；
  - `when_to_use` 写进 ADK 的 description，作为父助理判断何时转交的依据；
  - 委托链已在保存时校验防环与深度，运行期按树递归安全。
"""

from __future__ import annotations

from google.adk.agents import LlmAgent

from app.adk_runtime.naming import (
    adk_identifier,
    dedupe_identifiers,
    root_adk_name,
    unique_identifier,
)
from app.services.agent_service import compose_persona, get_agent
from app.services.agent_tool_service import build_agent_tool_bundle
from app.services.knowledge_scope import KnowledgeScope

__all__ = [
    "adk_identifier",
    "dedupe_identifiers",
    "build_sub_agents_for_agent",
]


def _delegation_description(description: str, when_to_use: str) -> str:
    """ADK 用 description 生成 transfer 工具说明，必须带上「何时使用」。"""

    when = when_to_use.strip()
    if when == "":
        return description
    if description.strip() == "":
        return f"何时使用：{when}"
    return f"{description}\n\n何时使用：{when}"


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
    web_search_enabled: bool = False,
    depth: int = 0,
    used_names: set[str] | None = None,
) -> list[LlmAgent]:
    """装配父助理委托的子 Agent 列表（递归，深度上限 3）。"""

    if depth >= 3 or len(parent.sub_agents) == 0:
        return []
    if used_names is None:
        # 根 Agent 也占一个 ADK 名；子 Agent 不能与它撞名
        used_names = {root_adk_name(parent.name)}

    from app.adk_runtime.agent_factory import build_chat_agent

    agents: list[LlmAgent] = []
    for sub_name in parent.sub_agents:
        sub = await get_agent(db, sub_name)
        bundle = await build_agent_tool_bundle(
            db,
            sub,
            web_search_enabled=web_search_enabled,
        )
        adk_name = unique_identifier(adk_identifier(sub_name), used_names)
        agents.append(
            build_chat_agent(
                provider_id=provider_id,
                model=model,
                base_url=base_url,
                api_key=api_key,
                agent_name=sub_name,
                agent_description=_delegation_description(
                    sub.description, sub.when_to_use
                ),
                persona_instruction=compose_persona(sub),
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
                extra_tools=bundle.extra_tools,
                confirm_tool_names=bundle.confirm_tool_names,
                disallowed_tool_names=bundle.disallowed_tool_names,
                skills=bundle.runtime_skills,
                include_builtin_tools=True,
                # 子 Agent 无人值守：澄清提问只在根 Agent（与用户直接对话）上
                include_ask_user=False,
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
                    web_search_enabled=web_search_enabled,
                    depth=depth + 1,
                    used_names=used_names,
                ),
            )
        )
    return agents
