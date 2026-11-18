"""平台助手进程内调用凭证：只在同一 Python 进程内有效，外部无法伪造。

平台助手代用户调用平台 API 时不走浏览器 Bearer Token，而是经 ASGITransport 在进程内
调用。身份由服务端根据会话归属确定（见 platform_api.py），再用这里的进程内密钥封装，
require_auth 校验通过后才把 actor 身份注入。密钥进程启动时随机生成、不落配置、不持久化，
外部请求拿不到，也就无法冒充任意用户。
"""

from __future__ import annotations

import hmac
import secrets

# 进程启动即生成；外部无法读取，重启即失效。
_INTERNAL_SECRET = secrets.token_urlsafe(48)

# 内部调用头。带 X- 前缀，正常前端不会发送。
SECRET_HEADER = "X-AIWB-Internal-Secret"  # noqa: S105 — header 名，不是密码
ACTOR_HEADER = "X-AIWB-Internal-Actor"


def internal_call_headers(actor_user_id: int) -> dict[str, str]:
    """构造进程内调用头；actor 必须是服务端已校验的真实用户 id。"""
    if not isinstance(actor_user_id, int) or actor_user_id < 1:
        raise ValueError("actor_user_id 无效")
    return {
        SECRET_HEADER: _INTERNAL_SECRET,
        ACTOR_HEADER: str(actor_user_id),
    }


def verify_internal_secret(secret: str | None) -> bool:
    """恒定时间比较；None / 空串一律拒绝。"""
    if not secret:
        return False
    return hmac.compare_digest(secret, _INTERNAL_SECRET)
