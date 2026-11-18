"""上下文压缩治理：按 token 阈值触发 ADK 事件压缩，防多轮工具结果撑爆上下文。

背景：即便工具结果已在 after_tool_callback 逐字符串收敛（见 tool_result_budget），
长会话里多轮工具结果的累积仍可能逼近模型上下文上限，导致 400 context length
exceeded 打断对话。ADK 官方提供 App.events_compaction_config（EventsCompactionConfig），
在每次模型调用前把超出阈值的历史事件压成摘要，是唯一官方收口点。

设计：
- 有效阈值 = 助理级覆盖 > 全局默认 > 内置默认。None = 继承；<=0 = 明确关闭。
- 只启用 token_threshold 触发器（模型调用前即时生效，能防溢出）；不暴露
  sliding-window（invocation 后才跑，对防溢出无用）。
- token_threshold 与 event_retention_size 必须成对设置（ADK 校验硬约束）：
  前者是触发阈值，后者压缩时保留的最近原始事件数。
- build_events_compaction_config(threshold) 返回 ADK 配置对象或 None（关闭）。
  调用方把它挂到 App.events_compaction_config，经 Runner(app=...) 生效。
"""

from __future__ import annotations

from google.adk.apps._configs import EventsCompactionConfig

# 内置兜底：0 = 关闭压缩。全局默认种子给一个正数（见 app_settings_service），
# 老库缺键或显式关闭时走这里 → 不压缩，行为与启用前一致。
DEFAULT_CONTEXT_COMPACTION_TOKEN_THRESHOLD = 0
# 压缩时保留的最近原始事件数：保留最近若干轮原文，避免摘要切断当前工作上下文。
CONTEXT_COMPACTION_EVENT_RETENTION_SIZE = 6


def resolve_context_compaction_threshold(
    agent_override: int | None, global_default: int | None
) -> int:
    """按 助理覆盖 > 全局默认 > 内置默认 解析有效阈值。

    None = 未设置（继承下一级）；<=0 是有效值，表示显式关闭压缩。
    """
    for candidate in (agent_override, global_default):
        if isinstance(candidate, int):
            return candidate
    return DEFAULT_CONTEXT_COMPACTION_TOKEN_THRESHOLD


def build_events_compaction_config(token_threshold: int | None) -> EventsCompactionConfig | None:
    """阈值 > 0 时构造 ADK 事件压缩配置；<=0 / None 返回 None（关闭）。

    只用 token_threshold 触发器：它在模型调用前的 CompactionRequestProcessor
    里即时压缩，能真正防住上下文溢出。event_retention_size 是 ADK 要求的成对参数。
    """
    if not isinstance(token_threshold, int) or token_threshold <= 0:
        return None
    return EventsCompactionConfig(
        token_threshold=token_threshold,
        event_retention_size=CONTEXT_COMPACTION_EVENT_RETENTION_SIZE,
    )
