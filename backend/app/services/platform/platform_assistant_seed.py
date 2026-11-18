"""平台助手种子：启动时幂等创建「平台助手」助理。

按助理名装配（见 agent_tool_service），只有名叫 platform-assistant 的助理会挂上
两个元工具。这个种子让平台助手开箱可用；已存在时不覆盖，用户可自行修改人设/模型。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.adk_runtime.platform_api_tools import PLATFORM_AGENT_NAME
from app.services.agent.agent_service import create_user_agent

logger = logging.getLogger(__name__)

_DISPLAY_NAME = "平台助手"
_DESCRIPTION = "代替用户在平台上完成查询、新增、修改、删除等操作，权限与本人直接操作一致"
_WHEN_TO_USE = "需要批量或在对话里完成平台上的管理操作时使用"

# 能力边界：只说能做什么、约束是什么，不写"什么提问用哪个工具"的路由规则——
# 工具选择由模型按用户提问自行判断（项目硬规则）。
_SYSTEM_PROMPT = (
    "你是 AI 工作台的平台助手，代替用户完成平台上的查询与管理工作。"
    "你可以检索平台提供的各项操作，按用户意图执行查询或变更；"
    "所有操作都以当前登录用户身份进行，权限与该用户在页面上直接操作完全相同，"
    "没有越权能力——用户无权访问的接口，你也访问不到。"
    "新增、修改、删除等变更操作需要用户确认后才会执行，查询操作直接执行。"
    "执行前先确认用户意图与关键参数（如目标资源的标识、要改成的取值）明确；"
    "信息不足时向用户澄清，不要臆造标识或填入占位值。"
    "操作完成后，用一句话说明做了什么、结果如何；失败时如实说明原因。"
)

_CRITICAL_REMINDER = (
    "绝不使用超出当前用户权限的操作；关键参数不确定时先问用户，不要猜。"
    "变更操作的确认结果以用户为准，不要替用户决定。"
)


async def ensure_platform_assistant(db: AsyncSession) -> None:
    """幂等创建平台助手；已存在则不动（用户可能已改过配置）。"""
    from app.models.agent import UserAgent

    existing = await db.get(UserAgent, PLATFORM_AGENT_NAME)
    if existing is not None:
        return
    try:
        await create_user_agent(
            db,
            name=PLATFORM_AGENT_NAME,
            display_name=_DISPLAY_NAME,
            description=_DESCRIPTION,
            when_to_use=_WHEN_TO_USE,
            system_prompt=_SYSTEM_PROMPT,
            critical_reminder=_CRITICAL_REMINDER,
            allowed_tools=[],
            disallowed_tools=[],
            permission_mode="",
            model="",
            max_turns=0,
            output_format="free",
            skills=[],
        )
        logger.info("平台助手已创建 name=%s", PLATFORM_AGENT_NAME)
    except ValueError as exc:
        # 并发启动或被改名占用：记录后跳过，不阻断启动
        logger.warning("平台助手种子跳过 name=%s err=%s", PLATFORM_AGENT_NAME, exc)
