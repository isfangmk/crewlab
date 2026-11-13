# 五大功能实施计划（① 解释器+Artifacts → ② 自动化触发 → ⑤ Memory → ③ 子Agent → ④ 对外发布）

## Context

产品定位「以对话为中心的 Agent 工作台」。五个大功能的底座在本轮已铺好：沙盒执行器双实现（local/docker）、文件落盘方案、向量存储抽象（namespace 可扩展）、工作流后台执行范式（`workflow_runner._spawn`）、审计/可观测记账、分享免登录模式。本计划按依赖与价值排序为 5 个可独立交付的阶段，每阶段完成后单独提交并跑全量回归。

**全局纪律**：无 mock 测试（真实依赖集成）；契约不符显式失败；审计留痕；UI 按 `docs/UI设计规范.md`；每个阶段走 `git commit`。

---

## 阶段 A：对话内代码解释器 + Artifacts

**数据模型**：新表 `artifacts`（`models_orm/artifact.py`）：`id(uuid)`、`session_id(FK chat_sessions)`、`message_id`、`kind('chart'|'table'|'file'|'text')`、`title`、`mime`、`payload_json`（echarts option/表格数据/文本）、`data_bytes`(可空 BLOB，文件类)、`created_at`。加进 `models_orm/__init__.py`。

**后端**：
1. `app/tools/code_interpreter.py`（新）：`run_python(code: str, purpose: str = "") -> dict` async 函数，声明 `tool_context: ToolContext` 取 session id。内部：复用 `workflow_node_impl.SANDBOX_SCRIPT_TEMPLATE` 思路构造脚本（state 传空、marker+JSON 信封），信封约定新增 `artifacts` 数组——用户代码调用注入的 `emit_chart(option,title)` / `emit_table(rows,title)` / `emit_file(name,b64)` helper 把产出带出（base64 走 stdout 信封，规避沙盒无文件取回通道的缺口）。调 `sandbox_service.run_code(ctx=SandboxContext(source="chat", session_id=...))`（`sandbox_service.py` 增 `SOURCE_CHAT="chat"` 常量）。返回 dict：`{status, stdout_tail, artifacts:[{kind,title,mime,payload}], error}`。
2. 注册：`app/tools/catalog.py` `BUILTIN_TOOL_SPECS` 加条目（group=`"code"`、risk=`"safe"`——执行器本身是受控沙盒）+ `_BUILTIN_TOOL_REGISTRY` 映射；`builtin_tool_service._BUILTIN_TOOL_GROUPS` 加 `"code": "代码解释器"` 分组；`app/tools/catalog.py` 的 workflow_tool_names 装配点把新工具并入 `build_knowledge_tools`（`toolset.py:26-41`）。
3. Artifacts 服务：`app/services/artifact_service.py`——`save_from_tool_result(session_id, message_id, artifacts) -> list[Artifact]`、`list_by_session`、`get_bytes`（下载复用 `api/chat.py:418-439` 的字节 Response 模式）。新端点 `app/api/artifacts.py`：`GET /chat/sessions/{id}/artifacts`、`GET /artifacts/{id}/download`（require_auth）。
4. 消息流挂接：`chat_service.AdkFramePersister` 在 `tool_end` 帧处理分支（chat_service.py:1135-1173）检测 `tool.name == "run_python"` 且 result 含 artifacts → 调 artifact_service 落库，并把 `artifact_ids` 写进 tool_card result；历史回放经 `message_to_wire` 透出。

**前端**：
1. `MessageBubble.tsx#ToolCallSummaryBlock`：`call.name === 'run_python'` 时特化渲染——代码块 + artifact 卡片（chart 类用 `ChartCard`/`ChartCanvas` 渲染 payload，table 用简单表格，file 给下载链接 `GET /artifacts/{id}/download`）。
2. 会话页右栏：`ChatThread.tsx` 布局改 `flex-row`，加停靠式 Artifacts 面板（仿 `components/workflow/WorkflowInspector.tsx:121-130` 的可拖拽停靠卡），列出本会话 artifacts（按 kind 过滤/预览/下载）。图表渲染复用 `ChartCard.tsx` 的 `ChartCanvas`。
3. 工具目录页 `/tools` 自动带出新分组（builtin_tool_service 已支持）。

**测试**（真实依赖，无 mock）：`tests/test_code_interpreter_tool.py`——真调 `run_python` 跑真实代码（local 执行器）：算术、emit_chart 产出 payload 结构、emit_file 字节还原、维度/异常分支、`SOURCE_CHAT` 落库到 sandbox_executions；API 集成：会话 artifacts 列表与下载字节一致。

---

## 阶段 B：自动化触发器（Cron + Webhook）

**数据模型**：`models_orm/trigger.py`——`TriggerRule(id, name, kind('cron'|'webhook'), cron_expr, target_kind('workflow'|'agent'), workflow_id?, agent_id?, prompt?, enabled, max_retries, webhook_token_hash, created_by, created_at)`；`TriggerRun(id, trigger_id, status('running'|'succeeded'|'failed'), attempt, error, started_at, finished_at, ref_id)`（工作流 run_uid 或会话 id）。

**后端**：
1. `app/api/triggers.py`：CRUD（require_not_viewer，admin 限删）+ `POST /triggers/webhook/{token}/fire`（公开端点，豁免方式=不声明鉴权依赖，先例 `GET /chat/shares/{token}`）。
2. `app/services/trigger_service.py`：cron 解析用 **croniter**（pyproject 新依赖，uv 加）；`compute_next_run`；执行分发——workflow → `workflow_runner.start_run(triggered_by=f"trigger:{id}", source="published")`（后台异步，现成范式）；agent → 进程内 `create_session` + `async for frame in stream_session_message(...)` 消费至 done（`set_request_id(f"trigger-{id}-...")`），遇 `approval_pending` 帧记「等待人工审批后中止」。
3. 调度器：`app/services/trigger_scheduler.py`——lifespan 启动的 asyncio 循环任务（复用 `workflow_runner._spawn` 强引用模式），每 30s 扫 enabled cron 触发器，到点（croniter 上次/下次比较）即派发；同 TriggerRun 写入 + 重试（attempt ≤ max_retries，指数退避）。
4. 审计：`trigger.create/update/delete/fire`（actor_user_id 可 None）；失败重试口径记在 TriggerRun.attempt。

**前端**：新页 `web/src/pages/Triggers.tsx`（TableCard 表格：名称/类型/目标/计划/状态/最近运行/操作；创建弹窗选目标——工作流下拉或助理+prompt；webhook 展示一次性 URL）；路由/App.tsx/Sidebar（工作流分组加「触发器」项）/routes.ts 命令面板。

**测试**：croniter 到点计算单测；触发器 CRUD 集成；webhook fire 真后端触发真实工作流 run（断言 run_uid 产生与状态流转）；agent 触发集成（消耗 1 轮真实对话）。

---

## 阶段 C：跨会话 Memory

**数据模型**：`models_orm/memory.py`——`MemoryItem(id, scope_agent_id(可空=全局), content(Text), source_session_id?, enabled, created_by, created_at)`。`vector_store/__init__.py` 加 `MEMORY_NAMESPACE = "memory_items"`。

**后端**：
1. `app/services/memory_service.py`：`remember(content, agent_id?)`（入库 + `embed_text` 写 `MEMORY_NAMESPACE`，ref=str(id)）、`recall(query, agent_id?) -> list`（`embed_text` + `vs.search` + 回业务库 join enabled 过滤，复用 `search_docs` 向量通道模式 knowledge_service.py:581-614）、`forget(id)`（删行+删向量）、`list`。`VECTOR_BACKEND=none` 时显式失败（`raise_vector_store_unavailable`）。
2. 对话工具：`app/tools/memory.py`——`remember_memory(content)` / `recall_memory(query)` 两个内置工具（catalog 注册，group="memory"），模型可在对话中主动存取记忆；recall 结果按 agent scope 过滤（本助理或全局）。
3. API：`app/api/memory.py` CRUD + 检索（require_not_viewer）。
4. 导航：侧栏「记忆」占位（`Sidebar.tsx:366 planned:true`）转正式页面。

**前端**：新页 `web/src/pages/Memory.tsx`（TableCard 列表：内容/归属助理/来源会话/时间/启停；新增弹窗；删除 ConfirmDialog）；按新页面注册清单接入（App.tsx 路由、Sidebar、routes.ts）。

**测试**：真实嵌入写入 + 语义检索命中（同义改写 query）、scope 过滤、`VECTOR_BACKEND=none` 显式失败、删除后检索不命中、对话工具集成（真实会话让模型记住/回忆——消耗 1-2 轮对话）。

---

## 阶段 D：多 Agent 协作（子 Agent 委托）

**数据模型**：`models_orm/agent.py` 加列 `sub_agents_json`（JSON 数组，存子助理 name 列表；NULL=无委托）——用既有 `db_schema.py` `_REQUIRED_COLUMNS` 幂等补列机制。

**后端**：
1. `agent_service.py`：`ResolvedAgent` 加字段；保存校验 `validate_sub_agents`（存在性、防自引用、**防环**——沿父子链 DFS）；`agent_to_wire`/wire 透出。
2. 装配：`agent_factory.build_chat_agent` 加 `sub_agents` 参数与**参数化 ADK agent name**（现在硬编码 `"workbench_chat"`，agent_factory.py:122——子 Agent 各用 `agent:<name>`）；子 Agent 递归用各自能力装配（知识范围/技能/工具独立）。两处装配点同改：`chat_service.py:1495-1518` 与 `approval_service.py:428-446`。
3. ADK 原生委托：`LlmAgent(sub_agents=[...])` 自动生成 `transfer_to_<子名>` 工具，委托即 ADK Runner 内部切换（无自研编排）；委托过程经现有 tool/事件帧自然流过可观测。
4. 边界：委托深度由树结构天然限制（防环校验）；`when_to_use`（字段已存在）作为委托判断依据写进子 Agent description。

**前端**：助理编辑器（Agents.tsx）加「子助理」分组（多选已保存助理，排除自身）；详情弹窗展示委托列表。

**测试**：真后端集成——创建父子助理（父无知识范围、子绑定「运维文档」scope）→ 流式会话让父委托子查文档 → 断言命中范围内文档；防环校验 400；委托链在 observability 有 trace。

---

## 阶段 E：对外发布（OpenAI 兼容 API + 独立聊天页）

**数据模型**：`models_orm/channel.py`——`PublishedChannel(id, name, agent_id, key_hash unique, expires_at?, enabled, created_by, created_at)`（签发复刻 share_service 模式：token_urlsafe 一次性返回 + sha256 落库）；`observation_turns` 加可空列 `channel`（`db_schema` 幂等补列）。

**后端**：
1. `app/api/publish.py`：
   - 管理端点（require_admin）：channels CRUD + 签发（原文一次性返回）+ 启停。
   - `POST /v1/chat/completions`（公开路由，豁免=不声明鉴权依赖）：`Authorization: Bearer pk-…` → 哈希查 channel（enabled/过期校验，401 同 deps.py 风格）→ messages 数组取最后 user 消息 → 每 channel 一个持久会话（上下文延续）→ 进程内消费 `stream_session_message` → **OpenAI chunk 格式映射**：`assistant_delta→delta.content`、`thinking_delta` 丢弃、tool/approval 帧丢弃（对外只回文本）、`done.usage`→`stream_options.include_usage` 的最终 chunk、终止 `data: [DONE]`；非流式聚合全文返回。记账：`observation_turns.channel` 落渠道名。
   - `GET /v1/models`（列已发布渠道的模型，OpenAI 兼容）。
2. 独立聊天页（P1 减项，若时间紧延后）：`/embed/{key}` 公开路由 + 公开流端点，复用 ShareView 模式。

**前端**：新页 `web/src/pages/Publish.tsx`（渠道列表：名称/助理/Key 掩码/用量(tokens)/状态/操作；创建弹窗选助理+有效期，Key 一次性展示+复制）；侧栏治理分组加「发布管理」；用量卡片可在渠道详情展示（observation 按 channel 过滤）。

**测试**：签发后真实调用 `/v1/chat/completions`（流式与非流式各一，消耗 1-2 轮 DeepSeek）断言 OpenAI chunk 结构与 usage；错 key 401；停用渠道 401；observation 按渠道可查。

---

## 执行顺序与提交节奏

按 A → B → C → D → E 顺序实施（无硬依赖，按价值排序），**每个阶段结束：跑全量 pytest + tsc + vitest，git commit 一次**。预计改动面：

- 阶段 A：后端 6 文件新增/修改 + 前端 4 文件 + 测试 1 文件
- 阶段 B：后端 4 新增 + main.py 挂载 + 前端 1 新页 + 注册 4 处 + 测试
- 阶段 C：后端 4 新增 + 前端 1 新页 + 工具 1 文件 + 测试
- 阶段 D：后端 3 修改 + 前端编辑器 + 测试
- 阶段 E：后端 3 新增 + main.py + observation 加列 + 前端 1 新页 + 测试

## 验证方式（每阶段）

1. `cd backend && uv run python -m pytest -q` 全绿（含各阶段新用例）
2. `cd web && npx tsc -b --noEmit && npx vitest run` 全绿
3. 后端重启 + 浏览器实测核心链路（解释器跑图表、触发器到点执行、记忆存取、父子委托、OpenAI 端点 curl 流式）
4. git commit（主题化 message，沿用中文惯例）

## 风险与边界

- 阶段 A 的 local 执行器是无隔离模式（本机 dev）；生产切 docker 配置即可，代码不动
- 阶段 B 的无人值守会话遇审批会中止并记录（绝不自动批准，符合 G1 口径）；建议绑定只读工具助理
- 阶段 E 的 OpenAI 端点不暴露工具/审批帧（对外只回文本），工具型助理不适用对外发布——文档写明
- 全程不打断：每阶段完成即提交并直接进入下一阶段
