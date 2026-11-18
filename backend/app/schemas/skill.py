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

    path: str = Field(min_length=1, description="资源文件在技能包内的相对路径")
    text: str | None = Field(default=None, description="文本文件的内容；文本文件用此字段，与 base64 二选一")
    base64: str | None = Field(default=None, description="二进制文件的 base64 编码内容；与 text 二选一")


class SkillPayloadIn(BaseModel):
    """技能包内容（创建 / 更新共用）。"""

    key: str = Field(min_length=1, max_length=64, description="技能唯一标识，即 SKILL.md frontmatter 的 name，须与目录名一致，最长 64")
    description: str = Field(min_length=1, max_length=1024, description="技能用途说明，供模型判断是否调用，最长 1024")
    instructions: str = Field(min_length=1, description="技能被触发后注入给模型的具体指令正文")
    preapproved_tools: str = Field(default="", description="预授权工具清单；留空表示无预授权工具，调用工具仍需审批")
    # 平台侧属性：ADK 规范外，写入 metadata 一并保存
    risk: str = Field(min_length=1, description="平台侧风险等级，随技能一并保存，用于风险提示与控制")
    # 平台侧分组（固定枚举），驱动技能目录页的筛选条与分组表
    group: str = Field(min_length=1, max_length=32, description="平台侧分组，驱动技能目录页筛选，最长 32")
    # metadata.adk_additional_tools
    tools: list[str] = Field(default_factory=list, description="附加工具名列表，写入 metadata.adk_additional_tools")
    # metadata.adk_inject_state
    inject_state: bool = Field(default=False, description="是否向技能注入运行状态，写入 metadata.adk_inject_state")
    references: list[SkillFileIn] = Field(default_factory=list, description="references 参考资源目录中的文件列表")
    assets: list[SkillFileIn] = Field(default_factory=list, description="assets 静态资源目录中的文件列表")
    scripts: list[SkillFileIn] = Field(default_factory=list, description="scripts 脚本资源目录中的文件列表")


class SkillImportIn(BaseModel):
    """zip 导入的补充字段；技能包自带 risk/group 时以包内为准。"""

    risk: str | None = Field(default=None, description="导入时指定的风险等级；技能包自带则以包内为准")
    group: str | None = Field(default=None, description="导入时指定的平台分组；技能包自带则以包内为准")
