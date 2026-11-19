"""ADK Agent 名称：业务名净化与根 Agent 命名。

ADK 要求 Agent 名是合法 Python 标识符，`user` 是保留名；业务名允许 `-`、
可以数字开头，不能直接使用。
"""

from __future__ import annotations

import re

# ADK 内部保留给终端用户消息的 author 名
_ADK_RESERVED = {"user"}


def adk_identifier(name: str) -> str:
    """业务名 → 合法 Python 标识符。"""

    cleaned = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    if cleaned == "" or cleaned[0].isdigit():
        cleaned = f"agent_{cleaned}"
    if cleaned in _ADK_RESERVED:
        cleaned = f"agent_{cleaned}"
    return cleaned


def root_adk_name(agent_name: str) -> str:
    """根 Agent 的 ADK 名：默认助理沿用历史名，其余按业务名派生。

    历史会话里 assistant 事件的 author 是 workbench_chat，默认助理保持不变；
    自定义根助理用 `agent_<业务名>`，避免与净化后的名字撞车。
    """

    if agent_name == "default":
        return "workbench_chat"
    return f"agent_{adk_identifier(agent_name)}"
