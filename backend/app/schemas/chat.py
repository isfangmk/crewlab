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

    page: str = Field(min_length=1, description="页面标识，由页面自身声明的名称，用于定位当前引用来源")
    fields: dict[str, str] = Field(default_factory=dict, description="页面声明的结构化字段，键为字段名、值为对应的文本内容")


class CreateSessionRequest(BaseModel):
    """新建一个对话会话。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, description="会话标题，至少 1 个字符")
    provider: str = Field(min_length=1, description="大模型提供方标识，如标识具体服务商")
    model: str = Field(min_length=1, description="会话使用的大模型名称")
    agent_id: str = Field(min_length=1, description="会话绑定的智能体 id")
    # 全局助理引用的当前页快照；落库后会话才能复现这次引用
    page_context: PageContextIn | None = Field(default=None, description="当前页快照，引用了页面时传入，未引用留空")
    # 「思考 / 联网搜索」开关初值（首页开关随建会话落库）
    thinking_enabled: bool = Field(default=False, description="思考模式开关初值，随建会话一并保存")
    web_search_enabled: bool = Field(default=False, description="联网搜索开关初值，随建会话一并保存")


class ChatSessionOut(BaseModel):
    """会话列表/详情中的会话摘要信息。"""

    id: str = Field(description="会话唯一 id")
    title: str = Field(description="会话标题")
    provider: str = Field(description="大模型提供方标识")
    model: str = Field(description="会话使用的大模型名称")
    agent_id: str | None = Field(default=None, description="会话绑定的智能体 id，未绑定时为 None")
    created_at: str = Field(description="会话创建时间")
    updated_at: str = Field(description="会话最近更新时间")
    # 引用了页面快照的会话；没有引用时为 None
    page_context: PageContextIn | None = Field(default=None, description="引用的当前页快照，未引用时为 None")
    pinned: bool = Field(default=False, description="会话是否被置顶")
    archived: bool = Field(default=False, description="会话是否已归档")
    # 「思考 / 联网搜索」开关（随会话持久化，换页面打开即同步）
    thinking_enabled: bool = Field(default=False, description="思考模式开关状态")
    web_search_enabled: bool = Field(default=False, description="联网搜索开关状态")
    # 列表摘要：正文消息数（仅 user/assistant，不含工具卡）与最近一条正文（已截断）
    message_count: int = Field(default=0, description="正文消息数，仅统计 user/assistant，不含工具卡")
    turn_count: int = Field(default=0, description="对话轮数，用户提问一次算一轮")
    # 工具 / 技能调用次数：来自 observation_turns 按回合记账，不是消息行数
    tool_call_count: int = Field(default=0, description="工具调用次数，按回合记账而非消息行数")
    skill_call_count: int = Field(default=0, description="技能调用次数，按回合记账而非消息行数")
    # 最后一条消息时间；发消息不会 touch 会话行，和 updated_at 不是一回事
    last_message_at: str | None = Field(default=None, description="最后一条消息的时间，与会话 updated_at 不同")
    last_message_preview: str | None = Field(default=None, description="最后一条正文消息的截断预览")
    # 运行信号：累计 tokens 与最近一回合状态（running/ok/error/stopped）；无观测时为 None
    total_tokens: int | None = Field(default=None, description="累计消耗的 token 数，无观测时为 None")
    last_turn_status: str | None = Field(default=None, description="最近一回合状态，取值 running/ok/error/stopped，无观测时为 None")
    # 该会话当前待人工确认的工具调用数（操作确认，不含澄清提问）
    pending_approvals: int = Field(default=0, description="待人工确认的工具调用数，不含澄清提问")
    # 该会话当前待回答的澄清提问数（ask_user），与审批分开计数
    pending_inputs: int = Field(default=0, description="待回答的澄清提问数（ask_user），与审批分开计数")
    # 会话里的图片附件数
    attachment_count: int = Field(default=0, description="会话中的图片附件数量")
    # 会话产出物个数：run_python 产出的图表/表格/文件
    artifact_count: int = Field(default=0, description="会话产出物数量，如 run_python 产出的图表、表格、文件")
    # 是否存在未过期的只读分享
    shared: bool = Field(default=False, description="是否存在未过期的只读分享")


class UpdateSessionFlagsRequest(BaseModel):
    """更新会话的置顶/归档标记，只更新显式提供的字段。"""

    model_config = ConfigDict(extra="forbid")

    pinned: bool | None = Field(default=None, description="是否置顶；传 null 表示不修改该项")
    archived: bool | None = Field(default=None, description="是否归档；传 null 表示不修改该项")


class UpdateSessionPrefsRequest(BaseModel):
    """「思考 / 联网搜索」开关：至少提供一个。"""

    model_config = ConfigDict(extra="forbid")

    thinking_enabled: bool | None = Field(default=None, description="思考模式开关；至少提供一项，传 null 表示不修改")
    web_search_enabled: bool | None = Field(default=None, description="联网搜索开关；至少提供一项，传 null 表示不修改")


class BatchDeleteSessionsRequest(BaseModel):
    """批量硬删除会话：ids 必须全部存在，否则整批失败。"""

    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(min_length=1, max_length=100, description="待删除会话 id 列表，1~100 个，必须全部存在否则整批失败")


class ForkSessionRequest(BaseModel):
    """从已有消息分叉出新会话。"""

    model_config = ConfigDict(extra="forbid")

    # 复制到该消息（含）为止；必须是本会话已有消息 id
    up_to_message_id: str = Field(min_length=1, description="复制截止的消息 id（含该条），必须是本会话已有消息")


class TruncateAndResendRequest(BaseModel):
    """编辑用户消息：删除该消息之后的内容，并用新正文重发。"""

    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, description="要编辑的用户消息 id，删除其后的内容并从这条重发")
    content: str = Field(min_length=1, description="替换后的新消息正文，至少 1 个字符")
    thinking_enabled: bool = Field(description="重发时使用的思考模式开关")
    web_search_enabled: bool = Field(description="重发时使用的联网搜索开关")
    attachment_ids: list[str] = Field(default_factory=list, description="重发时携带的图片附件 id 列表")


class CreateShareRequest(BaseModel):
    """创建会话的只读分享链接。"""

    model_config = ConfigDict(extra="forbid")

    # 过期小时数，必须显式给出
    expires_in_hours: int = Field(ge=1, le=24 * 30, description="分享有效期（小时），范围 1~720（30 天），必须显式给出")


class ChatMessageOut(BaseModel):
    """一条对话消息的响应表示。"""

    id: str = Field(description="消息唯一 id")
    role: str = Field(description="消息角色，如 user/assistant")
    kind: str = Field(description="消息种类，如正文、工具卡等")
    content: str | None = Field(default=None, description="消息正文；工具卡等类型的消息可能为空")
    tool_call: dict | None = Field(default=None, description="工具调用信息；非工具消息为 None")
    created_at: str = Field(description="消息创建时间")


class MentionIn(BaseModel):
    """一条 @ 提及。type 只能是工作台真实存在的对象类型。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["file", "server"] = Field(description="被提及对象类型，只能是 file（文件）或 server（服务）")
    id: str = Field(min_length=1, description="被提及对象的 id，后端据此反查真实对象")
    label: str = Field(min_length=1, description="被提及对象的展示名称")


class StreamMessageRequest(BaseModel):
    """在会话中流式发送一条用户消息。"""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, description="用户消息正文，至少 1 个字符")
    provider: str | None = Field(default=None, description="本回合覆盖的大模型提供方；留空则用会话设置")
    model: str | None = Field(default=None, description="本回合覆盖的大模型名称；留空则用会话设置")
    # 前端乐观用户气泡的 id；落库复用，避免轮询后整段重挂载
    client_message_id: str | None = Field(default=None, description="前端乐观用户气泡 id，落库时复用以免轮询后重挂载")
    thinking_enabled: bool = Field(description="本回合是否开启思考模式")
    web_search_enabled: bool = Field(description="本回合是否开启联网搜索")
    attachment_ids: list[str] = Field(description="本回合携带的图片附件 id 列表")
    # 本回合输出语言覆盖（覆盖设置页 agent.output_locale）；只接受 zh / en
    locale: str | None = Field(default=None, description="本回合输出语言，只接受 zh/en；留空则用会话默认")
    # @ 提及对象；后端按 id 反查真实对象并注入上下文
    mentions: list[MentionIn] = Field(default_factory=list, description="本回合 @ 提及的对象列表，后端按 id 反查并注入上下文")


class UpdateSessionTitleRequest(BaseModel):
    """重命名会话：只接受 title，与前端 renameSession 的请求体一致。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, description="新的会话标题，至少 1 个字符")


class UpdateSessionModelRequest(BaseModel):
    """切换会话模型：provider + model 必须成对出现。"""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, description="大模型提供方标识，须与 model 成对提供")
    model: str = Field(min_length=1, description="切换后的大模型名称，须与 provider 成对提供")


class UpdateSessionAgentRequest(BaseModel):
    """切换会话绑定的智能体。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1, description="新的智能体 id")


class LLMProviderOut(BaseModel):
    """大模型提供方及其可用模型的响应表示。"""

    id: str = Field(description="提供方标识")
    label: str = Field(description="提供方展示名称")
    models: list[str] = Field(description="该提供方下可用的大模型名称列表")
    model: str | None = Field(default=None, description="默认选中的大模型名称，无则为 None")


class ModelCatalogOut(BaseModel):
    """模型目录：全部提供方与默认配置。"""

    providers: list[LLMProviderOut] = Field(description="大模型提供方列表")
    default: dict[str, str] = Field(description="默认配置，键为提供方标识、值为默认模型名称")
