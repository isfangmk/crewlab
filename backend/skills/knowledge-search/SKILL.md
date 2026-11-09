---
name: knowledge-search
description: >-
  检索组织知识库。用户问手册、故障排查、内部约定或已上传文档内容时使用 query_knowledge。
metadata:
  author: ai-workbench
  category: 知识
  version: "1.0"
  risk: safe
  scope: manager
  adk_additional_tools:
    - query_knowledge
---

# 知识库检索

需要引用组织文档时调用 `query_knowledge`，基于返回的标题、路径与摘录作答；无命中则明确说明未找到，禁止编造。
