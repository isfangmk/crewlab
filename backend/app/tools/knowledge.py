"""知识库内置工具。"""

from __future__ import annotations

from app.services.knowledge_scope import KnowledgeScope
from app.services.knowledge_service import search_for_agent


async def query_knowledge(query: str, limit: int = 5) -> dict:
    """语义检索组织知识库，返回标题、路径、摘录与相关度。

    结果按相关度从高到低排列，序号从 1 开始；回答时应以 [序号] 标注引用条目。

    Args:
        query: 检索语句
        limit: 返回条数上限
    """

    return await search_for_agent(query, limit)


def scoped_query_knowledge(scope: KnowledgeScope):
    """把助理知识范围绑进 query_knowledge；工具名与文档保持不变。"""

    import functools

    @functools.wraps(query_knowledge)
    async def scoped(query: str, limit: int = 5) -> dict:
        return await search_for_agent(query, limit, scope=scope)

    return scoped
