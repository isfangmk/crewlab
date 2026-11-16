"""用 ADK 装配 LlmAgent；禁止自研 agent loop。"""

from __future__ import annotations

import logging
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm

from app.adk_runtime.naming import root_adk_name
from app.adk_runtime.tool_approval import build_before_tool_callback
from app.adk_runtime.toolset import build_builtin_tools
from app.models.llm import LlmProvider
from app.services.knowledge.knowledge_scope import KnowledgeScope
from app.services.platform.llm_config import compose_litellm_model, provider_extra_body
from app.services.skills.skills_service import build_skill_toolset

logger = logging.getLogger(__name__)

# 工作流节点单步耗时上限：节点本身也有 timeout_seconds，这里给模型一个兜底值。
WORKFLOW_LLM_TIMEOUT_SECONDS = 180

CHAT_AGENT_INSTRUCTION = (
    "你是 AI 工作台助手。"
    "多工具必须按「说明 → 调用 → 结果说明 → 下一说明 → 下一调用」推进，禁止连发后补写。"
    "可操作诉求先用当前可见的 search/load 类工具再行动；仅匹配 skill 描述时才 load_skill。"
    "画图一律用 run_python 的 chart() 助手产出图表产出物，正文只写文字结论，"
    "禁止把 ECharts 配置或图表数据 JSON 写进正文。"
    "未检索工具前不要向用户追问澄清。"
    "澄清规则（最高优先级）：禁止把提问话术、空串、未知、占位写进工具 arguments 再调用，"
    "禁止「先调用再等人填」。需要用户在候选中选择、补充具体信息或缺工具必填实参时，"
    "调用 ask_user 提问：question 说清缺什么，能给候选就给 options，要填写就给 fields，"
    "用户在卡片作答后本轮自动继续。"
    "只有一句纯文本就能问清且没有候选可给时才直接文字提问，此时本轮 function call 数量必须为 0。"
    "ask_user 只在与用户直接对话时使用；被委派的子任务里不要向用户提问，"
    "按最合理假设继续并在结果中注明假设。"
)

# 建议问题块：前端 parseFollowups 按同一规约解析（```suggestions 围栏、末尾、一行一问）。
# 只在与用户直接对话的根 Agent 上启用（include_followups 闸门）。
FOLLOWUPS_INSTRUCTION = (
    "建议问题（仅与用户直接对话时）：每次向用户回答完毕后，末尾另起一段输出围栏块：\n"
    "```suggestions\n问题一\n问题二\n```\n"
    "给 2-3 个用户接下来最可能追问的相关问题，一行一个、简短疑问句，不重复已答内容；"
    "没有自然的追问就不输出该块。该块是界面渲染用的元数据，不算正文，"
    "输出格式要求只约束围栏块之前的回答；若输出格式要求严格 JSON 或纯清单，"
    "则省略该块、格式优先。被委派的子任务结果不要带该块。"
)


def apply_thinking_extra(
    extra_body: dict[str, Any], *, thinking_enabled: bool, provider_id: str
) -> dict[str, Any]:
    """按对话开关改写思考相关的厂商请求体字段（就地修改并返回）。

    thinking（DashScope 形态）：extra_body 声明了就按开关改写；
    custom（OpenAI 兼容）未声明时也注入，否则开关对上游无效。
    reasoning_effort（OpenAI 形态）：聚合网关只认它、忽略 thinking——实测只有它能
    开出 reasoning_content（thinking 形态被静默忽略，开关等于白开）。
    开=medium（简短推理），关=none（实测不进推理）；声明了同样按开关改写。
    测试用同一函数构造上游探针体，产品与测试形态不许再各写各的。
    """

    if "thinking" in extra_body or provider_id == "custom":
        extra_body["thinking"] = {
            "type": "enabled" if thinking_enabled else "disabled"
        }
    if "reasoning_effort" in extra_body or provider_id == "custom":
        extra_body["reasoning_effort"] = "medium" if thinking_enabled else "none"
    return extra_body


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
    # 命中的审批规则动作（wire 名 → action）：闸门据此绕过会话信任
    rule_actions: dict[str, str] | None = None,
    max_turns: int = 0,
    knowledge_scope: KnowledgeScope | None = None,
    skills: list[Any] | None = None,
    sub_agents: list[Any] | None = None,
    adk_name: str | None = None,
    include_builtin_tools: bool = True,
    include_ask_user: bool = True,
    include_followups: bool = True,
    # 自定义工具闸门（如 worker 只读拦截）；缺省走审批闸门
    before_tool_callback: Any = None,
) -> LlmAgent:
    """按配置-模型与助理人设装配 LlmAgent；extra_tools 挂 MCP SearchableToolset 等。

    adk_name 缺省时按业务助理名派生根 Agent 名；子 Agent 由委托装配显式传入。
    模型名前缀与厂商专属请求体由 provider 配置决定：
    不能对所有 provider 强加 openai/ 前缀或 DeepSeek 的 thinking 字段。
    max_turns 在此校验并记入装配日志；实际限轮由 Runner 的 RunConfig.max_llm_calls 生效。
    include_ask_user=False 用于子 Agent：澄清提问只在与用户直接对话的根 Agent 上。
    include_followups=False 用于子 Agent 与试跑台：建议问题块只在与用户直接对话时输出。
    """

    if llm_timeout_seconds < 30 or llm_timeout_seconds > 900:
        raise ValueError("llm_timeout_seconds 范围是 30..900")
    if max_turns < 0:
        raise ValueError("max_turns 不能为负数")
    if adk_name is None:
        adk_name = root_adk_name(agent_name)
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
    if include_followups:
        instruction = f"{instruction}\n\n{FOLLOWUPS_INSTRUCTION}"

    extra_body = dict(provider_extra_body) if provider_extra_body else {}
    apply_thinking_extra(extra_body, thinking_enabled=thinking_enabled, provider_id=provider_id)
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
    tools: list[Any] = (
        list(build_builtin_tools(include_ask_user=include_ask_user))
        if include_builtin_tools
        else []
    )
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
    # ADK agent 名必须是合法 Python 标识符（委托工具名由它派生）；
    # 业务助理标识只进日志与人设 instruction。
    return LlmAgent(
        name=adk_name,
        model=llm,
        description=agent_description if agent_description != "" else "AI 工作台对话智能体",
        instruction=instruction,
        tools=tools,
        before_tool_callback=(
            before_tool_callback
            if before_tool_callback is not None
            else build_before_tool_callback(
                confirm_names, disallowed_tool_names, rule_actions
            )
        ),
        sub_agents=[a for a in (sub_agents or []) if a is not None],
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
