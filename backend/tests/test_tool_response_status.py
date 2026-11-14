"""工具响应状态映射：成功信封里的空 error 不能被当成失败。"""

from app.adk_runtime.stream_bridge import tool_response_status


def test_success_envelope_with_empty_error() -> None:
    assert tool_response_status({"status": "succeeded", "error": ""}) == "success"


def test_error_text_marks_failure() -> None:
    assert tool_response_status({"error": "执行失败"}) == "error"


def test_non_dict_response_is_success() -> None:
    assert tool_response_status("ok") == "success"
