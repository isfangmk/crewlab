"""对话创建 / 安装技能的三个 FunctionTool 装配器。

风格对齐 web_search_tool.build_web_search_tool：用 google.adk.tools.FunctionTool
包 async 闭包，db 与归属信息在装配时闭包注入。工具内部不抛穿异常——
service / 下载 / 落库的异常统一转成结构化 error 结果给模型，可自行修正重试。
"""

from __future__ import annotations

import logging

from google.adk.tools import FunctionTool
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.skills import skill_install_service

logger = logging.getLogger(__name__)


def _error_result(tool_name: str, exc: Exception) -> dict:
    """工具异常转错误结果（对齐 agent_factory.tool_error_as_result 风格）。"""

    reason = str(exc) or type(exc).__name__
    logger.warning("技能工具执行失败 tool=%s err=%s", tool_name, exc)
    return {
        "error": (
            f"工具 {tool_name} 执行失败：{reason}。"
            "请按报错原因修正参数后重试，或改用其他方式继续。"
        )
    }


def build_create_skill_tool(db: AsyncSession, *, owner_user_id: int | None) -> FunctionTool:
    """对话新建技能工具。"""

    async def create_skill(
        key: str,
        description: str,
        instructions: str,
        risk: str,
        group: str,
        preapproved_tools: str = "",
    ) -> dict:
        """按用户描述新建一个平台技能（需人工审批）。

        Args:
            key: 技能唯一标识，小写 kebab-case，如 my-new-skill，最长 64 字符。
            description: 技能描述（说明它做什么、何时用）。
            instructions: 技能正文（SKILL.md 的 L2 指令）。
            risk: 风险等级，safe / mutating / dangerous 之一。
            group: 技能分组 key（如 knowledge / cloud / other）。
            preapproved_tools: 预批准工具名，空格分隔，可留空。
        """

        try:
            return await skill_install_service.create_skill_from_chat(
                db,
                key=key,
                description=description,
                instructions=instructions,
                risk=risk,
                group=group,
                preapproved_tools=preapproved_tools,
                owner_user_id=owner_user_id,
            )
        except Exception as exc:  # noqa: BLE001 — 异常转结构化结果，不打断回合
            return _error_result("create_skill", exc)

    return FunctionTool(create_skill)


def build_install_skill_from_url_tool(
    db: AsyncSession, *, owner_user_id: int | None
) -> FunctionTool:
    """从网址安装技能工具（zip 直链 / git 仓库）。"""

    async def install_skill_from_url(
        url: str,
        risk: str = "",
        group: str = "",
    ) -> dict:
        """从网址下载并安装一个技能包（需人工审批）。

        Args:
            url: zip 直链或 git 仓库地址（github / gitlab 等），仅支持 http/https。
            risk: 技能包未声明风险等级时必填，safe / mutating / dangerous 之一。
            group: 技能包未声明分组时必填，技能分组 key。
        """

        try:
            return await skill_install_service.install_skill_from_url(
                db,
                url=url,
                risk=risk or None,
                group=group or None,
                owner_user_id=owner_user_id,
            )
        except Exception as exc:  # noqa: BLE001 — 异常转结构化结果，不打断回合
            return _error_result("install_skill_from_url", exc)

    return FunctionTool(install_skill_from_url)


def build_install_skill_from_zip_tool(
    db: AsyncSession, *, session_id: str, owner_user_id: int | None
) -> FunctionTool:
    """从对话 zip 附件安装技能工具。"""

    async def install_skill_from_zip(
        attachment_id: str,
        risk: str = "",
        group: str = "",
        tool_context=None,
    ) -> dict:
        """把本轮对话上传的 zip 附件安装为技能（需人工审批）。

        Args:
            attachment_id: 对话附件的 id（前端上传 zip 后随消息返回）。
            risk: 技能包未声明风险等级时必填，safe / mutating / dangerous 之一。
            group: 技能包未声明分组时必填，技能分组 key。
        """

        # 审批续跑时 build_agent_tool_bundle 会在新请求里重建，闭包里的 session_id
        # 可能只是上下文默认值；执行期从 ADK tool_context 取真实会话 id 更可靠。
        sid = session_id
        live_session = getattr(tool_context, "session", None)
        if live_session is not None:
            live_id = getattr(live_session, "id", "")
            if isinstance(live_id, str) and live_id != "":
                sid = live_id
        try:
            return await skill_install_service.install_skill_from_zip(
                db,
                attachment_id=attachment_id,
                session_id=sid,
                risk=risk or None,
                group=group or None,
                owner_user_id=owner_user_id,
            )
        except Exception as exc:  # noqa: BLE001 — 异常转结构化结果，不打断回合
            return _error_result("install_skill_from_zip", exc)

    return FunctionTool(install_skill_from_zip)
