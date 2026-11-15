# API 接口参考

Base URL：`http://127.0.0.1:18080/api/v1`。认证：`Authorization: Bearer <token>`，token 取自 `POST /auth/login`（email + password）。角色为 viewer 时写操作返回 403。

## 对话（/chat）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /chat/sessions | 会话列表，含消息数、附件数、产出物个数（artifact_count）等聚合 |
| POST | /chat/sessions | 新建会话 |
| GET/PATCH/DELETE | /chat/sessions/{id} | 会话详情 / 重命名 / 删除 |
| PATCH | /chat/sessions/{id}/flags | 置顶、归档 |
| POST | /chat/sessions/{id}/fork | 从会话分叉 |
| GET | /chat/sessions/{id}/export | 导出会话 |
| POST | /chat/sessions/{id}/share | 生成分享令牌（GET /chat/shares/{token} 公开读） |
| POST | /chat/sessions/{id}/messages | 发送消息（SSE 流式返回） |
| POST | /chat/sessions/{id}/messages/edit-resend | 编辑并重发 |
| POST/GET | /chat/sessions/{id}/attachments | 上传/列出附件 |
| POST | /chat/sessions/{id}/stop | 停止生成 |

## 知识库（/knowledge）

- `GET /knowledge/docs`：文档列表（支持筛选）；`GET /knowledge/docs/{id}` 详情（含分块）。
- `POST /knowledge/upload`：multipart 上传 `.md/.txt/.pdf/.docx`，参数 title/path/tags 可选；入库自动分块与向量化。
- `POST /knowledge/docs`：手工新建（直接给内容）。
- `PATCH /knowledge/docs/{id}`（编辑）、`/enabled`（启停）、`/move`（移动）；`DELETE /knowledge/docs/{id}`。
- `GET /knowledge/docs/{id}/source`：下载上传原件。

## 工具与审批

- `GET /tools/builtin`：内置工具目录与参数定义（工作流画布同源）。
- `POST /tools/builtin/{name}/run`：手动试跑工具。
- `GET /approvals?status=pending`：待办审批。
- `POST /approvals/{id}/approve/stream`：批准并恢复执行（ask_user 用它回传答案；SSE）。
- `POST /approvals/{id}/reject`：驳回。

## 其他资源

- 记忆：`/memory`（GET/POST/PATCH/DELETE）。
- 沙盒：`POST /sandbox/run` 手动执行代码；`GET /sandbox/executions` 执行记录。
- 工作流：`/workflows`（定义 CRUD、`/runs` 运行、`/runs/{uid}/stop` 停止、`/nodes/{nid}/run` 节点试跑）。
- 评测：`/evals`（数据集/用例/运行/门禁）。
- 模型：`/integrations/llm`（供应商配置与连通性测试）。
- 审计：`GET /audit`，写操作全覆盖（消息发送除外）。

## 约定

- 写操作全部落审计；纯查询类接口不进审计。
- SSE 流的事件帧包含正文增量、工具卡、审批暂停（approval_pending）等类型，前端按帧类型分发。
