# 五项改进：工具卡片对齐 + 澄清交互卡 + 时间注入 + 产出物计数 + 审计补全中文化

## Context

用户提出五项改进（已拍板范围：检索来源新组件覆盖 web_search + query_knowledge；审计写操作全接、每条消息发送除外）：

1. **web_search 卡片对齐 run_python**——现状两者其实已是同一张通用卡（`ToolCallSummaryBlock`，`MessageBubble.tsx:415-520`，折叠「参数/结果」，默认收起，回答用户疑问：run_python 确实如此）；差异在内容区：run_python 产出物走卡外 `InterpreterArtifacts`（`ToolCallBlock` 卡下方兄弟节点，MessageBubble.tsx:402-410），而检索来源 `SourcesSection` 内嵌在卡里（484-488）。改造=检索内容全部移出卡外，独立重设计展示组件。
2. **对话澄清卡片**——信息不明确/需补充时弹卡让用户选择或填写，回答回流智能体；改提示词引导。有现成骨架：审批暂停-恢复链路（`tool_approval.py` request_confirmation 哨兵 + `approval_pending` 帧 + `PendingApprovalCard` + `stream_decide` 经 `stream_adk_confirmation` 回灌 FunctionResponse 恢复执行）。
3. **提示词注入当前时间/时区**——现状完全没注入；`compose_persona`（`agent_service.py:139`）是 chat/审批恢复/playground/子助理全路径汇合点。
4. **会话列表新增「产出物个数」**——`list_session_stats`（`chat_service.py:366-536`）已有六条聚合（附件聚合 493-500 是现成模板），`artifacts.session_id` 可直接 group-by 计数。
5. **审计全覆盖 + 全中文**——缺口清单已摸清（chat 会话类 8 个、memory 全部、sandbox、workflow 停跑/节点试跑、eval gate、agent playground、llm test）；`Audit.tsx:38` ACTION_LABELS 缺 25+ 条目，详情弹窗直接显示英文原始 action。

**全局纪律（不变）**：禁 mock；中文注释只写「做了什么/为什么」；UI 按 `docs/UI设计规范.md`；禁全量测试；每阶段中文 commit；后端改动 `scripts/backend-restart.ps1` 重启 + healthz；`backend/.venv/Scripts/python.exe`；测试数据清理。

---

## 阶段 A：检索结果移出卡片（web_search + query_knowledge）

- `web/src/components/SourcesBlock.tsx` → 升级为独立 `SearchResults` 组件（可原文件重设计）：保留 `SourceItem`/`extractSources`/`mergeSources`/`collectToolSources` 数据抽取（31-73 行），渲染换成独立卡片区（序号、标题外链、来源徽标（Web/知识）、摘要 line-clamp、路径/评分小字）——形态对齐 `InterpreterArtifacts` 的「卡外产出物」地位。标题「搜索结果」+ 条数。
- `web/src/components/MessageBubble.tsx`：
  - `ToolCallSummaryBlock` 移除内嵌 `SourcesSection`（showSources 参数删除）；卡片只剩 头行 + 折叠「参数/结果」（现状即输入/输出）。
  - `ToolCallBlock`（385-413）在卡下方按 `extractSources(call.name, call.result)` 渲染 `<SearchResults>`（web_search / query_knowledge / 名含 search/knowledge 时）。
  - `AssistantBubble` 底部聚合来源区（333-349）换用同一 `SearchResults`（聚合逻辑保留）。
- 测试更新：`MessageBubble.test.tsx` 两个来源用例改为「检索内容在卡外独立区、卡未展开只见折叠收起态」；`SourcesBlock.test.tsx` 适配新渲染。

## 阶段 B：澄清交互卡（ask_user 工具 + 引导提示词）

**机制**（复用审批骨架，全程真实执行）：
1. 新内置工具 `ask_user`（`backend/app/tools/catalog.py` 注册 spec + registry；实现 `backend/app/tools/user_input.py`）：参数 `question: str`（必填）、`options: list[str]`（可选项，可空）、`fields: list[{key,label,placeholder?,required?}]`（可填表单项，可空）。返回值 = 用户回答 JSON `{selected, values}`。docstring 写明「需要用户选择或补充信息时使用」。
2. 暂停/恢复：`adk_runtime/tool_approval.py` 对 `ask_user` 特判——`before_tool_callback` 命中时 `tool_context.request_confirmation(hint=payload)`（payload 含 question/options/fields）+ 返回哨兵错误（同 41-73 行模式）；恢复时 `confirmed=True` 放行真实工具。用户答案写入 Approval 行（`approval_service.create_pending_approval` 的 payload 扩展 `answer` 字段，kind='user_input'），工具执行时经 `tool_call_id` 反查答案（仿 `code_interpreter.py:204-221` remember_pending 按 tool_call_id 外置存取先例）。
3. 帧与持久化：`stream_bridge.py:425-472` 的 `approval_pending` 帧 data 增 `kind: 'user_input'` 与 schema 字段；`AdkFramePersister`（`chat_service.py:1298-1346`）写 tool_card result 信封 `{"status":"pending_input", "approval_id", ...}`（历史回放可重渲染）。
4. 决策端点：扩展现有 `POST /approvals/{id}/approve/stream`（`approval_service.stream_decide:323`）接受可选 `answer`；`stream_adk_confirmation` 的 FunctionResponse `response` 带 `{confirmed, answer}`（`stream_bridge.py:733-744`），answer 同步落 Approval 行。
5. 前端：`MessageBubble.tsx` 新 `UserInputCard`（仿 `PendingApprovalCard:752`：2s 轮询 + `chatLiveBus` 回灌续跑流）：options 单选 chips（`fields` 有则加表单输入，必填校验）+「提交」；已答态显示「你的回答」只读回显。`chat.ts` dispatchFrame（527-570）与 `StreamCallbacks` 补 case；`ChatThread.tsx` 落消息处理（仿 onApprovalPending:1330）。
6. 提示词：`adk_runtime/agent_factory.py:24-33` `CHAT_AGENT_INSTRUCTION` 缺参规则改写——信息不明确、需要用户在选项中选择或补充具体信息时，调用 `ask_user`（给出清晰 question 与候选 options/fields），不要猜测或用占位参数继续；简单一句澄清可纯文本提问，涉及选择/填写用 ask_user。

**测试**：`backend/tests/test_ask_user.py`（真实会话：触发 ask_user → approval_pending → 带 answer 决策恢复 → 工具返回答案 → 助理续答；坏参/重复提交防护）；`MessageBubble.test.tsx` 增 UserInputCard 渲染/提交用例。

## 阶段 C：提示词注入当前时间/时区

- `agent_service.compose_persona`（`agent_service.py:139`）首段注入：
  `## 当前时间` + ISO 本地时间 + 时区标签（`app_settings_service.get_system_timezone`，observability.timezone）+ 星期。每回合现算（chat / 审批恢复 / playground / sub_agents 四路全覆盖）。
- 测试：compose_persona 单测断言含当天日期与时区偏移（不用 mock 时钟，断言「今天」）。

## 阶段 D：会话列表产出物个数

- `chat_service.list_session_stats` 加第 7 条聚合（照附件聚合 `chat_service.py:493-500` 模式，`artifacts` 表按 session_id count）→ `SessionStats.artifact_count`；`ChatSessionOut`（`schemas/chat.py:34-64`）+ `_session_out`（`api/chat.py:39-67`）加 `artifact_count`。
- `Sessions.tsx` 标题格附件徽章（485-493）旁加产出物徽章（Box/Package 图标 + N，同排同款样式）。
- 测试：后端列表接口断言 artifact_count；`Sessions.test.tsx` 徽章用例。

## 阶段 E：审计补全 + 全中文化

**补审计**（写操作全接，消息发送除外；纯查询类不进）：
| 端点 | action |
|---|---|
| POST /chat/sessions | `session.create` |
| PATCH /chat/sessions/{id} | `session.rename` |
| PATCH /chat/sessions/{id}/flags | `session.update`（detail 记 pinned/archived） |
| POST /chat/sessions/{id}/fork | `session.fork` |
| POST /chat/sessions/{id}/attachments | `session.attachment` |
| POST /chat/sessions/{id}/stop | `session.stop` |
| POST /chat/sessions/{id}/messages/edit-resend | `session.edit_resend` |
| POST/PATCH/DELETE /memory | `memory.create/update/delete` |
| POST /sandbox/run | `sandbox.run` |
| POST /workflows/runs/{uid}/stop | `workflow.run_stop` |
| POST /workflows/{id}/nodes/{nid}/run | `workflow.node_run` |
| POST /evals/runs/{uid}/gate | `eval.gate` |
| POST /agents/playground/stream | `agent.playground` |
| POST /integrations/llm/test | `llm.test` |

不进审计（代码注释注明原因）：`workflows/validate`、`knowledge/scope-preview`、`skills/parse`、`mcp prompts/get`（无状态查询）；对话消息发送（核心高频，消息已全量落 chat_messages）。

**中文化**（`web/src/pages/Audit.tsx`）：
- ACTION_LABELS 补全：上表新增 14 条 + 现缺的 `agent.rollback`、`knowledge.source_*`（5）、`eval.*`（13）、`publish.*`（4）、`trigger.*`（4）、`mcp.server_test`、`mcp.oauth_*`（3）、`workflow.*`（含动态 `workflow.run_{decision}`——按 decision 词典前缀兜底「工作流运行·通过/驳回」）。
- RESOURCE_LABELS 补全（eval_dataset/eval_case/eval_run、workflow、trigger、knowledge_source、publish_channel 等）。
- 详情弹窗动作/资源也走标签映射（现 652-653 显示原始串）；筛选下拉沿用标签映射。
- 测试：`backend/tests/test_audit_coverage.py` 扩展（抽查新审计点写操作后有 audit 行）；`Audit.test.tsx` 中文标签用例（无则建）。

---

## 执行顺序与提交

A（卡片对齐）→ B（澄清卡）→ C+D（时间注入 + 产出物计数，小项合并）→ E（审计）。每阶段：受影响测试 + tsc + eslint（后端改动则重启 + healthz），**中文 commit 共 4 个**。

## 验证

1. 后端：`pytest tests/test_ask_user.py tests/test_audit_coverage.py tests/test_session_stats*.py`（及相关既有文件）全绿；`compose_persona` 单测。
2. 前端：`vitest run` 相关文件（MessageBubble / SourcesBlock / Sessions / Audit / ChatThread）+ `tsc -b --noEmit` + eslint。
3. 手工冒烟：发一条需要澄清的指令看 ask_user 卡出现、选择/填写后助理续答；会话列表看产出物徽章；审计页看新动作全中文。

## 风险与边界

- ask_user 恢复链路深（ADK 暂停-恢复），严格沿审批骨架走，不自造通道；answer 经 FunctionResponse 与 DB 双写，工具只信 DB（防回放不一致）。
- 审计表增量受控（排除消息发送）；动作字符串不改历史命名，中文仅在展示层映射（历史行不迁移）。
- 时间注入每回合现算，不缓存；时区取系统设置（observability.timezone），缺省 UTC+08:00。