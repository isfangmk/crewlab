"""headless 工具轨迹收敛：入参/返回写轨迹前的体量控制。

轨迹会随步骤上下文传给下一步和最终汇总，超长工具返回若不收敛会撑爆 token。
这里验证收敛函数对短结构原样保留、对超长结构截断且不断言内容丢失。
"""

from __future__ import annotations

from app.adk_runtime.headless import _TRACE_VALUE_LIMIT, HeadlessResult, _trace_value


def test_trace_value_keeps_short_structure() -> None:
    value = {"workflows": [{"id": 45, "nodes": 4}], "total": 7}
    assert _trace_value(value) == value


def test_trace_value_truncates_long_text() -> None:
    long = "x" * (_TRACE_VALUE_LIMIT + 100)
    out = _trace_value(long)
    assert isinstance(out, str)
    assert len(out) == _TRACE_VALUE_LIMIT
    assert out == "x" * _TRACE_VALUE_LIMIT


def test_trace_value_truncates_long_dict_to_text() -> None:
    value = {"blob": "y" * (_TRACE_VALUE_LIMIT + 500)}
    out = _trace_value(value)
    assert isinstance(out, str)
    assert out.endswith("…(truncated)")


def test_headless_result_has_tool_trace() -> None:
    result = HeadlessResult(status="succeeded")
    assert result.tool_trace == []
