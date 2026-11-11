"""内置 Tool 列表契约（不是 Skill）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class BuiltinToolParamOut(BaseModel):
    name: str
    type: str
    required: bool = False
    default: Any | None = None
    desc: str | None = None
    enum: list[str] | None = None


class BuiltinToolGroupOut(BaseModel):
    key: str
    title: str
    title_en: str


class BuiltinToolOut(BaseModel):
    name: str
    group: str
    description: str
    risk: str
    # always = 随 SkillToolset 始终装配；per_turn = 由对话开关决定
    mount: str
    params: list[BuiltinToolParamOut] = Field(default_factory=list)


class BuiltinToolListOut(BaseModel):
    items: list[BuiltinToolOut]
    groups: list[BuiltinToolGroupOut]
    total: int


class BuiltinToolRunIn(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


class BuiltinToolRunOut(BaseModel):
    tool: str
    result: Any
