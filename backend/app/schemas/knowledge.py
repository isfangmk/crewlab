"""知识库 API 契约。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DocCreateIn(BaseModel):
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    title_en: str | None = None
    url: str | None = None
    path: str | None = None
    tags: list[str] | None = None


class DocUpdateIn(BaseModel):
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    title_en: str | None = None
    path: str | None = None
    tags: list[str] | None = None


class DocMoveIn(BaseModel):
    path: str
