"""上下文压缩治理：有效阈值解析 + ADK EventsCompactionConfig 构造。

纯逻辑单测：不依赖外部服务，验证覆盖优先级、关闭语义与 ADK 配置约束。
"""

from __future__ import annotations

from app.adk_runtime.context_compaction import (
    CONTEXT_COMPACTION_EVENT_RETENTION_SIZE,
    DEFAULT_CONTEXT_COMPACTION_TOKEN_THRESHOLD,
    build_events_compaction_config,
    resolve_context_compaction_threshold,
)


def test_resolve_precedence() -> None:
    """助理覆盖 > 全局默认 > 内置默认；0 是有效的显式关闭值。"""
    assert resolve_context_compaction_threshold(500_000, 400_000) == 500_000
    assert resolve_context_compaction_threshold(None, 400_000) == 400_000
    assert resolve_context_compaction_threshold(0, 400_000) == 0  # 助理显式关闭
    assert (
        resolve_context_compaction_threshold(None, None)
        == DEFAULT_CONTEXT_COMPACTION_TOKEN_THRESHOLD
    )
    assert resolve_context_compaction_threshold(None, 0) == 0  # 全局关闭


def test_build_config_enabled() -> None:
    """正阈值构造 ADK 配置：只用 token_threshold 触发器 + 成对 retention。"""
    cfg = build_events_compaction_config(400_000)
    assert cfg is not None
    assert cfg.token_threshold == 400_000
    assert cfg.event_retention_size == CONTEXT_COMPACTION_EVENT_RETENTION_SIZE
    # 不暴露 sliding-window 触发器（invocation 后才跑，对防溢出无用）
    assert cfg.compaction_interval is None
    assert cfg.overlap_size is None


def test_build_config_disabled() -> None:
    """0 / 负数 / None 一律关闭，返回 None（不挂到 App）。"""
    assert build_events_compaction_config(0) is None
    assert build_events_compaction_config(-1) is None
    assert build_events_compaction_config(None) is None
