"""内置 Tool 列表契约（不是 Skill）。"""

from __future__ import annotations

from pydantic import BaseModel


class BuiltinToolOut(BaseModel):
    name: str
    description: str
    risk: str


class BuiltinToolListOut(BaseModel):
    items: list[BuiltinToolOut]
    total: int
