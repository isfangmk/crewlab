"""对话创建 / 安装技能：落库 + 审计 + 下载侧的 SSRF 防护。

三个入口对应三条对话工具：
  - create_skill_from_chat：LLM 产出结构化字段，平台内新建（origin=created）；
  - install_skill_from_url：HTTP(S) zip 直链或 git 仓库，下载后导入（origin=imported）；
  - install_skill_from_zip：对话附件（attachment_id）指向的 zip 包导入。

安全口径：
  - 任何外部 URL 先做真实 DNS 解析 + IP 判定，私有段 / 回环 / 链路本地 /
    云元数据地址一律拒绝；重定向每一跳重新校验（防 DNS rebinding 与跳转绕过）。
  - 禁 file:// 与 git@（scp 风格）用于安装；下载压缩字节前置上限 32MB，
    解压上限交给 skill_bundle.payload_from_zip（复用 ADK 的 32MB / 2000 条目限额）。
  - 包内自声明的 risk / group 必须服务端校验，非法显式失败，不静默采信。
"""

from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import logging
import shutil
import socket
import tempfile
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.observability.audit_service import write_audit
from app.services.skills import skill_bundle, skills_service
from app.services.skills.skill_bundle import RISK_LEVELS, SkillPayload, payload_from_zip
from app.services.skills.skill_groups import validate_skill_group

logger = logging.getLogger(__name__)

# 压缩下载上限：与附件 zip 上限、ADK 解压上限对齐
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
_HTTP_TIMEOUT_SECONDS = 30.0
_MAX_REDIRECTS = 5

# 明确禁止的 URL 协议：只放行 http/https，file:// 与 git@（解析后无 scheme）都被挡掉
_ALLOWED_SCHEMES = frozenset({"http", "https"})


# ---------------------------------------------------------------- 通用工具


def _validate_skill_key(key: str) -> str:
    """按 ADK 规则校验 key，必须在写库前完成。

    create_user_skill 本身只校验非空；真正的命名规则在 payload_to_skill →
    Frontmatter（小写 kebab-case/snake_case、≤64、无连续/首尾分隔符）。
    若不在这里提前校验，落库提交后 get_skill_summary 回读会失败，且脏数据已提交。
    直接借 ADK 的 Frontmatter 做校验，确保与实际运行时的规则永远一致
    （snake_case 是否放行由 ADK 特性开关决定，不在此处复制一套正则）。
    """
    from google.adk.skills import Frontmatter

    if not isinstance(key, str) or key.strip() == "":
        raise ValueError("技能 key 不能为空")
    try:
        Frontmatter(name=key, description="key-validation")
    except Exception as exc:  # noqa: BLE001 — ADK 抛 pydantic.ValidationError，转成 ValueError
        raise ValueError(
            f"技能 key 不符合 ADK 命名规则（小写 kebab-case/snake_case、"
            f"最长 64 字符、无连续或首尾分隔符）: {key!r}"
        ) from exc
    return key


def _decode_tools(tools: Any) -> list[str] | None:
    """LLM 产出的 tools 可能是数组，也可能是 JSON / 逗号分隔字符串，统一解成列表。"""

    if tools is None:
        return None
    if isinstance(tools, str):
        raw = tools.strip()
        if raw == "":
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = [item.strip() for item in raw.split(",")]
        if isinstance(parsed, str):
            parsed = [parsed]
        if not isinstance(parsed, list):
            raise ValueError("tools 必须是字符串数组或 JSON 数组字符串")
        return [str(item).strip() for item in parsed if str(item).strip() != ""]
    if isinstance(tools, (list, tuple)):
        return [str(item).strip() for item in tools if str(item).strip() != ""]
    raise ValueError("tools 类型非法")


def _as_files(value: Any, *, scripts: bool = False) -> dict[str, Any] | None:
    """资源入参归一化：dict 直用；wire 列表 / JSON 字符串走 skill_bundle 解码。"""

    if value is None:
        return None
    decode = skill_bundle.scripts_from_wire if scripts else skill_bundle.files_from_wire
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        if value.strip() == "":
            return None
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("资源参数必须是对象或 JSON 数组字符串") from exc
    if isinstance(value, list):
        return decode(value)
    raise ValueError("资源参数类型非法")


def _resolve_risk(metadata: dict[str, Any], explicit: str | None, *, key: str) -> str:
    """包内声明优先；声明非法显式失败，缺失时才退回调用方显式值。"""

    if "risk" in metadata:
        declared = metadata["risk"]
        if not isinstance(declared, str) or declared not in RISK_LEVELS:
            raise ValueError(
                f"技能包声明的 risk 非法: {key} risk={declared!r}"
                f"（允许 {'/'.join(RISK_LEVELS)}）"
            )
        return declared
    if explicit is None or explicit == "":
        raise ValueError(f"技能包未声明 risk，请显式提供: {key}")
    if explicit not in RISK_LEVELS:
        raise ValueError(f"risk 非法: {key} risk={explicit!r}")
    return explicit


def _resolve_group(metadata: dict[str, Any], explicit: str | None, *, key: str) -> str:
    if "group" in metadata:
        declared = metadata["group"]
        if not isinstance(declared, str):
            raise ValueError(f"技能包声明的 group 非法: {key} group={declared!r}")
        return validate_skill_group(declared)
    if explicit is None or explicit == "":
        raise ValueError(f"技能包未声明 group，请显式提供: {key}")
    return validate_skill_group(explicit)


def _result(row: Any, payload: SkillPayload) -> dict:
    """工具返回值：key + summary，前端据此展示技能卡片。"""

    return {
        "key": row.key,
        "summary": {
            "key": row.key,
            "description": row.description,
            "risk": row.risk,
            "group": row.skill_group,
            "origin": row.origin,
            "tools": payload.declared_tools(),
        },
    }


# ---------------------------------------------------------------- SSRF 防护


def _is_forbidden_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """私有段 / 回环 / 链路本地（含 169.254.169.254 云元数据）/ 多播 / 保留 / 未指定。"""

    # IPv4-mapped IPv6（::ffff:127.0.0.1）必须取其映射的 IPv4 再判一次
    mapped = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) else None
    if mapped is not None and _is_forbidden_ip(mapped):
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _assert_public_host(host: str, port: int) -> None:
    """真实 DNS 解析后逐 IP 判定；解析失败或任一 IP 非法即拒绝（fail closed）。"""

    host = host.rstrip(".")
    if host == "":
        raise ValueError("URL 缺少主机名")
    try:
        # 字面量 IP 直接判定，不走 DNS
        literal = ipaddress.ip_address(host)
        if _is_forbidden_ip(literal):
            raise ValueError(f"拒绝访问内网/保留地址: {host}")
        return
    except ValueError:
        # 不是 IP 字面量，继续走 DNS 解析
        pass

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError) as exc:
        raise ValueError(f"URL 主机 DNS 解析失败: {host}（{exc}）") from exc
    if not infos:
        raise ValueError(f"URL 主机无解析结果: {host}")
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ValueError(f"URL 主机解析出非法 IP: {address}") from exc
        if _is_forbidden_ip(ip):
            raise ValueError(f"拒绝访问内网/保留地址: {host} -> {address}")


def _assert_public_url(url: str) -> None:
    """校验协议 + 主机；HTTP(S) 之外的协议（含 file:// / git@）一律拒绝。"""

    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"不支持的 URL 协议: {scheme or '(无)'}"
            "（仅允许 http/https；禁止 file:// 与 git@）"
        )
    host = parsed.hostname
    if host is None:
        raise ValueError("URL 缺少主机名")
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError(f"URL 端口非法: {exc}") from exc
    _assert_public_host(host, port)


async def _download_http_guarded(url: str) -> tuple[bytes, str]:
    """HTTP(S) 直链下载；手动处理重定向，每跳先做 SSRF 校验，流式限量 32MB。"""

    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        _assert_public_url(current)
        try:
            async with httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT_SECONDS,
                follow_redirects=False,
                headers={"User-Agent": "Crewlab-SkillInstall/1.0"},
            ) as client:
                async with client.stream("GET", current) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError(f"重定向响应缺少 Location: {current}")
                        current = urllib.parse.urljoin(current, location)
                        continue
                    if response.status_code >= 400:
                        raise ValueError(f"下载失败: HTTP {response.status_code}")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > MAX_DOWNLOAD_BYTES:
                            raise ValueError(
                                f"下载内容超过上限: {total} > {MAX_DOWNLOAD_BYTES} 字节"
                            )
                        chunks.append(chunk)
                    return b"".join(chunks), current
        except httpx.HTTPError as exc:
            raise ValueError(f"下载失败: {exc}") from exc
    raise ValueError(f"重定向次数超过 {_MAX_REDIRECTS} 次")


# ---------------------------------------------------------------- git 仓库取包


def _looks_like_git_repo(url: str) -> bool:
    """区分 git 仓库与 zip 直链：.git 结尾或常见托管域名（但 .zip 直链优先）。"""

    parsed = urllib.parse.urlsplit(url)
    path = parsed.path.lower()
    if path.endswith(".zip"):
        return False
    if path.endswith(".git"):
        return True
    host = (parsed.hostname or "").lower()
    return any(
        domain in host
        for domain in ("github.com", "gitlab.com", "bitbucket.org", "gitee.com")
    )


def _zip_skill_dir(skill_dir: Path) -> bytes:
    """把含 SKILL.md 的目录打成 zip（只收 SKILL.md + 三类资源，排除隐藏文件）。"""

    buffer = io.BytesIO()
    total = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in sorted(skill_dir.rglob("*")):
            if not item.is_file() or item.is_symlink():
                continue
            rel = item.relative_to(skill_dir)
            parts = rel.parts
            if any(part.startswith(".") for part in parts):
                continue
            top = parts[0]
            if top.lower() != "skill.md" and top not in ("references", "assets", "scripts"):
                continue
            total += item.stat().st_size
            if total > MAX_DOWNLOAD_BYTES:
                raise ValueError("技能目录压缩前总量超过 32MB 上限")
            zout.writestr(rel.as_posix(), item.read_bytes())
    return buffer.getvalue()


def _payload_from_git_repo(repo_dir: Path) -> SkillPayload:
    """从克隆结果里找 SKILL.md（取其所在目录打包）或 .zip 技能包。"""

    repo_root = repo_dir.resolve()
    skill_mds: list[Path] = []
    zips: list[Path] = []
    for item in repo_dir.rglob("*"):
        if not item.is_file() or item.is_symlink() or ".git" in item.parts:
            continue
        if item.name.lower() == "skill.md":
            skill_mds.append(item)
        elif item.suffix.lower() == ".zip":
            zips.append(item)
    # 优先最浅目录（仓库根目录的 SKILL.md 最可能是技能本体）
    if skill_mds:
        skill_mds.sort(key=lambda p: (len(p.relative_to(repo_dir).parts), str(p)))
        chosen = skill_mds[0]
        # 防目录穿越：必须在克隆目录内
        chosen.resolve().relative_to(repo_root)
        raw = _zip_skill_dir(chosen.parent)
        return payload_from_zip(raw)
    if zips:
        zips.sort(key=lambda p: (len(p.relative_to(repo_dir).parts), str(p)))
        chosen_zip = zips[0]
        chosen_zip.resolve().relative_to(repo_root)
        raw = chosen_zip.read_bytes()
        if len(raw) > MAX_DOWNLOAD_BYTES:
            raise ValueError("仓库内 zip 超过 32MB 上限")
        return payload_from_zip(raw)
    raise ValueError("仓库中未找到 SKILL.md 或 .zip 技能包")


async def _payload_from_git_url(url: str) -> tuple[SkillPayload, str]:
    """复用 knowledge_sync._run_git 的 clone 骨架，产出 payload + commit_sha。"""

    # git clone 同样可能成为 SSRF 通道：克隆前先对 URL 做 DNS + IP 校验
    _assert_public_url(url)
    from app.services.knowledge.knowledge_sync import _run_git

    temp_dir = Path(tempfile.mkdtemp(prefix="crelab-skill-git-"))
    repo_dir = temp_dir / "repo"
    try:
        # -c http.followRedirects=false：禁止 git 自己跟随重定向，否则 clone 阶段
        # 会跳到未校验的地址，绕过上面的 SSRF 检查（与 HTTP 下载逐跳重校同等口径）。
        await _run_git(
            [
                "-c",
                "http.followRedirects=false",
                "clone",
                "--depth",
                "1",
                "--single-branch",
                "--no-tags",
                "--",
                url,
                str(repo_dir),
            ]
        )
        commit_sha = (await _run_git(["rev-parse", "HEAD"], cwd=repo_dir)).strip()
        payload = _payload_from_git_repo(repo_dir)
        return payload, commit_sha
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------- 三个入口


async def create_skill_from_chat(
    db: AsyncSession,
    *,
    key: str,
    description: str,
    instructions: str,
    risk: str,
    group: str,
    preapproved_tools: str = "",
    tools: Any = None,
    references: Any = None,
    assets: Any = None,
    scripts: Any = None,
    owner_user_id: int | None = None,
) -> dict:
    """对话新建技能：LLM 产结构化字段，先按 ADK 规则校验 key 再落库。"""

    _validate_skill_key(key)
    if risk not in RISK_LEVELS:
        raise ValueError(f"risk 非法: {risk}")
    validate_skill_group(group)

    metadata: dict[str, Any] = {}
    decoded_tools = _decode_tools(tools)
    if decoded_tools:
        metadata["adk_additional_tools"] = decoded_tools
    payload = SkillPayload(
        key=key,
        description=description,
        instructions=instructions,
        preapproved_tools=preapproved_tools or "",
        metadata=metadata,
        references=_as_files(references) or {},
        assets=_as_files(assets) or {},
        scripts=_as_files(scripts, scripts=True) or {},
    )
    row = await skills_service.create_user_skill(
        db, payload, risk=risk, group=group, origin="created"
    )
    logger.info("对话创建技能 key=%s risk=%s group=%s owner=%s", row.key, risk, group, owner_user_id)
    await write_audit(
        actor_user_id=owner_user_id,
        action="skill.create",
        resource_type="skill",
        resource_id=row.key,
        detail={
            "via": "chat",
            "risk": row.risk,
            "group": row.skill_group,
            "tools": payload.declared_tools(),
        },
    )
    return _result(row, payload)


async def install_skill_from_url(
    db: AsyncSession,
    *,
    url: str,
    risk: str | None = None,
    group: str | None = None,
    owner_user_id: int | None = None,
) -> dict:
    """从 URL 安装技能：SSRF 防护优先，支持 zip 直链与 git 仓库。"""

    if not isinstance(url, str) or url.strip() == "":
        raise ValueError("url 不能为空")
    url = url.strip()
    _assert_public_url(url)  # 最外层先校验，git 与直链共用

    commit_sha = ""
    zip_sha256 = ""
    final_url = url
    if _looks_like_git_repo(url):
        payload, commit_sha = await _payload_from_git_url(url)
    else:
        raw, final_url = await _download_http_guarded(url)
        zip_sha256 = hashlib.sha256(raw).hexdigest()
        payload = payload_from_zip(raw)
    # 来源写进 metadata，随 SKILL.md 往返、零迁移
    payload.metadata["source_url"] = final_url

    resolved_risk = _resolve_risk(payload.metadata, risk, key=payload.key)
    resolved_group = _resolve_group(payload.metadata, group, key=payload.key)
    row = await skills_service.create_user_skill(
        db,
        payload,
        risk=resolved_risk,
        group=resolved_group,
        forbid_unknown_tools=False,
        origin="imported",
    )
    logger.info(
        "URL 安装技能 key=%s url=%s commit=%s owner=%s",
        row.key,
        final_url,
        commit_sha or "-",
        owner_user_id,
    )
    await write_audit(
        actor_user_id=owner_user_id,
        action="skill.install",
        resource_type="skill",
        resource_id=row.key,
        detail={
            "via": "url",
            "url": final_url,
            "commit_sha": commit_sha,
            "zip_sha256": zip_sha256,
            "risk": row.risk,
            "group": row.skill_group,
        },
    )
    return _result(row, payload)


async def install_skill_from_zip(
    db: AsyncSession,
    *,
    attachment_id: str,
    session_id: str,
    risk: str | None = None,
    group: str | None = None,
    owner_user_id: int | None = None,
) -> dict:
    """从对话附件安装技能：附件归属校验 → 取字节 → 解析 → 落库。"""

    from app.services.chat.attachment_service import bytes_from_attachment, get_attachment

    attachment = await get_attachment(db, session_id, attachment_id)
    raw = bytes_from_attachment(attachment)
    zip_sha256 = hashlib.sha256(raw).hexdigest()
    payload = payload_from_zip(raw)
    payload.metadata["source_url"] = f"attachment://{attachment_id}"

    resolved_risk = _resolve_risk(payload.metadata, risk, key=payload.key)
    resolved_group = _resolve_group(payload.metadata, group, key=payload.key)
    row = await skills_service.create_user_skill(
        db,
        payload,
        risk=resolved_risk,
        group=resolved_group,
        forbid_unknown_tools=False,
        origin="imported",
    )
    logger.info(
        "附件安装技能 key=%s attachment=%s session=%s owner=%s",
        row.key,
        attachment_id,
        session_id,
        owner_user_id,
    )
    await write_audit(
        actor_user_id=owner_user_id,
        action="skill.install",
        resource_type="skill",
        resource_id=row.key,
        detail={
            "via": "zip",
            "attachment_id": attachment_id,
            "session_id": session_id,
            "zip_sha256": zip_sha256,
            "risk": row.risk,
            "group": row.skill_group,
        },
    )
    return _result(row, payload)
