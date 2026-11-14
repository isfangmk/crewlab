"""ask_user：对话内澄清交互——暂停回合，让用户选择选项或填写信息。

暂停/恢复复用审批骨架：before_tool_callback 命中 ask_user 时走 ADK
request_confirmation；用户答案经 ToolConfirmation.payload 随确认响应
回流（ADK 原生通道），工具恢复执行时读到答案并返回给模型。
"""

from __future__ import annotations

from typing import Any

ASK_USER_TOOL_NAME = "ask_user"

_ANSWER_KEYS = {"selected", "values", "text"}


def coerce_ask_display(raw: dict[str, Any]) -> dict[str, Any]:
    """把 ask_user 实参整理成卡片可渲染的形状（尽力而为，不抛错）。

    模型给出的实参不可信，落库信封与前端卡片都用它做兜底清洗。
    """

    question = raw.get("question")
    options_raw = raw.get("options")
    fields_raw = raw.get("fields")

    options: list[str] = []
    if isinstance(options_raw, list):
        for item in options_raw:
            text = str(item).strip()
            if text != "":
                options.append(text)

    fields: list[dict[str, Any]] = []
    if isinstance(fields_raw, list):
        for item in fields_raw:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            if not isinstance(key, str) or key == "":
                continue
            field: dict[str, Any] = {"key": key, "label": str(item.get("label") or key)}
            placeholder = item.get("placeholder")
            if isinstance(placeholder, str) and placeholder != "":
                field["placeholder"] = placeholder
            if item.get("required") is True:
                field["required"] = True
            fields.append(field)

    return {
        "question": str(question).strip() if question is not None else "",
        "options": options,
        "fields": fields,
    }


def normalize_ask_spec(
    question: Any, options: Any, fields: Any
) -> tuple[str, list[str], list[dict[str, Any]]]:
    """校验并规范化提问规格；非法规格显式报错，让模型自行修正后重试。"""

    display = coerce_ask_display(
        {"question": question, "options": options, "fields": fields}
    )
    if display["question"] == "":
        raise ValueError("question 不能为空")
    keys: set[str] = set()
    for field in display["fields"]:
        if field["key"] in keys:
            raise ValueError(f"fields 的 key 重复: {field['key']}")
        keys.add(field["key"])
    if not display["options"] and not display["fields"]:
        raise ValueError("options 与 fields 至少给一个，否则改用纯文字提问")
    return display["question"], display["options"], display["fields"]


def normalize_user_answer(raw: Any) -> dict[str, Any]:
    """把用户回答规范成 {selected, values, text}；形状非法显式报错。"""

    if not isinstance(raw, dict):
        raise TypeError("answer 必须是对象")
    unknown = sorted(set(raw) - _ANSWER_KEYS)
    if unknown:
        raise ValueError(f"answer 不接受字段: {','.join(unknown)}")
    selected = raw.get("selected")
    if selected is not None and not isinstance(selected, str):
        raise TypeError("answer.selected 必须是字符串")
    values = raw.get("values") or {}
    if not isinstance(values, dict):
        raise TypeError("answer.values 必须是对象")
    for key, value in values.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError("answer.values 必须是字符串到字符串的映射")
    text = raw.get("text") or ""
    if not isinstance(text, str):
        raise TypeError("answer.text 必须是字符串")
    return {
        "selected": selected if selected is not None else None,
        "values": dict(values),
        "text": text,
    }


def validate_answer(
    answer: dict[str, Any],
    options: list[str],
    fields: list[dict[str, Any]],
) -> None:
    """按提问规格校验回答：选项必须命中候选，必填字段不能为空。"""

    if options:
        if answer["selected"] is None or answer["selected"] == "":
            raise ValueError("请选择一个选项")
        if answer["selected"] not in options:
            raise ValueError(f"selected 不在候选选项内: {answer['selected']}")
    else:
        answer["selected"] = None
    field_keys = {field["key"] for field in fields}
    unknown_values = sorted(set(answer["values"]) - field_keys)
    if unknown_values:
        raise ValueError(f"values 含未知字段: {','.join(unknown_values)}")
    for field in fields:
        value = answer["values"].get(field["key"], "")
        if field.get("required") is True and value.strip() == "":
            raise ValueError(f"缺少必填项: {field['key']}")
    if not options and not fields and answer["text"].strip() == "":
        raise ValueError("请填写回答")


async def ask_user(
    question: str,
    options: list[str] | None = None,
    fields: list[dict] | None = None,
    tool_context: Any = None,
) -> dict:
    """暂停对话并向用户提问：让用户选择选项或填写信息，答案回流后继续。

    需要用户在候选中选择、补充具体信息或缺关键参数时使用。
    question: 向用户提出的问题（说清缺什么信息）。
    options: 候选选项列表，用户单选其一；不需要选择时省略。
    fields: 要用户填写的表单项列表，每项 {key, label, placeholder, required}。
    """

    question, options, fields = normalize_ask_spec(question, options, fields)

    confirmation = (
        getattr(tool_context, "tool_confirmation", None)
        if tool_context is not None
        else None
    )
    if confirmation is None:
        # 手动运行没有确认通道：不猜答案，显式报错
        return {"error": "ask_user 需要用户在对话卡片中作答，不支持直接运行"}
    if not getattr(confirmation, "confirmed", False):
        return {"error": "用户跳过了该问题"}
    answer_raw = getattr(confirmation, "payload", None)
    if not isinstance(answer_raw, dict):
        return {"error": "未收到用户回答，请重新提问"}

    answer = normalize_user_answer(answer_raw)
    validate_answer(answer, options, fields)
    return answer
