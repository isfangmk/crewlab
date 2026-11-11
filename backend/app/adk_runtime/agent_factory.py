"""用 ADK 装配 LlmAgent；禁止自研 agent loop。"""

from __future__ import annotations

import logging
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm

from app.adk_runtime.builtin_tools import build_builtin_tools
from app.adk_runtime.tool_approval import build_before_tool_callback
from app.models_orm.llm import LlmProvider
from app.services.knowledge_scope import KnowledgeScope
from app.services.llm_config import compose_litellm_model, provider_extra_body
from app.services.skills_service import build_skill_toolset

logger = logging.getLogger(__name__)

# 工作流节点单步耗时上限：节点本身也有 timeout_seconds，这里给模型一个兜底值。
WORKFLOW_LLM_TIMEOUT_SECONDS = 180

CHAT_AGENT_INSTRUCTION = (
    "你是 AI 工作台助手。"
    "多工具必须按「说明 → 调用 → 结果说明 → 下一说明 → 下一调用」推进，禁止连发后补写。"
    "可操作诉求先用当前可见的 search/load 类工具再行动；仅匹配 skill 描述时才 load_skill。"
    "未检索工具前不要向用户追问澄清。"
    "缺参规则（最高优先级）：用户本轮未给出某工具必填实参时，本轮输出只能是一段纯文字提问，"
    "且本轮 function call 数量必须为 0。"
    "禁止把提问话术、空串、未知、占位写进 arguments 再调用；"
    "禁止「先调用再等人填」；等用户下一条消息给出真实值后，下一轮再调用。"
)


def build_chat_agent(
    *,
    provider_id: str,
    model: str,
    base_url: str,
    api_key: str,
    agent_name: str,
    agent_description: str,
    persona_instruction: str,
    thinking_enabled: bool,
    extra_tools: list[Any] | None = None,
    llm_timeout_seconds: int = 120,
    confirm_tool_names: set[str] | None = None,
    model_prefix: str = "",
    provider_extra_body: dict[str, Any] | None = None,
    disallowed_tool_names: set[str] | None = None,
    max_turns: int = 0,
    knowledge_scope: KnowledgeScope | None = None,
    skills: list[Any] | None = None,
) -> LlmAgent:
    """按配置-模型与助理人设装配 LlmAgent；extra_tools 挂 MCP SearchableToolset 等。

    模型名前缀与厂商专属请求体由 provider 配置决定：
    不能对所有 provider 强加 openai/ 前缀或 DeepSeek 的 thinking 字段。
    max_turns 在此校验并记入装配日志；实际限轮由 Runner 的 RunConfig.max_llm_calls 生效。
    """

    if llm_timeout_seconds < 30 or llm_timeout_seconds > 900:
        raise ValueError("llm_timeout_seconds 范围是 30..900")
    if max_turns < 0:
        raise ValueError("max_turns 不能为负数")
    confirm_names = set() if confirm_tool_names is None else confirm_tool_names

    litellm_model = compose_litellm_model(
        provider_id=provider_id,
        model_prefix=model_prefix,
        model=model,
    )
    if persona_instruction == "":
        instruction = CHAT_AGENT_INSTRUCTION
    else:
        instruction = f"{CHAT_AGENT_INSTRUCTION}\n\n{persona_instruction}"

    extra_body = dict(provider_extra_body) if provider_extra_body else {}
    # DeepSeek 等在 extra_body 声明了 thinking：按开关改写。
    # custom（OpenAI 兼容）未声明时也按对话开关注入，否则开关对上游无效。
    if "thinking" in extra_body or provider_id == "custom":
        extra_body["thinking"] = {
            "type": "enabled" if thinking_enabled else "disabled"
        }
    logger.info(
        "装配 LlmAgent agent=%s provider=%s model=%s litellm_model=%s base_url=%s "
        "extra_tools=%s extra_body_keys=%s thinking=%s timeout=%s max_turns=%s "
        "persona_len=%s confirm_tools=%s",
        agent_name,
        provider_id,
        model,
        litellm_model,
        base_url,
        0 if extra_tools is None else len(extra_tools),
        sorted(extra_body.keys()),
        extra_body.get("thinking"),
        llm_timeout_seconds,
        max_turns,
        len(persona_instruction),
        len(confirm_names),
    )
    llm = LiteLlm(
        model=litellm_model,
        api_base=base_url,
        api_key=api_key,
        timeout=llm_timeout_seconds,
        extra_body=extra_body if extra_body else None,
    )
    tools: list[Any] = list(build_builtin_tools())
    # SkillToolset(EAGER)：目录可见，正文仍 load_skill 按需加载
    tools.append(build_skill_toolset(knowledge_scope, skills))
    if knowledge_scope is not None and not knowledge_scope.is_empty:
        logger.info(
            "装配知识范围 agent=%s paths=%s doc_ids=%s",
            agent_name,
            list(knowledge_scope.paths),
            len(knowledge_scope.doc_ids),
        )
    if extra_tools is not None:
        tools.extend(extra_tools)
    # ADK Runner 侧固定 agent 名；业务助理标识只进日志与人设 instruction。
    return LlmAgent(
        name="workbench_chat",
        model=llm,
        description=agent_description if agent_description != "" else "AI 工作台对话智能体",
        instruction=instruction,
        tools=tools,
        before_tool_callback=build_before_tool_callback(
            confirm_names, disallowed_tool_names
        ),
    )


def build_workflow_agent(
    *,
    node_id: str,
    llm: LlmProvider,
    model_name: str,
    instruction: str,
) -> LlmAgent:
    """工作流 LLM 节点：模型与指令都由节点自己声明，不依赖助理配置。

    节点只做一次模型调用，不挂工具：知识检索、联网搜索、MCP 调用在画布上
    各自有独立节点，需要工具就往后面接一个。
    """

    litellm_model = compose_litellm_model(
        provider_id=llm.provider_id,
        model_prefix=llm.model_prefix,
        model=model_name,
    )
    extra_body = provider_extra_body(llm)
    logger.info(
        "装配工作流 LLM 节点 node=%s provider=%s model=%s litellm_model=%s instruction_len=%s",
        node_id,
        llm.provider_id,
        model_name,
        litellm_model,
        len(instruction),
    )
    model = LiteLlm(
        model=litellm_model,
        api_base=llm.base_url,
        api_key=llm.api_key,
        timeout=WORKFLOW_LLM_TIMEOUT_SECONDS,
        extra_body=extra_body if extra_body else None,
    )
    return LlmAgent(
        name=node_id,
        model=model,
        description="工作流 LLM 节点",
        instruction=instruction,
    )


def build_switch_agent(
    *, node_id: str, llm: LlmProvider, model_name: str, instruction: str
) -> LlmAgent:
    """条件分支判定用的一次性 LLM：不挂工具，只做单选。"""

    litellm_model = compose_litellm_model(
        provider_id=llm.provider_id,
        model_prefix=llm.model_prefix,
        model=model_name,
    )
    extra_body = provider_extra_body(llm)
    logger.info(
        "装配分支判定 LLM node=%s model=%s litellm_model=%s",
        node_id,
        model_name,
        litellm_model,
    )
    return LlmAgent(
        name=node_id,
        model=LiteLlm(
            model=litellm_model,
            api_base=llm.base_url,
            api_key=llm.api_key,
            timeout=WORKFLOW_LLM_TIMEOUT_SECONDS,
            extra_body=extra_body if extra_body else None,
        ),
        description="工作流条件分支判定",
        instruction=instruction,
    )
