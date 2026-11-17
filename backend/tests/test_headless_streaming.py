"""headless SSE 流式：真实 LLM 验证逐字增量与终包去重。

流式汇总要逐字推正文，必须验证两件事：真能拿到多个 partial 增量，
且 ADK 终包对整段的回放被跳过（否则正文翻倍）。用真实 deepseek 跑一个
最小回合，不写库、不 mock；断言 delta 拼接与最终结论一致。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.adk_runtime.agent_factory import build_chat_agent
from app.adk_runtime.headless import WorkerBudget, run_headless_turn


@pytest.mark.asyncio
async def test_headless_streaming_deltas_match_conclusion() -> None:
    db_path = Path(__file__).resolve().parents[1] / "data" / "ai_workbench.db"
    db = sqlite3.connect(db_path)
    try:
        row = db.execute(
            "SELECT api_key, base_url, model_prefix FROM llm_providers "
            "WHERE provider_id='deepseek' AND enabled=1 LIMIT 1"
        ).fetchone()
    finally:
        db.close()
    if row is None:
        pytest.skip("无可用 deepseek provider")
    api_key, base_url, model_prefix = row

    agent = build_chat_agent(
        provider_id="deepseek",
        model="deepseek-flash",
        base_url=base_url,
        api_key=api_key,
        agent_name="verify-stream",
        agent_description="流式验证",
        persona_instruction="直接输出正文，不要工具、不要 JSON、不要输出思考过程。",
        thinking_enabled=False,
        model_prefix=model_prefix,
        include_builtin_tools=False,
        include_ask_user=False,
        include_followups=False,
        skills=[],
        knowledge_scope=None,
    )

    deltas: list[str] = []

    async def on_delta(delta: str, partial: bool) -> None:
        deltas.append(delta)

    async def log(line: str, tool: str = "") -> None:
        pass

    result = await run_headless_turn(
        agent=agent,
        app_name="verify_stream",
        prompt="用三句话介绍北京的秋天，每句独立一行。",
        budget=WorkerBudget(max_steps=2, timeout_seconds=60, token_budget=20000),
        log=log,
        stop_check=lambda: False,
        opening="验证开始",
        closing="验证结束",
        on_text_delta=on_delta,
    )

    assert result.status == "succeeded", result.error
    # 真流式：拿到多个增量，而不是一次性整段
    assert len(deltas) > 1, "SSE 模式未产生逐字增量"
    # 终包去重后：增量拼接 == 最终落库正文（不多不少，正文不翻倍）
    assert "".join(deltas) == result.conclusion
