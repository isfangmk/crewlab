"""助理 Playground 试跑：不落会话、不落观测的单轮流式对话。

与正式会话的差别：不写 chat_messages / observation_turns，ADK 会话用
InMemorySessionService 一次性创建，跑完即弃，试跑不影响正式对话上下文。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from uuid import uuid4

from google.adk.sessions import InMemorySessionService
from sqlalchemy.ext.asyncio import AsyncSession

from app.adk_runtime.stream_bridge import SseFrame, stream_adk_turn
from app.services import agent_service, app_settings_service, llm_config
from app.services.agent_service import ResolvedAgent
from app.services.agent_tool_service import build_agent_tool_bundle
from app.services.llm_config import resolve_model

logger = logging.getLogger(__name__)


async def stream_preview(
    db: AsyncSession,
    *,
    agent: ResolvedAgent,
    content: str,
    provider: str | None = None,
    model: str | None = None,
    thinking_enabled: bool = False,
    web_search_enabled: bool = False,
    locale: str | None = None,
) -> AsyncIterator[SseFrame]:
    """跑一轮试跑对话，产出与正式会话同构的 SSE 帧。"""

    # 模型解析口径与评测目标一致：助理模型优先，其次显式 provider+model，最后全局默认
    from app.services.llm_config import (
        get_default_selection,
        list_enabled_providers,
        split_models,
    )

    if agent.model != "":
        for candidate in await list_enabled_providers(db):
            if agent.model in split_models(candidate.models):
                llm = await resolve_model(db, candidate.provider_id, agent.model)
                use_model = agent.model
                break
        else:
            raise LookupError(f"助理模型不在任何已启用 provider 中: {agent.model}")
    elif provider is not None and provider != "" and model is not None and model != "":
        llm = await resolve_model(db, provider, model)
        use_model = model
    elif (provider is None or provider == "") and (model is None or model == ""):
        default_provider, default_model = await get_default_selection(db)
        llm = await resolve_model(db, default_provider, default_model)
        use_model = default_model
    else:
        raise ValueError("provider 与 model 必须同时提供或同时留空")

    persona = await agent_service.compose_persona(db, agent)
    runtime = await app_settings_service.get_agent_runtime(db)
    if locale is None:
        effective_locale = runtime["output_locale"]
    else:
        effective_locale = locale
    if effective_locale == "zh":
        hint = "请始终使用简体中文回答。"
        persona = hint if persona == "" else f"{persona}\n\n{hint}"
    elif effective_locale == "en":
        hint = "Always answer in English."
        persona = hint if persona == "" else f"{persona}\n\n{hint}"

    tool_bundle = await build_agent_tool_bundle(
        db,
        agent,
        web_search_enabled=web_search_enabled,
    )

    # 试跑不落库：一次性内存会话，跑完即弃
    session_service = InMemorySessionService()
    session_id = f"playground-{uuid4().hex}"
    user_id = "playground"

    async for frame in stream_adk_turn(
        session_id=session_id,
        user_id=user_id,
        user_text=content,
        provider_id=llm.provider_id,
        model=use_model,
        base_url=llm.base_url,
        api_key=llm.api_key,
        session_service=session_service,
        agent_name=agent.name,
        agent_description=agent.description,
        persona_instruction=persona,
        thinking_enabled=thinking_enabled,
        extra_tools=tool_bundle.extra_tools,
        llm_timeout_seconds=int(runtime["llm_timeout_seconds"]),
        confirm_tool_names=tool_bundle.confirm_tool_names,
        model_prefix=llm.model_prefix,
        provider_extra_body=llm_config.provider_extra_body(llm),
        disallowed_tool_names=tool_bundle.disallowed_tool_names,
        max_turns=agent.max_turns,
        knowledge_scope=agent.knowledge_scope,
        skills=tool_bundle.runtime_skills,
        sub_agents=None,
    ):
        yield frame
    logger.info("Playground 试跑结束 session_id=%s model=%s", session_id, use_model)
