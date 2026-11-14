# 三大功能实施计划：助理版本+Playground → 评测 2.0 → MCP Resources/Prompts+Registry

## Context

产品已具备对话、助理、工具/技能/MCP、知识库、审批、触发器、Memory、子 Agent、发布、评测、沙箱、Artifacts、可观测。本轮补三个「上生产」必需的能力，对照业界（Langfuse / Open WebUI / LibreChat / MCP 规范）的差距：

1. **助理版本与 Playground** — 当前 `agent_service.update_user_agent` 原地覆盖（`agent_service.py:575-591`），旧值直接丢失；调人设只能开正式会话，污染上下文。
2. **评测 2.0** — 当前只有确定性断言判分（`eval_runner.py:114-158` 的 `judge`，注释明确写「不引入模型裁判」）；无 LLM-judge、无人工标注、无 CI 门禁、无对抗用例、无导出。
3. **MCP 完整化** — 当前只缓存 tools（`mcp_servers.tools_cache_json`）；`mcp` SDK 2.3.0 已支持 `list_resources/read_resource/list_prompts/get_prompt`，ADK 2.14.0 的 `McpToolset` 已有 `use_mcp_resources=True`，但都未接入。

**已定范围**（用户拍板）：
- MCP 只做 **Resources/Prompts + Registry 一键登记**；OAuth（浏览器跳转依赖）、MCP Apps（SDK 无协议支持）、Tasks（无便捷 API）**不进本轮**。
- 红队做 **对抗用例集**（复用现有评测管线 + 内置对抗库），不做完整红队框架。
- 助理版本**只做历史 + 回滚**，会话仍实时读当前配置，不做版本锁定。

**全局纪律**：无 mock 测试（真实依赖，连不上则 skip 并说明）；契约不符显式失败；中文注释只写「做了什么/为什么」；UI 严格按 `docs/UI设计规范.md`；每阶段跑受影响测试 + tsc + eslint，中文 `git commit`；后端改完用 `scripts/backend-restart.ps1` 重启（禁 `--reload`）；不使用浏览器测试。

---

## 阶段 A：助理版本与回滚 + Playground

### A1. 数据模型

新表 `user_agent_versions`（`backend/app/models_orm/agent_version.py`）：
- `id` Integer PK autoincrement
- `agent_name` String(128) FK `user_agents.name` ondelete=CASCADE + index
- `version` Integer（单调递增；UniqueConstraint("agent_name","version")）
- `snapshot_json` Text（整份 `UserAgentInput` 序列化，含 display_name/system_prompt/examples/knowledge_scope/skills/sub_agents）
- `change_summary` String(256) 默认 ""（可选备注）
- `created_by` Integer nullable
- `created_at` AwareDateTime server_default=now
- `restored_from_version` Integer nullable（回滚时记录来源版本）

在 `models_orm/__init__.py` import 注册；新表由 `main.py` lifespan 的 `Base.metadata.create_all` 自动建，无需动 `db_schema._REQUIRED_COLUMNS`。

### A2. 后端服务

新 `backend/app/services/agent_version_service.py`：
- `snapshot_agent_version(db, agent_name, snapshot: dict, *, created_by, change_summary="", restored_from_version=None) -> int` — 版本号取 `max(version)+1`，写入并返回版本号。
- `list_versions(db, agent_name) -> list[dict]`（version/created_at/created_by/change_summary/restored_from_version）
- `get_version(db, agent_name, version) -> dict`（含完整 snapshot）
- `rollback(db, agent_name, version, *, actor_user_id) -> ResolvedAgent` — 读快照 → 走 `agent_service.update_user_agent` 全量覆盖 → 再快照一版（`restored_from_version=version`）。

`agent_service.py` 改动：
- `create_user_agent` 成功后 `snapshot_agent_version`（版本 1）。
- `update_user_agent` 成功后 `snapshot_agent_version`。
- `agent_to_wire` 增加 `version: int`（当前最新版本号）与 `updated_at`。

### A3. 后端 API（`backend/app/api/agents.py`）

- `GET /agents/custom/{name}/versions` → `{items:[...], total}`（require_auth）
- `GET /agents/custom/{name}/versions/{version}` → 快照详情（require_auth）
- `POST /agents/custom/{name}/rollback` body `{version, change_summary?}` → `agent_to_wire`（require_not_viewer，审计 `agent.rollback`）
- `POST /agents/playground/stream` body `{agent: UserAgentInput, content, thinking_enabled, web_search_enabled, locale?}` → SSE（require_not_viewer；不落 `chat_messages`/`observation_turns`）

Playground 实现要点：
- 复用 `adk_runtime/stream_bridge.py:594` 的 `stream_adk_turn(..., session_service=InMemorySessionService())`（先例 `adk_runtime/workflow_node_impl.py:755-796` 的 `InMemoryRunner`）。
- 复制 `chat_service.stream_session_message` 的「解析模型 → compose_persona → build_agent_tool_bundle」链路，**去掉** `_add_message` / `observation_service.start_chat_turn` / `AdkFramePersister`。
- 会话 id 用一次性 uuid，`user_id="playground:{user_id}"`；SSE 包装复用 `api/chat.py:471` 的 `_sse_from_stream`。
- 草稿助理不落库：直接用 `UserAgentInput` 构造 `ResolvedAgent`（新增 `agent_service.resolve_draft_agent(db, body) -> ResolvedAgent`，复用 `validate_skill_binding`/`validate_scope`/`parse_examples` 等校验，只是不 insert）。

### A4. 前端

`web/src/api/agents.ts` 增：`listAgentVersions`、`getAgentVersion`、`rollbackAgent`、`playgroundStream(input, content, cbs, signal)`（SSE 消费照抄 `api/chat.ts` 的 `consumeSseResponse`/`dispatchFrame`/`StreamCallbacks`）。

`web/src/pages/Agents.tsx`：
- 编辑态 footer 加「版本历史」按钮（`RowAction` 尺寸），打开版本列表 Modal：版本号/时间/来源（回滚标记）/变更摘要 + 「回滚到此版本」`RowAction`（`ConfirmDialog` 二次确认）。
- 编辑态 footer 加「试跑」按钮，打开右侧 `AgentPlaygroundPanel`（抽屉/侧栏），**携带当前草稿**（AgentEditor 各 `useState` 组装成 `UserAgentInput`，不必先保存）。

新 `web/src/components/AgentPlaygroundPanel.tsx`：
- 顶部：模型下拉（复用 `ChatModelControls.ModelDropdown`）+ 思考/联网开关 + 清空按钮。
- 中部：消息列表（复用 `components/MessageBubble.tsx` 渲染 tool_card/tool_intent/delta）。
- 底部：输入框 + 发送/停止；每次发送清空上一轮（Playground 默认单轮试跑，可保留多轮但会话在内存）。
- 明确标注「试跑不落会话、不影响正式对话」。

UI 规范对齐：`§3.7 助理编辑器固定分区`（Playground 按钮放 footer 不动 Tabs）、`§2.5 详情弹窗`（版本列表 Modal 结构）、`§2.7 空状态`（无版本历史时 `EmptyStateCard`）、`§5.1 行内操作`（回滚用 `RowAction`，删除/复制用 `RowActionIcon`）。

### A5. 测试

- `backend/tests/test_agent_versions.py`：httpx 打真实服务（照 `test_agent_persona.py` 模式），unique name + `finally` 清理；断言 create 后版本 1、PATCH 后版本 2、rollback 后 GET 字段与 v1 一致、版本 3 的 `restored_from_version=1`。
- `backend/tests/test_agent_playground.py`：流端点断言 `assistant_delta`/`done` 帧；跑完比对 `chat_messages` 计数无新增（可用 `GET /chat/sessions` + `GET /chat/sessions/{id}/messages` 对照）。
- `web/src/pages/Agents.test.tsx` 增：版本历史弹窗渲染、回滚 ConfirmDialog 流程、Playground 面板开合与草稿携带（MSW `http.get('/api/v1/agents/:name/versions')`、`http.post('.../rollback')`、playground 流用 `HttpResponse.stream` 造 SSE）。

---

## 阶段 B：评测 2.0（LLM-judge + 人工标注 + CI 门禁 + 对抗用例集）

### B1. 数据模型（`backend/app/models_orm/eval.py` 扩展）

- `EvalJudgeConfig`（表 `eval_judge_configs`，1:1 绑 dataset）：`dataset_id` FK unique、`enabled` bool、`rubric` Text（评分标准）、`judge_provider`/`judge_model`（默认跟随评测目标）、`pass_score` float（阈值）、`created_at`/`updated_at`。
- `EvalJudgeScore`（表 `eval_judge_scores`）：`id` String32 PK、`result_id` FK `eval_case_results.id` ondelete=CASCADE、`dimension` String(64)、`score` float、`reason` Text、`judge_model` String、`created_at`。
- `EvalAnnotation`（表 `eval_annotations`）：`id` String32 PK、`result_id` FK、`annotator_user_id` Integer、`label` String(32)（good/needs_work/bad）、`comment` Text、`created_at`。
- `EvalCase` 加列 `kind` String(16) 默认 "normal"（`normal`/`redteam`）、`attack_type` String(64) 默认 ""（越狱/提示注入/工具滥用/…）、`safety_keywords_json` Text 默认 "[]"（出现即判失败的泄漏/合规词）—— 登记进 `db_schema._REQUIRED_COLUMNS`。
- `EvalDataset` 加列 `baseline_run_uid` String(32) nullable、`gate_max_regressions` Integer 默认 0、`gate_min_pass_rate` float 默认 0.0 —— 登记进 `_REQUIRED_COLUMNS`。

### B2. 后端服务

新 `backend/app/services/eval_judge_service.py`：
- `judge_result(db, result: EvalCaseResult, config: EvalJudgeConfig) -> list[EvalJudgeScore]` — 组装输入（`result.output` + `result.tool_names` + `chat_service.list_messages(db, result.session_id)` 取工具入参出参）→ `litellm.acompletion`（用 `llm_config.compose_litellm_model`/`resolve_model`/`provider_extra_body`）→ 要求 JSON 结构化输出（`response_format` 或 prompt 约束）→ 解析成维度分。解析失败显式记 `judge_model="parse_error"` 并落 reason，不静默吞。
- 判定优先级：确定性 `judge()`（复用 `eval_runner.judge`）→ LLM-judge 平均分 ≥ `pass_score`。

`backend/app/services/eval_runner.py` 改：`_run_case` 末尾若 dataset 启用了 judge，调 `eval_judge_service.judge_result` 并落分；`_run_detached` 汇总时把 judge 分计入 `passed/failed`（judge 不过也算 failed，reason 里写明）。

新 `backend/app/services/annotation_service.py`：队列 CRUD、`claim_next`（认领下一条未标注结果）、`submit_annotation`、`list_annotations`、一致性统计（多人标注同一 result 的一致率）。

新 `backend/app/services/eval_gate_service.py`：`evaluate_gate(db, run_uid) -> dict` — 调 `eval_service.compare_runs` 对比 `dataset.baseline_run_uid`，按 `gate_max_regressions`/`gate_min_pass_rate` 判 `pass|fail`，返回 `{pass, regressions, pass_rate, reasons}`。

红队：复用现有 `EvalCase` + 新增 `kind="redteam"`；`eval_runner._run_case` 对 redteam 用例额外跑 `safety_keywords` 断言（命中即 failed）；内置对抗用例库 `backend/app/services/redteam_library.py`（越狱、提示注入、工具滥用、数据外泄各 3-5 条，创建数据集时可一键导入）。

### B3. 后端 API（`backend/app/api/evals.py`）

- `GET/PUT /evals/datasets/{id}/judge-config`（require_not_viewer）
- `GET /evals/runs/{run_uid}/results/{result_id}/judge-scores`（require_auth）
- `POST /evals/annotations`、`GET /evals/runs/{run_uid}/annotations`、`GET /evals/annotations/queue`（claim）、`POST /evals/annotations/{id}`（require_not_viewer，审计 `eval.annotation`）
- `POST /evals/runs/{run_uid}/gate` → `{pass, regressions, pass_rate, reasons}`（require_auth）
- `GET /evals/runs/{run_uid}/export?format=csv|json`（require_auth，审计 `eval.export`，脱敏走 `app/services/redaction.py`）
- `POST /evals/datasets/{id}/redteam/seed`（require_not_viewer，导入内置对抗库）

### B4. CI 门禁

新 `scripts/eval-gate.ps1`：入参 `dataset_id`、`baseline_run_uid`、`--fail-on-regressions`；流程 = 创建 run → 轮询完成 → 调 gate → 非零退出码。鉴权用现有 `api_tokens` Bearer（`auth_service.resolve_bearer`）。文档写进 `docs/产品功能迭代计划.md` 落地记录。

### B5. 前端

`web/src/api/evals.ts` 增：`getJudgeConfig/saveJudgeConfig`、`listAnnotations/claimNextAnnotation/submitAnnotation`、`evaluateGate`、`exportEvalRun`、`seedRedteamCases`。

`web/src/pages/EvalDataset.tsx`：
- `RunResultDialog` 结果表加「评分」列（确定性 + judge 分 + 阈值徽章），点行展开 judge 维度分与理由；结果行加「标注」`RowAction`（打开标注弹窗，选 label + comment）。
- 顶部 Tabs 拆「用例 / 运行记录 / 标注队列 / 门禁」；门禁 Tab 展示 baseline 选择、阈值配置、最近一次 gate 结果（pass/fail 徽章 + 回归清单）。
- 用例表加 `kind` 列（普通/对抗 chip）+ `attack_type`；「导入对抗用例」按钮。
- `RunDialog` 加「启用 LLM 评分」开关 + rubric 编辑。
- 顶部「导出」按钮（CSV/JSON）。

`web/src/pages/Evals.tsx`：数据集行加 gate 状态徽章（若配置了 baseline）。

UI 规范对齐：`§2.4.2 排序与时间`（标注队列、结果表都要时间排序）、`§2.5 详情弹窗`（judge 分详情）、`§4.1 状态语义`（pass/fail/needs_work 用 `StatusPill`）、`§5.1 行内操作`。

### B6. 测试

- `backend/tests/test_eval_judge.py`：真实调用 judge 模型跑 1 条短输出（连不上 skip）；解析失败分支用畸形返回单测。
- `backend/tests/test_eval_annotations.py`：认领/提交/一致性统计全链路。
- `backend/tests/test_eval_gate.py`：造两轮 run，断言回归超阈值时 `pass=false` 且 reasons 含回归用例。
- `backend/tests/test_eval_redteam.py`：导入对抗库 → 跑 1 条越狱用例 → 断言 `safety_keywords` 命中判 failed。
- `backend/tests/test_eval_export.py`：CSV/JSON 导出字段完整 + 脱敏。
- `web/src/pages/EvalDataset.test.tsx` 增：judge 分列、标注弹窗、门禁 Tab、导入对抗用例、导出按钮。

---

## 阶段 C：MCP Resources / Prompts + Registry 一键登记

### C1. 数据模型

`mcp_servers` 加列（登记 `db_schema._REQUIRED_COLUMNS`）：
- `resources_cache_json` Text 默认 "[]"（`[{uri, name, description, mime_type}]`）
- `prompts_cache_json` Text 默认 "[]"（`[{name, description, arguments:[{name, description, required}]}]`）
- `resource_templates_cache_json` Text 默认 "[]"
- `last_probed_at` AwareDateTime nullable

OAuth/Apps/Tasks **不加列**（本轮不做）。

### C2. 后端服务（`backend/app/services/mcp_service.py` 扩展）

- `test_server` 扩展：连上后除 `list_tools` 外，再 `list_resources`/`list_resource_templates`/`list_prompts`，一并写入三个 cache 列；任一失败不整体判 error（记 `last_error` 局部信息，tools 仍是主判据）。
- 新 `async list_resources(db, server_id) -> list[dict]`、`async read_resource(db, server_id, uri) -> dict`、`async list_prompts(db, server_id) -> list[dict]`、`async get_prompt(db, server_id, name, arguments) -> dict` — 复用 `connection_params_for_server` + `mcp.client.session.ClientSession` 的 `list_resources`/`read_resource`/`list_prompts`/`get_prompt`。
- 运行时装配：`build_searchable_mcp_toolsets` 里给 `McpToolset` 加 `use_mcp_resources=True`（ADK 2.14.0 自动注入 `load_mcp_resource` 工具），让模型可按需读资源。

新 `backend/app/services/mcp_registry_service.py`：
- `search_registry(query, limit, cursor) -> dict` — httpx 调 `https://registry.modelcontextprotocol.io/v0/servers?search=&limit=&cursor=`；网络不可达显式抛错（前端显示错误横幅）。
- `registry_entry_to_server_input(entry) -> dict` — 映射 `remotes`（Streamable HTTP/SSE URL）或 `packages`（npm/pypi，转 endpoint 提示）到 `McpServerInput` 预填结构。

### C3. 后端 API（`backend/app/api/mcp.py`）

- `GET /mcp/servers/{id}/resources`（require_auth）
- `GET /mcp/servers/{id}/resources/read?uri=`（require_auth）
- `GET /mcp/servers/{id}/resource-templates`（require_auth）
- `GET /mcp/servers/{id}/prompts`（require_auth）
- `POST /mcp/servers/{id}/prompts/get` body `{name, arguments}`（require_auth）
- `GET /mcp/registry/servers?search=&limit=&cursor=`（require_auth，代理 + 结果映射）

`server_to_wire` 增 `resources_count`/`prompts_count`/`last_probed_at`。补 `test` 端点审计 `mcp.server_test`（当前缺失）。

### C4. 前端

`web/src/api/mcp.ts` 增：`listMcpResources`/`readMcpResource`/`listMcpPrompts`/`getMcpPrompt`/`searchMcpRegistry`。

`web/src/pages/Mcp.tsx`（严格按 UI 规范 `§3.9 MCP 服务卡片`）：
- `ServerRow` 摘要行加「资源 N · 提示词 N」（沿用分隔点串联，不堆胶囊）。
- 展开区在「工具清单」之后加两个分组：「资源」（uri/名称/类型 + 「读取」`RowAction` 打开 `CodeBlock` 显示内容）与「提示词」（名称/参数表 + 「使用」`RowAction` 弹窗填参数后取 prompt 消息）。
- 顶部加「从 Registry 添加」按钮，打开 Modal：搜索框 + 结果列表（名称/描述/传输方式）→ 点「添加」预填 `ServerEditor` 表单。
- `ServerEditor` 不动主结构，只让 Registry 预填能填入 name/endpoint/transport。

UI 规范对齐：`§3.9`（展开区分组标题、错误横幅共用一条、`RowActionIcon` 尺寸）、`§2.7 空状态`（无资源/提示词时 `EmptyStateCard`）、`§3.5 可搜索选择器`（Registry 搜索框）。

### C5. 测试

- `backend/tests/test_mcp_resources_prompts.py`：改造 `scripts/mcp-demo/server.py` 或用 `mcp/client/_memory.py` 内存传输起本地 MCP server，断言 `test_server` 写入三个 cache 列、`read_resource`/`get_prompt` 返回结构；连不上 skip。
- `backend/tests/test_mcp_registry.py`：`search_registry` 真实打 registry（不可达 skip）、`registry_entry_to_server_input` 映射单测。
- `web/src/pages/Mcp.test.tsx` 增：展开区资源/提示词分组、读取弹窗、Registry 搜索与预填。

---

## 执行顺序与提交节奏

A → B → C，**每阶段结束**：跑受影响的 backend pytest（只跑新增/相关文件，禁全量）+ 前端 `npx tsc -b --noEmit` + 相关 vitest + eslint，然后中文 `git commit` 一次。后端改完用 `scripts/backend-restart.ps1` 重启并确认 `/healthz` 200。

改动面预估：
- 阶段 A：后端 1 新表 + 2 服务新增/改 + 1 API 改；前端 2 组件新增/改 + 1 API client；测试 3 文件。
- 阶段 B：后端 4 新表/加列 + 4 服务 + 1 API 改；前端 2 页面改 + 1 API client；测试 6 文件；CI 脚本 1。
- 阶段 C：后端 1 表加列 + 2 服务新增/改 + 1 API 改；前端 1 页面改 + 1 API client；测试 3 文件。

## 验证方式（每阶段）

1. `cd backend && uv run python -m pytest tests/test_<新文件>.py -q` 全绿（连不上真实依赖的用例 skip 并说明）。
2. `cd web && npx tsc -b --noEmit && npx vitest run <相关测试文件>` 全绿；`npx eslint <改动文件>` 通过。
3. 后端重启 + `curl /healthz` 200；Playground 流端点用 `curl -N` 看到 `assistant_delta`/`done` 帧且 `chat_messages` 无新增。
4. `git commit`（中文主题化 message）。

## 风险与边界

- **Playground 不落库**：绕开 `stream_session_message` 直调 `stream_adk_turn` + `InMemorySessionService`；工具审批在试跑中一律「自动拒绝」并提示（试跑不写审批记录），避免留下悬挂审批。
- **LLM-judge 不可复现性**：rubric + 结构化输出 + 留存 `judge_model` 与原始 reason；判分失败显式落 `parse_error`，不静默吞。测试只跑 1-2 条真实调用，其余走解析单测。
- **Registry 网络依赖**：`registry.modelcontextprotocol.io` 不可达时显式报错并给重试入口，不做静默降级；测试 skip。
- **对抗用例集**：只做「输入 + 安全断言」复用现有管线，不自研攻击生成器；内置库来源需在 `redteam_library.py` 注释里写明。
- **版本回滚影响所有会话**：会话实时读助理配置（`chat_service.py:1478`），回滚立即生效——这是本轮接受的口径，回滚弹窗里必须明示。
- **不使用浏览器测试**。