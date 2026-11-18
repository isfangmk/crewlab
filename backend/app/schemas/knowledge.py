"""知识库 API 契约。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DocCreateIn(BaseModel):
    """新建一篇知识文档。"""

    title: str = Field(min_length=1, description="文档标题，至少 1 个字符")
    content: str = Field(min_length=1, description="文档正文内容，至少 1 个字符")
    title_en: str | None = Field(default=None, description="英文标题；留空表示未设置")
    url: str | None = Field(default=None, description="文档来源链接；留空表示无外部来源")
    path: str | None = Field(default=None, description="文档在知识库中的目录路径；留空表示根目录")
    tags: list[str] | None = Field(default=None, description="文档标签列表；留空表示不添加标签")


class DocUpdateIn(BaseModel):
    """更新一篇知识文档。"""

    title: str = Field(min_length=1, description="文档标题，至少 1 个字符")
    content: str = Field(min_length=1, description="文档正文内容，至少 1 个字符")
    title_en: str | None = Field(default=None, description="英文标题；留空表示未设置")
    path: str | None = Field(default=None, description="文档在知识库中的目录路径；留空表示根目录")
    tags: list[str] | None = Field(default=None, description="文档标签列表；留空表示不添加标签")


class DocMoveIn(BaseModel):
    """移动文档到指定目录。"""

    path: str = Field(description="目标目录路径，空字符串表示移到根目录")


class DocEnabledIn(BaseModel):
    """设置文档的启用状态。"""

    enabled: bool = Field(description="是否启用该文档，启用后才会参与知识检索")


class ScopePreviewIn(BaseModel):
    """编辑器预览助理知识范围：目录 + 文档集。"""

    paths: list[str] = Field(default_factory=list, description="预览包含的目录路径列表")
    doc_ids: list[str] = Field(default_factory=list, description="预览包含的文档 id 列表")
