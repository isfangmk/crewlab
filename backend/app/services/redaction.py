"""脱敏工具：审计导出、配置快照、凭证清单共用同一套规则。

原则：导出物是给人看的合规材料，不是排障现场；密钥类字段一律遮蔽，
URL 里的 userinfo 与敏感查询参数同样不能带出。
"""

from __future__ import annotations

import re
from urllib.parse import unquote

from app.logging_setup import is_secret_key, redact_secrets

MASK = "***"

_URL_RE = re.compile(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)(?P<rest>.*)$", re.S)


def _redact_query(query: str) -> str:
    """查询串里只有敏感参数需要遮蔽，其余原样保留以便对照。"""

    parts: list[str] = []
    for pair in query.split("&"):
        if pair == "":
            parts.append(pair)
            continue
        key, sep, _value = pair.partition("=")
        if sep and is_secret_key(unquote(key)):
            parts.append(f"{key}={MASK}")
        else:
            parts.append(pair)
    return "&".join(parts)


def redact_url(url: str) -> str:
    """遮蔽 URL 的 userinfo 密码与敏感查询参数。"""

    match = _URL_RE.match(url)
    if match is None:
        return redact_secrets(url)
    scheme = match.group("scheme")
    rest = match.group("rest")

    cut = len(rest)
    for char in "/?#":
        index = rest.find(char)
        if index != -1 and index < cut:
            cut = index
    authority, remainder = rest[:cut], rest[cut:]

    if "@" in authority:
        userinfo, _, host = authority.rpartition("@")
        user, sep, _password = userinfo.partition(":")
        authority = f"{user}:{MASK}@{host}" if sep else f"{MASK}@{host}"

    fragment = ""
    if "#" in remainder:
        remainder, _, fragment = remainder.partition("#")
        fragment = f"#{fragment}"

    query = ""
    if "?" in remainder:
        remainder, _, query = remainder.partition("?")
        query = f"?{_redact_query(query)}"

    return f"{scheme}{authority}{remainder}{query}{fragment}"


def redact_value(value: object) -> object:
    """按类型递归脱敏：dict 按键名、str 按内嵌密钥、URL 另做结构化遮蔽。"""

    if isinstance(value, dict):
        redacted: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("脱敏只处理字符串键的 dict")
            if is_secret_key(key):
                redacted[key] = MASK
            else:
                redacted[key] = redact_value(item)
        return redacted
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            return redact_url(value)
        return redact_secrets(value)
    return value
