"""工作流变量插值：把 {{变量}} 替换成共享状态里的值。

画布上的连线只表达控制流，数据靠这里的共享状态传递。插值必须显式失败：
引用了不存在的变量就报错，不静默替换成空串。

变量支持逐层读取 JSON 字段：`{{result.items[0].name}}`。这里只做
「对象取键 / 数组取下标」，不引入 JSONPath 的通配、过滤与表达式求值——
画布上的模板要可预测、可报错，而不是一门小语言。
"""

from __future__ import annotations

import json
import re
from typing import Any

# 占位符必须以变量名开头；不是变量样式的双花括号（如 JSON 片段）原样保留。
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][^{}]*?)\s*\}\}")
# 整个字符串就是一个占位符时，保留原始类型（列表/对象/数字），供参数表使用。
_WHOLE = re.compile(r"^\{\{\s*([A-Za-z_][^{}]*?)\s*\}\}$")
_PATH_ATTR = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PATH_INDEX = re.compile(r"-?\d+")


class MissingVariableError(KeyError):
    """引用的变量在当前状态里不存在。"""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name
        self.message = f"变量未定义或尚未产生: {name}"
        super().__init__(self.message)

    def __str__(self) -> str:
        return self.message


class VariablePathError(MissingVariableError):
    """变量存在，但路径上的字段/下标取不到。"""

    def __init__(self, expression: str, reason: str) -> None:
        self.name = expression
        self.message = f"变量路径无法读取: {expression}（{reason}）"
        KeyError.__init__(self, self.message)

    def __str__(self) -> str:
        return self.message


def parse_expression(expression: str) -> list[str | int]:
    """把 `result.items[0].name` 解析成 ["result", "items", 0, "name"]。

    语法：
      - 变量名开头，后接任意数量的 `.字段`、`[下标]`、`["带引号的键"]`；
      - 也接受 `.0` 这种写法，方便沿用 Jinja 风格的数组下标。
    格式不对时抛 ValueError，避免把半截表达式当成变量名静默忽略。
    """

    text = expression.strip()
    if text == "":
        raise ValueError("变量表达式不能为空")
    root = _PATH_ATTR.match(text)
    if root is None:
        raise ValueError(f"变量表达式必须以变量名开头: {expression}")
    tokens: list[str | int] = [root.group(0)]
    pos = root.end()
    while pos < len(text):
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            break
        if text[pos] == ".":
            pos += 1
            while pos < len(text) and text[pos].isspace():
                pos += 1
            attr = _PATH_ATTR.match(text, pos)
            if attr is not None:
                tokens.append(attr.group(0))
                pos = attr.end()
                continue
            index = _PATH_INDEX.match(text, pos)
            if index is not None:
                tokens.append(int(index.group(0)))
                pos = index.end()
                continue
            raise ValueError(f"变量表达式格式错误: {expression}")
        if text[pos] == "[":
            close = text.find("]", pos)
            if close == -1:
                raise ValueError(f"变量表达式缺少 ]: {expression}")
            inner = text[pos + 1 : close].strip()
            if inner == "":
                raise ValueError(f"变量表达式的中括号不能为空: {expression}")
            if _PATH_INDEX.fullmatch(inner):
                tokens.append(int(inner))
            elif len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in {'"', "'"}:
                if inner[0] == '"':
                    try:
                        tokens.append(json.loads(inner))
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"变量表达式的键名不合法: {expression}") from exc
                else:
                    tokens.append(inner[1:-1])
            else:
                raise ValueError(f"中括号里只能填数组下标或带引号的键名: {expression}")
            pos = close + 1
            continue
        raise ValueError(f"变量表达式格式错误: {expression}")
    return tokens


def _step(value: Any, token: str | int, expression: str) -> Any:
    if isinstance(value, dict):
        key = token if isinstance(token, str) else str(token)
        if key not in value:
            raise VariablePathError(expression, f"字段 {key} 不存在")
        return value[key]
    if isinstance(value, (list, tuple)):
        if not isinstance(token, int):
            raise VariablePathError(expression, f"需要数组下标，不能读取字段 {token}")
        if token >= len(value) or token < -len(value):
            raise VariablePathError(expression, f"下标 {token} 越界（长度 {len(value)}）")
        return value[token]
    if value is None:
        raise VariablePathError(expression, "上一层是 null")
    raise VariablePathError(
        expression, f"{type(value).__name__} 不支持继续取字段或下标"
    )


def resolve_expression(expression: str, state: dict[str, Any]) -> Any:
    """按路径从状态里取值；根变量不存在或路径断开都显式报错。"""

    tokens = parse_expression(expression)
    root = tokens[0]
    if not isinstance(root, str):
        raise ValueError(f"表达式根变量非法: {expression}")
    if root not in state:
        raise MissingVariableError(root)
    value = state[root]
    for token in tokens[1:]:
        value = _step(value, token, expression.strip())
    return value


def referenced_expressions(text: str) -> list[str]:
    """列出文本里引用到的变量表达式，按出现顺序去重。"""

    seen: list[str] = []
    for match in _PLACEHOLDER.finditer(text):
        expression = match.group(1).strip()
        if expression not in seen:
            seen.append(expression)
    return seen


def referenced_names(text: str) -> list[str]:
    """列出文本里引用到的根变量名，按出现顺序去重。"""

    seen: list[str] = []
    for expression in referenced_expressions(text):
        root = _PATH_ATTR.match(expression)
        if root is None:
            continue
        name = root.group(0)
        if name not in seen:
            seen.append(name)
    return seen


def _to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def render(text: str, state: dict[str, Any]) -> str:
    """把字符串里的 {{变量表达式}} 全部替换成文本；取不到值直接报错。"""

    def _replace(match: re.Match[str]) -> str:
        return _to_text(resolve_expression(match.group(1), state))

    return _PLACEHOLDER.sub(_replace, text)


def render_value(text: str, state: dict[str, Any]) -> Any:
    """单值插值：整串是一个占位符时返回原始类型，否则按文本处理。"""

    whole = _WHOLE.match(text)
    if whole is not None:
        return resolve_expression(whole.group(1), state)
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
