"""脱敏规则单元测试：审计导出 / 配置快照 / 凭证清单共用的遮蔽逻辑。"""

from __future__ import annotations

from app.services.redaction import MASK, redact_url, redact_value


def test_redacts_secret_keys_recursively() -> None:
    detail = {
        "name": "github",
        "api_key": "AKIA-secret",
        "nested": {"password": "hunter2", "token": "abc"},
        "list": [{"access_token": "xyz"}, {"tool": "read_file"}],
        "api_key_changed": True,
    }
    out = redact_value(detail)
    assert out["api_key"] == MASK
    assert out["nested"]["password"] == MASK
    assert out["nested"]["token"] == MASK
    assert out["list"][0]["access_token"] == MASK
    assert out["list"][1]["tool"] == "read_file"
    # 布尔开关不是密钥值，不能被顺手抹掉
    assert out["api_key_changed"] is True


def test_redacts_url_userinfo_and_sensitive_query() -> None:
    url = "https://user:p@ss@mcp.example.com/sse?token=abc&page=2#frag"
    out = redact_url(url)
    assert out.startswith("https://user:") and f"user:{MASK}@mcp.example.com" in out
    assert "token=***" in out
    assert "page=2" in out
    assert "#frag" in out
    assert "abc" not in out


def test_redacts_inline_secrets_in_plain_text() -> None:
    out = redact_value({"result": '{"api_key": "sk-abcdefgh12345", "ok": true}'})
    assert "sk-abcdefgh12345" not in out["result"]
    assert MASK in out["result"]


def test_redacts_bearer_token() -> None:
    out = redact_value({"headers": "Authorization: Bearer abc.def.ghi"})
    assert "abc.def.ghi" not in out["headers"]


def test_keeps_non_sensitive_values() -> None:
    detail = {"model": "glm-5", "max_turns": 8, "tools": ["get_current_time"]}
    assert redact_value(detail) == detail
