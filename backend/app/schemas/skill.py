"""技能 API 契约。

技能包对应 ADK 的 SKILL.md 目录结构：
  - `key` 即 SKILL.md frontmatter 的 name，必须与目录名一致；
  - `references` / `assets` / `scripts` 是三类可选资源目录；
  - 资源用 [{path, text}] 或 [{path, base64}] 表示（二进制走 base64）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SkillFileIn(BaseModel):
    """一个资源文件：文本走 text，二进制走 base64（二选一）。"""

    path: str = Field(min_length=1)
    text: str | None = None
    base64: str | None = None


class SkillPayloadIn(BaseModel):
    """技能包内容（创建 / 更新共用）。"""

    key: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1024)
    instructions: str = Field(min_length=1)
    license: str = ""
    compatibility: str = ""
    preapproved_tools: str = ""
    # 平台侧属性：ADK 规范外，写入 metadata 一并保存
    risk: str = Field(min_length=1)
    category: str = Field(min_length=1, max_length=64)
    # metadata.adk_additional_tools
    tools: list[str] = Field(default_factory=list)
    # metadata.adk_inject_state
    inject_state: bool = False
    references: list[SkillFileIn] = Field(default_factory=list)
    assets: list[SkillFileIn] = Field(default_factory=list)
    scripts: list[SkillFileIn] = Field(default_factory=list)


class SkillImportIn(BaseModel):
    """zip 导入的补充字段；技能包自带 risk/category 时以包内为准。"""

    risk: str | None = None
    category: str | None = None
