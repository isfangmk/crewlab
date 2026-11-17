"""计划收尾汇总的上下文组装：纯函数层验证证据传递与提示词形状。

收尾汇总要从步骤的完整证据（conclusion/findings/tool_trace）写最终回答。
这里测组装逻辑本身，不跑 LLM；执行缝由 test_plan_service 的打桩用例覆盖。
"""

from __future__ import annotations

from app.services.plan.plan_summary import build_summary_prompt
from app.services.plan.step_runner import build_plan_step_prompt, render_prior_context


def _step(name: str, conclusion: str, node_count: int) -> dict:
    return {
        "name": name,
        "conclusion": conclusion,
        "findings": [
            {
                "title": "工作流拓扑",
                "detail": "7 个工作流共 22 节点 15 连线",
                "evidence": [
                    {"call_id": "c-1", "tool": "list_workflows", "excerpt": f"nodes={node_count}"}
                ],
            }
        ],
        "tool_trace": [
            {
                "call_id": "c-1",
                "name": "list_workflows",
                "args": {"workspace": "crewlab"},
                "response": {"workflows": [{"id": 45, "nodes": 4}], "total": node_count},
            }
        ],
    }


def test_render_prior_context_includes_findings_and_tool_data() -> None:
    lines = render_prior_context([_step("盘点工作流", "7 个工作流均为 draft", 7)])
    text = "\n".join(lines)
    assert "[盘点工作流]" in text
    assert "7 个工作流均为 draft" in text
    assert "工作流拓扑" in text
    assert "call_id=c-1" in text
    assert "list_workflows" in text
    assert "nodes" in text


def test_build_step_prompt_uses_full_context_not_bare_summary() -> None:
    prompt = build_plan_step_prompt(
        question="查看工作流并绘图",
        name="绘图展示",
        instruction="画柱状图",
        prior_summaries=[_step("盘点工作流", "7 个工作流均为 draft", 7)],
    )
    assert "查看工作流并绘图" in prompt
    assert "绘图展示" in prompt
    assert "工具数据" in prompt
    assert "list_workflows" in prompt


def test_build_summary_prompt_feeds_all_evidence() -> None:
    steps = [
        _step("盘点工作流", "7 个工作流均为 draft", 7),
        _step("绘图展示", "节点数对比图", 22),
    ]
    prompt = build_summary_prompt(question="查看工作流并绘图", steps=steps)
    assert "查看工作流并绘图" in prompt
    assert "[盘点工作流]" in prompt
    assert "[绘图展示]" in prompt
    assert "22 节点 15 连线" in prompt
    assert "chart()" in prompt
    assert "findings" in prompt
