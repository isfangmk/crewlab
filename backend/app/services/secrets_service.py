"""凭证 CRUD：用 AUTH_TOKEN 派生 Fernet 密钥加密字段值。"""

from __future__ import annotations

import base64
import hashlib
import json
import logging

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models_orm.secret import SecretCredential

logger = logging.getLogger(__name__)

# 内置类型：与设置页文案对齐；custom = 自由字段
_CREDENTIAL_TYPES: list[dict] = [
    {
        "name": "custom",
        "label": "自定义",
        "fields": [],
        "builtin": True,
    },
    {
        "name": "github",
        "label": "GitHub",
        "fields": [
            {"key": "token", "label": "Personal Access Token", "secret": True},
        ],
        "inject_env": {"token": "GITHUB_TOKEN"},
        "builtin": True,
    },
    {
        "name": "aws",
        "label": "AWS",
        "fields": [
            {"key": "access_key_id", "label": "Access Key ID", "secret": True},
            {"key": "secret_access_key", "label": "Secret Access Key", "secret": True},
        ],
        "inject_env": {
            "access_key_id": "AWS_ACCESS_KEY_ID",
            "secret_access_key": "AWS_SECRET_ACCESS_KEY",
        },
        "builtin": True,
    },
    {
        "name": "tencentcloud",
        "label": "腾讯云",
        "fields": [
            {"key": "secret_id", "label": "SecretId", "secret": True},
            {"key": "secret_key", "label": "SecretKey", "secret": True},
        ],
        "inject_env": {
            "secret_id": "TENCENTCLOUD_SECRET_ID",
            "secret_key": "TENCENTCLOUD_SECRET_KEY",
        },
        "builtin": True,
    },
]


def _fernet() -> Fernet:
    """凭证字段的加解密器。

    密钥来自独立的 CREDENTIAL_ENCRYPTION_KEY，与登录口令 AUTH_TOKEN 解耦：
    轮换登录口令不应让已存凭证永久不可解。
    """

    raw = get_settings().credential_encryption_key.encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def list_credential_types() -> list[dict]:
    return list(_CREDENTIAL_TYPES)


def _type_by_name(name: str) -> dict:
    for t in _CREDENTIAL_TYPES:
        if t["name"] == name:
            return t
    raise LookupError(f"未知凭证类型: {name}")


def _encrypt_fields(fields: dict[str, str]) -> str:
    raw = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
    return _fernet().encrypt(raw.encode("utf-8")).decode("ascii")


def _validate_fields(cred_type: str, fields: dict[str, str]) -> list[str]:
    if not isinstance(fields, dict) or len(fields) == 0:
        raise ValueError("fields 不能为空")
    for k, v in fields.items():
        if not isinstance(k, str) or k == "":
            raise ValueError("fields 的 key 必须是非空字符串")
        if not isinstance(v, str) or v == "":
            raise ValueError(f"fields[{k}] 不能为空")

    spec = _type_by_name(cred_type)
    declared = [f["key"] for f in spec["fields"]]
    if cred_type == "custom":
        return sorted(fields.keys())
    missing = [k for k in declared if k not in fields]
    if missing:
        raise ValueError(f"缺少字段: {','.join(missing)}")
    extra = [k for k in fields if k not in declared]
    if extra:
        raise ValueError(f"多余字段: {','.join(extra)}")
    return declared


def secret_to_wire(row: SecretCredential) -> dict:
    keys = json.loads(row.field_keys_json)
    if not isinstance(keys, list):
        raise TypeError(f"field_keys_json 损坏 id={row.id}")
    return {
        "id": row.id,
        "name": row.name,
        "type": row.cred_type,
        "description": row.description,
        "field_keys": keys,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


async def list_secrets(db: AsyncSession) -> list[SecretCredential]:
    result = await db.scalars(select(SecretCredential).order_by(SecretCredential.id))
    return list(result.all())


async def create_secret(
    db: AsyncSession,
    *,
    name: str,
    cred_type: str,
    description: str,
    fields: dict[str, str],
) -> SecretCredential:
    if name == "":
        raise ValueError("name 不能为空")
    _type_by_name(cred_type)
    keys = _validate_fields(cred_type, fields)
    existing = await db.scalar(select(SecretCredential).where(SecretCredential.name == name))
    if existing is not None:
        raise ValueError(f"凭证名称已存在: {name}")
    row = SecretCredential(
        name=name,
        cred_type=cred_type,
        description=description,
        field_keys_json=json.dumps(keys, ensure_ascii=False),
        fields_ciphertext=_encrypt_fields(fields),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info("创建凭证 id=%s name=%s type=%s keys=%s", row.id, name, cred_type, keys)
    return row


async def delete_secret(db: AsyncSession, secret_id: int) -> None:
    row = await db.get(SecretCredential, secret_id)
    if row is None:
        raise LookupError(f"凭证不存在: {secret_id}")
    await db.delete(row)
    await db.commit()
    logger.info("删除凭证 id=%s", secret_id)


async def resolve_secret_fields(
    db: AsyncSession, name: str
) -> dict[str, str]:
    """按凭证名取出解密后的字段值。

    这是凭证库唯一的读取入口：解密失败（密钥被换过 / 密文损坏）
    必须显式失败，不能返回空 dict 让调用方以为凭证是空的。
    """

    row = await db.scalar(select(SecretCredential).where(SecretCredential.name == name))
    if row is None:
        raise LookupError(f"凭证不存在: {name}")
    try:
        raw = _fernet().decrypt(row.fields_ciphertext.encode("ascii"))
    except Exception as exc:
        raise ValueError(
            f"凭证解密失败（id={row.id} name={name}）：密钥不匹配或密文损坏"
        ) from exc
    fields = json.loads(raw.decode("utf-8"))
    if not isinstance(fields, dict):
        raise TypeError(f"凭证字段损坏 id={row.id}")
    for k, v in fields.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise TypeError(f"凭证字段类型非法 id={row.id} key={k}")
    logger.info("解析凭证 name=%s type=%s keys=%s", name, row.cred_type, sorted(fields.keys()))
    return fields
