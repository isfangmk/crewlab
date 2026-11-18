"""内置 Tool 列表契约（不是 Skill）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class BuiltinToolParamOut(BaseModel):
    """内置工具的一个参数定义（响应）。"""

    name: str = Field(description="参数名")
    type: str = Field(description="参数类型，如 string/number/boolean")
    required: bool = Field(default=False, description="该参数是否必填")
    default: Any | None = Field(default=None, description="参数默认值；无默认值为 None")
    desc: str | None = Field(default=None, description="参数用途说明")
    enum: list[str] | None = Field(default=None, description="参数可选值列表；无限定则 None")


class BuiltinToolGroupOut(BaseModel):
    """内置工具分组（响应）。"""

    key: str = Field(description="分组标识")
    title: str = Field(description="分组中文标题")
    title_en: str = Field(description="分组英文标题")


class BuiltinToolOut(BaseModel):
    """一个内置工具的元信息（响应）。"""

    name: str = Field(description="工具名称")
    group: str = Field(description="所属分组标识，对应 BuiltinToolGroupOut.key")
    description: str = Field(description="工具用途说明")
    risk: str = Field(description="工具风险等级，用于风险提示与控制")
    # always = 随 SkillToolset 始终装配；per_turn = 由对话开关决定
    mount: str = Field(description="工具装配方式：always 随 SkillToolset 始终装配 / per_turn 由对话开关决定")
    params: list[BuiltinToolParamOut] = Field(default_factory=list, description="工具参数定义列表")


class BuiltinToolListOut(BaseModel):
    """内置工具列表（响应）。"""

    items: list[BuiltinToolOut] = Field(description="内置工具列表")
    groups: list[BuiltinToolGroupOut] = Field(description="工具分组列表")
    total: int = Field(description="内置工具总数")


class BuiltinToolRunIn(BaseModel):
    """运行一个内置工具的入参。"""

    params: dict[str, Any] = Field(default_factory=dict, description="传给工具的参数表，键为参数名、值为参数值")


class BuiltinToolRunOut(BaseModel):
    """内置工具运行结果（响应）。"""

    tool: str = Field(description="被执行 tools 的名称")
    result: Any = Field(description="工具执行结果")
