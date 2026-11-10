"""对话相关请求/响应契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PageContextIn(BaseModel):
    """全局助理引用的当前页快照。

    只接受页面自己声明的结构化字段（page + fields），
    不接受任意 DOM 文本、URL 或表单值。
    """

    model_config = ConfigDict(extra="forbid")

    page: str = Field(min_length=1)
    fields: dict[str, str] = Field(default_factory=dict)


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    # 全局助理引用的当前页快照；落库后会话才能复现这次引用
    page_context: PageContextIn | None = None


class ChatSessionOut(BaseModel):
    id: str
    title: str
    provider: str
    model: str
    agent_id: str | None
    created_at: str
    updated_at: str
    # 引用了页面快照的会话；没有引用时为 None
    page_context: PageContextIn | None = None
    pinned: bool = False
    archived: bool = False
    # 列表摘要：正文消息数（仅 user/assistant，不含工具卡）与最近一条正文（已截断）
    message_count: int = 0
    # 对话轮数：用户提问次数（一问一答算一轮）
    turn_count: int = 0
    # 工具 / 技能调用次数：来自 observation_turns 按回合记账，不是消息行数
    tool_call_count: int = 0
    skill_call_count: int = 0
    # 最后一条消息时间；发消息不会 touch 会话行，和 updated_at 不是一回事
    last_message_at: str | None = None
    last_message_preview: str | None = None
    # 运行信号：累计 tokens 与最近一回合状态（running/ok/error/stopped）；无观测时为 None
    total_tokens: int | None = None
    last_turn_status: str | None = None
    # 该会话当前待人工确认的工具调用数
    pending_approvals: int = 0
    # 会话里的图片附件数
    attachment_count: int = 0
    # 是否存在未过期的只读分享
    shared: bool = False


class UpdateSessionFlagsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pinned: bool | None = None
    archived: bool | None = None


class BatchDeleteSessionsRequest(BaseModel):
    """批量硬删除会话：ids 必须全部存在，否则整批失败。"""

    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(min_length=1, max_length=100)


class ForkSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 复制到该消息（含）为止；必须是本会话已有消息 id
    up_to_message_id: str = Field(min_length=1)


class TruncateAndResendRequest(BaseModel):
    """编辑用户消息：删除该消息之后的内容，并用新正文重发。"""

    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    thinking_enabled: bool
    web_search_enabled: bool
    attachment_ids: list[str] = Field(default_factory=list)


class CreateShareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 过期小时数，必须显式给出
    expires_in_hours: int = Field(ge=1, le=24 * 30)


class ChatMessageOut(BaseModel):
    id: str
    role: str
    kind: str
    content: str | None = None
    tool_call: dict | None = None
    created_at: str


class MentionIn(BaseModel):
    """一条 @ 提及。type 只能是工作台真实存在的对象类型。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["file", "server"]
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)


class StreamMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)
    provider: str | None = None
    model: str | None = None
    # 前端乐观用户气泡的 id；落库复用，避免轮询后整段重挂载
    client_message_id: str | None = None
    thinking_enabled: bool
    web_search_enabled: bool
    attachment_ids: list[str]
    # 本回合输出语言覆盖（覆盖设置页 agent.output_locale）；只接受 zh / en
    locale: str | None = None
    # @ 提及对象；后端按 id 反查真实对象并注入上下文
    mentions: list[MentionIn] = Field(default_factory=list)


class UpdateSessionTitleRequest(BaseModel):
    """重命名会话：只接受 title，与前端 renameSession 的请求体一致。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)


class UpdateSessionModelRequest(BaseModel):
    """切换会话模型：provider + model 必须成对出现。"""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)


class UpdateSessionAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)


class LLMProviderOut(BaseModel):
    id: str
    label: str
    models: list[str]
    model: str | None = None


class ModelCatalogOut(BaseModel):
    providers: list[LLMProviderOut]
    default: dict[str, str]
