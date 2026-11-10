"""技能包（SKILL.md + references/assets/scripts）的解析、构建与校验。

口径完全对齐 ADK：
  - 目录结构：`SKILL.md`（frontmatter + 正文）+ 可选 `references/` `assets/` `scripts/`；
  - frontmatter 合法键：name / description / license / compatibility /
    allowed-tools / allowed_tools / metadata（`google.adk.skills._utils._ALLOWED_FRONTMATTER_KEYS`）；
  - zip 导入：SKILL.md 必须在压缩包根目录，条目 ≤2000、解压后 ≤32MB、禁止 zip slip
    （直接复用 ADK 的 `_load_skill_from_zip_bytes`，不另写一套）；
  - scripts 必须是 UTF-8 文本（`_build_scripts` 会跳过非 UTF-8）。

本模块只做「纯数据」转换，不碰数据库。
"""

from __future__ import annotations

import base64
import io
import json
import logging
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Union

import yaml
from google.adk.skills import Frontmatter, Resources, Script, Skill
from google.adk.skills._utils import (
    _MAX_ZIP_ENTRIES,
    _MAX_ZIP_UNCOMPRESSED_BYTES,
    _load_skill_from_zip_bytes,
    _parse_skill_md_content,
)

logger = logging.getLogger(__name__)

RISK_LEVELS = ("safe", "mutating", "dangerous")

# ADK 允许出现在 frontmatter 的键；多写一个都会被这里拒绝（ADK 的目录校验同口径）
ALLOWED_FRONTMATTER_KEYS = frozenset(
    {
        "name",
        "description",
        "license",
        "allowed-tools",
        "allowed_tools",
        "metadata",
        "compatibility",
    }
)


@dataclass
class SkillPayload:
    """一份技能包：frontmatter 字段 + 正文 + 三类资源。"""

    key: str
    description: str
    instructions: str
    license: str = ""
    compatibility: str = ""
    # frontmatter 的 allowed-tools 原样保存（空格分隔）
    preapproved_tools: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    references: dict[str, Union[str, bytes]] = field(default_factory=dict)
    assets: dict[str, Union[str, bytes]] = field(default_factory=dict)
    scripts: dict[str, str] = field(default_factory=dict)

    def declared_tools(self) -> list[str]:
        """metadata.adk_additional_tools；未声明返回空列表。"""

        raw = self.metadata.get("adk_additional_tools")
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise TypeError("metadata.adk_additional_tools 必须是字符串数组")
        return [str(item) for item in raw]

    def inject_state(self) -> bool:
        raw = self.metadata.get("adk_inject_state")
        if raw is None:
            return False
        if not isinstance(raw, bool):
            raise TypeError("metadata.adk_inject_state 必须是布尔值")
        return raw


def _safe_relative_path(path: str) -> bool:
    return (
        path != ""
        and not path.startswith("/")
        and "\\" not in path
        and all(part not in ("", ".", "..") for part in path.split("/"))
    )


def encode_files(files: Mapping[str, Union[str, bytes]]) -> str:
    """资源目录序列化：文本存 text，二进制存 base64。

    入参用 Mapping：references/assets 是 dict[str, str | bytes]，scripts 是
    dict[str, str]，dict 的值类型不协变，直接用 dict 参数会拒绝其中之一。
    """

    items: list[dict[str, str]] = []
    for path, value in sorted(files.items()):
        if not _safe_relative_path(path):
            raise ValueError(f"资源路径非法（必须是相对路径，且不含 . / ..）: {path}")
        if isinstance(value, bytes):
            items.append({"path": path, "base64": base64.b64encode(value).decode("ascii")})
        else:
            items.append({"path": path, "text": value})
    return json.dumps(items, ensure_ascii=False)


def decode_files(raw: str) -> dict[str, Union[str, bytes]]:
    """encode_files 的逆操作。"""

    if raw.strip() == "":
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise TypeError("资源 JSON 必须是数组")
    out: dict[str, Union[str, bytes]] = {}
    for item in parsed:
        if not isinstance(item, dict):
            raise TypeError("资源条目必须是对象")
        path = item.get("path")
        if not isinstance(path, str):
            raise TypeError("资源条目缺少 path")
        if "text" in item:
            out[path] = str(item["text"])
        elif "base64" in item:
            out[path] = base64.b64decode(str(item["base64"]))
        else:
            raise ValueError(f"资源条目既没有 text 也没有 base64: {path}")
    return out


def _wire_items(items: list[dict[str, Any]]) -> list[tuple[str, Union[str, bytes]]]:
    """校验接口入参 [{path, text|base64}]，解出 (路径, 内容) 列表。

    接口层用 pydantic 序列化时 text / base64 都会出现（另一个是 None），
    所以按「值是否为 None」判断来源，不能按「键是否存在」。
    """

    out: list[tuple[str, Union[str, bytes]]] = []
    seen: set[str] = set()
    for item in items:
        path = item.get("path")
        if not isinstance(path, str) or path == "":
            raise ValueError("资源条目必须带非空 path")
        if not _safe_relative_path(path):
            raise ValueError(f"资源路径非法（必须是相对路径，且不含 . / ..）: {path}")
        if path in seen:
            raise ValueError(f"资源路径重复: {path}")
        seen.add(path)
        text = item.get("text")
        encoded = item.get("base64")
        if text is not None and encoded is not None:
            raise ValueError(f"资源条目 text 与 base64 只能二选一: {path}")
        if text is not None:
            out.append((path, str(text)))
        elif encoded is not None:
            out.append((path, base64.b64decode(str(encoded))))
        else:
            raise ValueError(f"资源条目既没有 text 也没有 base64: {path}")
    return out


def files_from_wire(items: list[dict[str, Any]]) -> dict[str, Union[str, bytes]]:
    """接口入参 → references/ assets/ 资源映射（文本原样，二进制解 base64）。"""

    return dict(_wire_items(items))


def scripts_from_wire(items: list[dict[str, Any]]) -> dict[str, str]:
    """接口入参 → scripts/ 资源映射；脚本必须是 UTF-8 文本。

    二进制脚本在这里显式拒绝，而不是存进去等到执行时才失败。
    """

    out: dict[str, str] = {}
    for path, value in _wire_items(items):
        if isinstance(value, bytes):
            try:
                out[path] = value.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"scripts/{path} 必须是 UTF-8 文本") from exc
        else:
            out[path] = value
    return out


def payload_from_skill_md(
    content: str,
    references: dict[str, Union[str, bytes]] | None = None,
    assets: dict[str, Union[str, bytes]] | None = None,
    scripts: dict[str, Union[str, bytes]] | None = None,
) -> tuple[SkillPayload, Frontmatter]:
    """解析 SKILL.md 文本 + 三份资源，得到 payload 与 ADK Frontmatter 校验结果。"""

    parsed, body = _parse_skill_md_content(content)
    unknown = set(parsed.keys()) - ALLOWED_FRONTMATTER_KEYS
    if unknown:
        raise ValueError(f"SKILL.md frontmatter 含未知字段: {sorted(unknown)}")
    frontmatter = Frontmatter.model_validate(parsed)

    script_text: dict[str, str] = {}
    for path, value in (scripts or {}).items():
        if isinstance(value, bytes):
            try:
                script_text[path] = value.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"scripts/{path} 必须是 UTF-8 文本") from exc
        else:
            script_text[path] = value

    payload = SkillPayload(
        key=frontmatter.name,
        description=frontmatter.description,
        instructions=body,
        license=frontmatter.license or "",
        compatibility=frontmatter.compatibility or "",
        preapproved_tools=frontmatter.allowed_tools or "",
        metadata=dict(frontmatter.metadata),
        references=dict(references or {}),
        assets=dict(assets or {}),
        scripts=script_text,
    )
    return payload, frontmatter


def _normalize_single_root_dir(raw: bytes) -> bytes:
    """把「整体套了一层目录」的 zip 剥掉该目录前缀。

    规范要求 SKILL.md 在压缩包根目录；但用户从编辑器下载的技能包常见形态是
    `my-skill/SKILL.md`。这里只在「根目录没有 SKILL.md 且仅有一个顶层目录」时
    重写一次压缩包，并打日志留痕，不做静默猜测。
    """

    with zipfile.ZipFile(io.BytesIO(raw)) as zin:
        names = [n for n in zin.namelist() if not n.endswith("/")]
        if any(n in ("SKILL.md", "skill.md") for n in names):
            return raw
        roots = {n.split("/", 1)[0] for n in names if "/" in n}
        if len(roots) != 1:
            return raw
        root = roots.pop()
        if not any(n in (f"{root}/SKILL.md", f"{root}/skill.md") for n in names):
            return raw

        logger.info("技能包整体位于子目录，导入时剥掉前缀 dir=%s", root)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zout:
            for name in names:
                if not name.startswith(f"{root}/"):
                    continue
                zout.writestr(name[len(root) + 1 :], zin.read(name))
        return buffer.getvalue()


def payload_from_zip(raw: bytes) -> SkillPayload:
    """解析 zip 技能包（复用 ADK 的限额与 zip slip 校验）。"""

    normalized = _normalize_single_root_dir(raw)
    skill = _load_skill_from_zip_bytes(normalized)
    return SkillPayload(
        key=skill.frontmatter.name,
        description=skill.frontmatter.description,
        instructions=skill.instructions,
        license=skill.frontmatter.license or "",
        compatibility=skill.frontmatter.compatibility or "",
        preapproved_tools=skill.frontmatter.allowed_tools or "",
        metadata=dict(skill.frontmatter.metadata),
        references=dict(skill.resources.references),
        assets=dict(skill.resources.assets),
        scripts={path: script.src for path, script in skill.resources.scripts.items()},
    )


def build_skill_md(payload: SkillPayload) -> str:
    """把 payload 还原为 SKILL.md 文本（frontmatter + 正文）。"""

    front: dict[str, Any] = {
        "name": payload.key,
        "description": payload.description,
    }
    if payload.license != "":
        front["license"] = payload.license
    if payload.compatibility != "":
        front["compatibility"] = payload.compatibility
    if payload.preapproved_tools != "":
        front["allowed-tools"] = payload.preapproved_tools
    if payload.metadata:
        front["metadata"] = payload.metadata
    dumped = yaml.safe_dump(front, allow_unicode=True, sort_keys=False).rstrip()
    return f"---\n{dumped}\n---\n\n{payload.instructions.strip()}\n"


def payload_to_zip(payload: SkillPayload) -> bytes:
    """导出为可再次导入的 zip（SKILL.md + references/ + assets/ + scripts/）。"""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zout:
        zout.writestr("SKILL.md", build_skill_md(payload))
        for prefix, files in (
            ("references", payload.references),
            ("assets", payload.assets),
            ("scripts", payload.scripts),
        ):
            for path, value in sorted(files.items()):
                data = value.encode("utf-8") if isinstance(value, str) else value
                zout.writestr(f"{prefix}/{path}", data)
    return buffer.getvalue()


def payload_to_skill(payload: SkillPayload) -> Skill:
    """转成 ADK 运行时对象（供 SkillToolset 使用）。"""

    front: dict[str, Any] = {
        "name": payload.key,
        "description": payload.description,
        "metadata": payload.metadata,
    }
    if payload.license != "":
        front["license"] = payload.license
    if payload.compatibility != "":
        front["compatibility"] = payload.compatibility
    if payload.preapproved_tools != "":
        front["allowed-tools"] = payload.preapproved_tools
    frontmatter = Frontmatter.model_validate(front)
    scripts = {path: Script(src=src) for path, src in payload.scripts.items()}
    return Skill(
        frontmatter=frontmatter,
        instructions=payload.instructions,
        resources=Resources(
            references=dict(payload.references),
            assets=dict(payload.assets),
            scripts=scripts,
        ),
    )


def check_payload_limits(payload: SkillPayload) -> None:
    """对齐 ADK zip 限额：条目数与解压后总字节。"""

    files = [
        *payload.references.items(),
        *payload.assets.items(),
        *payload.scripts.items(),
    ]
    if len(files) > _MAX_ZIP_ENTRIES:
        raise ValueError(
            f"技能文件过多：{len(files)} 个，上限 {_MAX_ZIP_ENTRIES} 个"
        )
    total = 0
    for path, value in files:
        if not _safe_relative_path(path):
            raise ValueError(f"资源路径非法（必须是相对路径，且不含 . / ..）: {path}")
        total += len(value.encode("utf-8")) if isinstance(value, str) else len(value)
    if total > _MAX_ZIP_UNCOMPRESSED_BYTES:
        raise ValueError(
            f"技能解压后过大：{total} 字节，上限 {_MAX_ZIP_UNCOMPRESSED_BYTES} 字节"
        )


def validate_payload(payload: SkillPayload) -> None:
    """校验一份技能包的结构与限额；任何问题都抛 ValueError（不静默降级）。

    工具名是否存在于目录（内置工具 / MCP 工具）由 skills_service 负责：
    本模块只处理「纯数据」，不认识工具注册表与 MCP 探测缓存。
    """

    if payload.key.strip() == "":
        raise ValueError("技能名不能为空")
    if payload.description.strip() == "":
        raise ValueError("技能描述不能为空")
    if payload.instructions.strip() == "":
        raise ValueError("SKILL.md 正文不能为空")
    check_payload_limits(payload)
    # 声明工具的类型校验（数组 / 非空字符串）在取值时完成
    payload.declared_tools()
    # adk_inject_state 的类型校验在取值时完成
    payload.inject_state()
