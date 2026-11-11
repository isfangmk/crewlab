"""工作流变量插值：把 {{变量}} 替换成共享状态里的值。

画布上的连线只表达控制流，数据靠这里的共享状态传递。插值必须显式失败：
引用了不存在的变量就报错，不静默替换成空串。
"""

from __future__ import annotations

import json
import re
from typing import Any

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
# 整个字符串就是一个占位符时，保留原始类型（列表/对象/数字），供参数表使用。
_WHOLE = re.compile(r"^\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}$")


class MissingVariableError(KeyError):
    """引用的变量在当前状态里不存在。"""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name
        self.message = f"变量未定义或尚未产生: {name}"
        super().__init__(self.message)

    def __str__(self) -> str:
        return self.message


def _lookup(state: dict[str, Any], name: str) -> Any:
    if name not in state:
        raise MissingVariableError(name)
    return state[name]


def _to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def render(text: str, state: dict[str, Any]) -> str:
    """把字符串里的 {{变量}} 全部替换成文本；缺变量直接报错。"""

    def _replace(match: re.Match[str]) -> str:
        return _to_text(_lookup(state, match.group(1)))

    return _PLACEHOLDER.sub(_replace, text)


def render_value(text: str, state: dict[str, Any]) -> Any:
    """单值插值：整串是一个占位符时返回原始类型，否则按文本处理。"""

    whole = _WHOLE.match(text)
    if whole is not None:
        return _lookup(state, whole.group(1))
    return render(text, state)


def render_map(values: dict[str, str], state: dict[str, Any]) -> dict[str, Any]:
    """参数表逐个插值，值的类型跟随变量本身。"""

    return {key: render_value(text, state) for key, text in values.items()}


def resolve_conditional(text: str, state: dict[str, Any]) -> bool | None:
    """把条件表达式求值成布尔。

    支持的最小语法（够画布用，不引入表达式引擎）：
      - `{{var}}`            → 真值判断
      - `{{var}} > 数字`     → 数值比较（> >= < <= == !=）
      - `{{var}} == 文本`    → 相等比较
    无法识别时显式报错，不猜。
    """

    stripped = text.strip()
    if stripped == "":
        raise ValueError("条件表达式不能为空")
    for op in (">=", "<=", "==", "!=", ">", "<"):
        if op in stripped:
            left_raw, right_raw = stripped.split(op, 1)
            left = render_value(left_raw.strip(), state)
            right_text = right_raw.strip()
            right = render_value(right_text, state) if right_text.startswith("{{") else right_text
            return _compare(left, right, op)
    return bool(render_value(stripped, state))


def _compare(left: Any, right: Any, op: str) -> bool:
    if op == "==":
        return bool(_to_text(left) == _to_text(right))
    if op == "!=":
        return bool(_to_text(left) != _to_text(right))
    left_num = _as_number(left)
    right_num = _as_number(right)
    if op == ">":
        return left_num > right_num
    if op == ">=":
        return left_num >= right_num
    if op == "<":
        return left_num < right_num
    return left_num <= right_num


def _as_number(value: Any) -> float:
    if isinstance(value, bool):
        raise TypeError("布尔值不能参与数值比较")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError as exc:
            raise ValueError(f"不是数字，无法比较: {value!r}") from exc
    raise TypeError(f"不支持比较的类型: {type(value).__name__}")
