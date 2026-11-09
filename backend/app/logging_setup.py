"""结构化日志初始化，并对密钥类字段做脱敏。"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

# 仓库根目录 logs/backend.log；每次启动覆盖写入
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_FILE = _REPO_ROOT / "logs" / "backend.log"

# 密钥类字段脱敏。
# 引号包裹的值允许含空格（password='p@ss w0rd'），未加引号的值读到空白为止。
_SECRET_KEY_QUOTED = re.compile(
    r"(?P<key>[\"']?[A-Za-z0-9_.-]+[\"']?)(?P<sep>\s*[=:]\s*)"
    r"(?P<quote>[\"'])(?P<value>[^\"']*)(?P=quote)"
)
_SECRET_KEY_BARE = re.compile(
    r"(?P<key>[\"']?[A-Za-z0-9_.-]+[\"']?)(?P<sep>\s*[=:]\s*)"
    r"(?P<value>[^\s\"',}\]]+)"
)

# 敏感键名白名单：按整名匹配，避免把 token_count / prompt_tokens 之类计数误脱敏。
_SECRET_KEY_NAMES: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "api-key",
        "access_key",
        "access_key_id",
        "secret_key",
        "secret_access_key",
        "client_secret",
        "password",
        "passwd",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "auth_token",
        "credential",
        "credentials",
        "private_key",
    }
)


def _is_secret_key(raw: str) -> bool:
    """键名（去引号、转小写、'-' 与 '_' 等价）是否属于敏感键。"""

    cleaned = raw.strip("\"'").lower().replace("-", "_")
    if cleaned in _SECRET_KEY_NAMES:
        return True
    # 允许 vendor 前缀，如 openai_api_key / x-api-key
    return any(cleaned.endswith(f"_{name}") for name in _SECRET_KEY_NAMES)


_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(Bearer\s+)(\S+)", re.IGNORECASE), r"\1***"),
    (re.compile(r"(sk-[A-Za-z0-9]{8})[A-Za-z0-9_-]+"), r"\1***"),
    (re.compile(r"(AUTH_TOKEN\s*[=:]\s*)(\S+)", re.IGNORECASE), r"\1***"),
)


def _redact_pairs(text: str) -> str:
    """把 key=value / key: "value" 里的敏感键值替换为 ***。"""

    for pattern in (_SECRET_KEY_QUOTED, _SECRET_KEY_BARE):

        def _replace(match: re.Match[str]) -> str:
            key = match.group("key")
            if not _is_secret_key(key):
                return match.group(0)
            sep = match.group("sep")
            quote = match.groupdict().get("quote")
            if quote:
                return f"{key}{sep}{quote}***{quote}"
            return f"{key}{sep}***"

        text = pattern.sub(_replace, text)
    return text


def redact_secrets(message: str) -> str:
    """把日志文本中的密钥替换为 ***。"""

    out = message
    for pattern, repl in _REDACT_PATTERNS:
        out = pattern.sub(repl, out)
    return _redact_pairs(out)


class RedactingFormatter(logging.Formatter):
    """格式化时脱敏。"""

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        return redact_secrets(original)


def setup_logging(level: str, *, log_file: Path | None = None) -> None:
    """按级别配置根日志；业务代码统一用 logging.getLogger(__name__)。

    默认写入仓库 logs/backend.log，每次启动以覆盖模式打开。
    """

    path = log_file if log_file is not None else DEFAULT_LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    formatter = RedactingFormatter(
        fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    root = logging.getLogger()
    root.handlers.clear()

    file_handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # 前台运行时仍打控制台；后台 Hidden 启动时无终端可读
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    root.setLevel(level.upper())

    # uvicorn 默认自带 handler；清掉后走根 logger，写入同一日志文件
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True
