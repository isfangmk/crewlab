"""用 ADK 装配 LlmAgent；禁止自研 agent loop。"""

from __future__ import annotations

import logging
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from app.adk_runtime.naming import root_adk_name
from app.adk_runtime.tool_approval import build_before_tool_callback
from app.adk_runtime.tool_result_budget import build_after_tool_callback
from app.adk_runtime.toolset import build_builtin_tools
from app.models.llm import LlmProvider
from app.services.knowledge.knowledge_scope import KnowledgeScope
from app.services.platform.llm_config import compose_litellm_model, provider_extra_body
from app.services.skills.skills_service import build_skill_toolset

logger = logging.getLogger(__name__)

# 工作流节点单步耗时上限：节点本身也有 timeout_seconds，这里给模型一个兜底值。
WORKFLOW_LLM_TIMEOUT_SECONDS = 180


async def tool_error_as_result(
    tool: BaseTool,
    args: dict[str, Any],  # pyright: ignore[reportUnusedParameter] — ADK 契约参数
    tool_context: ToolContext,  # pyright: ignore[reportUnusedParameter] — ADK 契约参数
    error: Exception,
) -> dict[str, Any]:
    """工具异常转错误结果：带原因回给模型、可重试，不打断本轮执行。

    ADK 只对 MCP 工具内置了异常兜底；其余工具抛错会一路冒泡终止整个
    agent 回合（对话轮次 / worker 一路）。这里在装配入口统一兜底，
    覆盖所有对话、子代理与排查 worker。
    """

    name = getattr(tool, "name", "") or "工具"
    reason = str(error) or type(error).__name__
    logger.warning("工具执行失败 name=%s err=%s", name, error)
    return {
        "error": (
            f"工具 {name} 执行失败：{reason}。"
            "请按报错原因修正参数后重试，或改用其他线索继续。"
        )
    }

CHAT_AGENT_INSTRUCTION = (
    "你是 AI 工作台助手。"
    "当你自己逐步调用工具落实时，面向用户的实质调用按「说明 → 调用 → 结果说明 → 下一说明 → 下一调用」推进，禁止连发后补写；"
    "工具检索、加载等内务操作静默进行，不逐一向用户说明。"
    "可操作诉求优先调用工具落实；工具都列在可见清单里，按名直接调用，"
    "禁止发明清单外的工具名。MCP 工具按需用清单里的 mcp__<服务>__search_tools 检索、"
    "mcp__<服务>__load_tool 加载；仅匹配 skill 描述时才 load_skill。"
    "所有图都用 run_python 产出，不要在正文里写图表配置、图数据或 mermaid 源码。"
    "数据类图表（折线/柱状/饼图等）用 run_python 的 chart() 助手产出图表产出物；"
    "流程图/架构图/时序图/状态图等结构关系用 run_python 的 emit_mermaid(source, title) 助手产出，"
    "source 是符合 mermaid 语法的图定义文本（如 \"flowchart TD\\nA-->B\"、\"sequenceDiagram\"），界面会渲染成图；"
    "只在确实有助于理解结构关系时画图，简单关系用文字即可，不要每段都画图。"
    "动手前先对照可见工具清单确认能力，不要没看清单就向用户追问澄清。"
    "你还可以把目标拆成多个子目标、交给独立节点分别推进：submit_task_graph 一次性下发整张任务图，"
    "每个节点写明具体的任务动作与所需工具，depends_on 留空表示与其它起点同时开始、"
    "填写前驱 id 表示等这些前驱完成后再开始，多个前驱即汇合、单向衔接即串行；"
    "图以草稿呈现，用户确认后各节点独立执行并自动汇总进对话。"
    "选择用图还是直接查询、盘点工具能力、如何拆解与组织草稿都是你的内部决策，"
    "正文只写与用户诉求直接相关的结论和必要过程，禁止交代这些内部决策、禁止复述草稿的节点安排与规划思路——"
    "草稿本身会作为卡片呈现给用户，无需在正文里再描述一遍。"
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
    "```suggestions\n这个异常下一步怎么查？\n能把证据展开看看吗？\n```\n"
    "给 2-3 个用户接下来最可能自己输入的问题，一行一个、简短疑问句，不重复已答内容；"
    "每条必须写成用户会用**用户自己的口吻、亲自输入给助理的原话**（如上例），"
    "禁止用助理/模型的口吻（如「我可以帮你查…」「要不要我导出…」「我能分析…」那样替用户提议帮忙）；"
    "每条紧扣本次回答里的具体结论或未决项深挖（如某个异常项的下一步、某个判断的证据），"
    "但深挖内容仍要由用户发问，而不是由助理提议提供帮忙；"
    "禁止问执行机制类元问题（耗时多久、执行完会怎样、能不能改步骤），禁止跳到与本次回答无关的主题；"
    "若本次回答只是呈现待确认的草稿、尚未产生任何分析结论，就不输出该块；"
    "没有自然的追问就不输出该块。该块是界面渲染用的元数据，不算正文，"
    "输出格式要求只约束围栏块之前的回答；若输出格式要求严格 JSON 或纯清单，"
    "则省略该块、格式优先。被委派的子任务结果不要带该块。"
)


def apply_thinking_extra(
    extra_body: dict[str, Any], *, thinking_enabled: bool, provider_id: str
) -> dict[str, Any]:
    """按对话开关改写思考相关的厂商请求体字段（就地修改并返回）。

    thinking（DashScope 形态）：extra_body 声明了就按开关改写；custom 未声明也注入。
    reasoning_effort（OpenAI 形态）：声明了同样按开关改写；custom 注入开=medium、
    关=none。两种形态并注入，网关认哪种由它自己定；测试用本函数构造探针体。
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
    # 工具结果回喂模型的字符上限；None = 内置默认。所有工具/路径统一收口。
    max_tool_result_chars: int | None = None,
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
        "persona_len=%s confirm_tools=%s max_tool_result_chars=%s",
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
        max_tool_result_chars,
    )
    llm = LiteLlm(
        model=litellm_model,
        api_base=base_url,
        api_key=api_key,
        timeout=llm_timeout_seconds,
        extra_body=extra_body if extra_body else None,
    )
    from app.services.agent.agent_service import PLATFORM_AGENT_NAME

    is_platform_agent = agent_name == PLATFORM_AGENT_NAME
    tools: list[Any] = (
        list(build_builtin_tools(include_ask_user=include_ask_user))
        if include_builtin_tools and not is_platform_agent
        else []
    )
    # SkillToolset(EAGER)：目录可见，正文仍 load_skill 按需加载。
    # 平台助手不挂技能（只挂两个元工具，避免工具爆炸与越权面）。
    if not is_platform_agent:
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
        on_tool_error_callback=tool_error_as_result,
        # 工具结果大小治理：所有工具结果在变成 FunctionResponse 回喂模型前
        # 统一截断，防止超大结果撑爆上下文打断对话。
        after_tool_callback=build_after_tool_callback(max_tool_result_chars),
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
