# AI 工作台 — 功能迭代文档

> 基线参考：`D:\project\github\ongrid`（仅 Agent / 对话 / 技能 / MCP / 知识库 / 模型配置 / 审批相关能力）  
> 目标仓库：`D:\project\github\ongrid-python3`  
> 文档版本：v0.3 · 2026-11-08

---

## 1. 产品定位

做一套 **AI 工作台**：以对话为中心，管理智能体、**Tool（工具）**、**Skill（技能）**、MCP、知识库与模型配置；运行时全部交给 **Google ADK**，业务后端只做 FastAPI 装配、配置持久化、前端 API 与流式桥接。

**明确不做**（ongrid 中的 AIOps / 基础设施能力）：设备与 Edge、K8s、拓扑、监控告警、日志链路、工作流编排、任务/产物中心、IM 双向通道、WebSSH 等。

### 1.1 Tool 与 Skill 不得混用

| 概念 | 英文 | 含义 | 前端入口 |
| --- | --- | --- | --- |
| 工具 | **Tool** | ADK `FunctionTool` / 内置工具 / MCP 工具（经 tool_search） | 侧边栏 **工具** `/tools` |
| 技能 | **Skill** | ADK `Skill` / `SkillToolset` | 侧边栏 **技能** `/skills` |

代码、API、UI、日志中禁止把 skill 叫成 tool，或把内置 tool 挂到 skills 页。

---

## 2. 范围界定（前端裁剪）

前端在 ongrid `web/` 上改：属于工作台的 **保留且不改交互契约**；不属于的 **整页删除**（含路由、Sidebar、api client、测试）。

### 2.1 保留（AI 工作台）

| 模块 | 路由 / 入口 | 说明 |
| --- | --- | --- |
| 首页对话 | `/` | 新建会话、选模型、进会话 |
| 会话线程 | `/chat/:sessionId` | 流式打字机、工具意图、工具卡、审批卡、正文 |
| 助理 | `/agents` | 智能体列表 / 创建 / 选用 |
| **工具（新增）** | `/tools` | **展示平台自带 Tool**（非 Skill、非 MCP 全量列表） |
| 技能 | `/skills`、`/skills/:key` | Skill 目录与详情 |
| MCP | `/mcp` | MCP Server 注册 / 探测 / 启停 |
| 知识库 | `/knowledge` | 文档入库、检索（向量） |
| 审批 | `/approvals`、`/approvals/:id` | 工具审批列表与详情（对接 ADK confirmation） |
| 登录 | `/login` | 最小可用认证 |
| 设置 · 模型 | `/settings/llm` | **对话模型列表唯一来源** |
| 设置 · 凭证 | `/settings/secrets` | MCP / 嵌入等密钥托管 |
| 设置 · 助理 | `/settings/agent` | 超时、输出语言等运行参数（去掉 ongrid 运行时版本切换） |
| 布局与 UI | Layout / Sidebar / Chat 组件 / `components/ui` | 仅保留被上述页面引用的部分 |

### 2.2 删除（非工作台）

- 仪表盘、设备/集群/拓扑/K8s、监控/日志/APM/Traces/告警
- 工作流 `/workflows`、日常任务页（原 `/tools` 日常工具）、产物 Pages、抓包
- Agent Observability、IM/通知、集成（Prometheus 等）、升级、WebSSH
- 用户组织管理可暂缓；P0 可用单用户 / 固定管理员登录

> 注意：原 ongrid `/tools` 是「日常工具」，本项目 `/tools` 改为 **内置 Tool 目录**，语义不同。

### 2.3 知识库 Repos

`/knowledge/repos`（代码仓同步）**暂不纳入 P0**，P1 再评估。P0 只做文档上传 + 向量检索。

---

## 3. 硬性约束（必须遵守）

### 3.1 `.cursor` 开发规范

见 `.cursor/rules/dev-standards.mdc`：

1. **禁止 DDD**；后端用 FastAPI 标准布局  
2. **中文注释**；重要业务路径打日志；禁止日志输出密钥/Token  
3. **禁止兜底与兼容 / 禁止兜底修补代码**：缺字段 / 契约不符必须显式失败；禁止 `or ""`、静默吞异常、为凑合而补丁  
4. **测试用真实数据与真实依赖**；连不上则 skip 并说明原因  
5. **禁止 uvicorn `--reload`**；改完后端 **必须** 用仓库提供的启停脚本重启  

包管理使用 **uv**（安装依赖走阿里云 PyPI 镜像）。PostgreSQL 用 WSL Docker 已有 `pgvector/pgvector:pg16` 镜像部署。

### 3.2 Agent 必须用 ADK，禁止自研运行时

以下能力 **禁止自研替代实现**，只允许调用 `google-adk`（及 ADK 官方文档推荐的配套类型）：

| 能力 | ADK 入口（以锁定版本为准） | 禁止事项 |
| --- | --- | --- |
| 智能体 | `LlmAgent` / `Agent`、`sub_agents` | 自研 agent loop / planner |
| 技能 | `SkillToolset`、`Skill`、`SearchSkillsTool`、`discovery_mode=LAZY` | 自研 skill 调度与注入 |
| 工具 | `FunctionTool`、`McpToolset`、ADK Toolset | 自研 tool registry / 执行器 |
| 对话 | `Runner.run_async`、Session / Event | 自研多轮编排与 tool-call 状态机 |
| 工具审批 | `require_confirmation` + ADK confirmation 事件流 | 自研审批状态机去绕过 ADK |
| MCP 工具懒加载 | ADK 提供的 tool search / deferred tools（见下） | 手写「搜索后注入 tools 列表」循环 |

**MCP 工具不得一次性进入智能体上下文**：初始只暴露搜索类管理工具；模型 `tool_search`（或当前 ADK 版本等价 API）命中后再加载并调用。实现方式：

1. 优先使用锁定版 `google-adk` 官方 deferred tools / `tool_search`  
2. 技能侧用 `SkillToolset(discovery_mode=LAZY)`  
3. **禁止**业务仓库内自研 BM25 索引 + callback 注入；若官方能力未覆盖，先升级 ADK，不写旁路执行器  

### 3.3 对话框模型列表

- UI 模型下拉 **只读**「配置 → 模型」（`/settings/llm`）中已配置且探测可用的模型  
- 禁止前端写死模型名、禁止旁路环境变量拼一份独立模型表  
- 会话创建/切换的 `provider + model` 必须能在配置中反查到，否则 **显式失败**

### 3.4 已知 ADK 风险（开工前验证）

**决议（已落地）：方案 B**

- ADK 会话：`DatabaseSessionService(postgresql+asyncpg://...)`，多轮事件持久化，重启可续。  
- 审批：`LlmAgent.before_tool_callback` 内对非 trusted MCP 业务工具调用 `ToolContext.request_confirmation()`；SSE 审批卡 + FunctionResponse 恢复仍走 ADK，禁止自研执行器。  
- MCP 工具本身不再设 `require_confirmation`（避免双重闸门）。

---

## 4. 技术栈

| 层 | 选型 | 说明 |
| --- | --- | --- |
| 前端 | React 18 + Vite + TS + Tailwind + Zustand + React Router | 从 ongrid `web/` 裁剪 |
| 后端 | Python 3.11+ / FastAPI / Uvicorn（无 reload） | 标准 `app/` 布局，非 DDD |
| Agent | **google-adk** | Agent / Skill / Tool / Runner / Confirmation / MCP |
| LLM | OpenAI 兼容（DeepSeek 等） | 配置存 PG；对话只读配置 |
| Embedding | HTTP 嵌入服务 `br-embedding`，维度 **2560** | 知识库向量 |
| 数据库 | **PostgreSQL + pgvector**（WSL Docker） | 业务表 + 向量；ADK session 按 §3.4 |
| MCP | ADK `McpToolset` | Tool 经 tool_search 后再用 |
| 流式 | **SSE token 增量**（最优打字机） | 见 §3.5；禁止前端假打字机轮询 |
| 包管理 | **uv** + 阿里云 PyPI | `uv pip install --index-url https://mirrors.aliyun.com/pypi/simple/` |
| 启停 | `scripts/backend-*.ps1` / `.sh` | 改代码后必须脚本重启 |
| 测试 | pytest + 真实 DeepSeek / 嵌入 / PG | 禁止假数据绿测 |

### 3.5 流式打字机与回合展示顺序（最优方案）

**传输**：SSE，正文用 `assistant_delta` **增量 token** 推送；前端直接 append 到同一条 assistant bubble。禁止用定时器假打字机，禁止等整段再一次性替换。

**同一回合强制顺序**（前后端一致）：

1. **工具调用意图**（`tool_intent`）：调用 Tool 前先输出意图说明  
2. **工具卡片**（`tool_start`/`tool_end`）或 **审批卡片**（`approval_pending`）  
3. **正文**（`assistant_delta`… → 回合结束）  

Skill 激活/检索若产生可见事件，不得冒充 Tool 卡片；MCP Tool 与内置 Tool 均走 Tool 卡片通道。

---

## 5. 工程结构（目标）

```text
ongrid-python3/
├── .cursor/rules/dev-standards.mdc
├── docs/
│   └── AI工作台-功能迭代.md          # 本文档
├── backend/
│   ├── pyproject.toml / requirements.txt
│   ├── .env.example                  # 无真实密钥
│   ├── app/
│   │   ├── main.py                   # FastAPI 入口
│   │   ├── config.py                 # 环境配置（显式校验）
│   │   ├── db.py                     # SQLAlchemy / asyncpg / pgvector
│   │   ├── logging_setup.py
│   │   ├── api/                      # 路由：chat / agents / skills / mcp / knowledge / models / approvals / auth
│   │   ├── schemas/                  # Pydantic 请求响应（契约失败即 4xx）
│   │   ├── services/                 # 薄服务：装配 ADK、读写配置、知识库切块嵌入
│   │   ├── adk_runtime/              # 仅 ADK 装配，无自研 loop
│   │   │   ├── agents.py             # LlmAgent 构建
│   │   │   ├── skills.py             # SkillToolset
│   │   │   ├── mcp_tools.py          # McpToolset + tool_search 装配
│   │   │   ├── runner.py             # Runner + confirmation 事件转发
│   │   │   └── models.py             # 从「配置-模型」解析 ADK model
│   │   └── models_orm/               # ORM：llm_provider、mcp_server、knowledge_*、approval_projection…
│   └── tests/                        # 真实依赖集成测
├── web/                              # 从 ongrid/web 复制后裁剪
│   ├── package.json
│   └── src/...
└── README.md
```

**分层原则（非 DDD）：** `api` 调 `services`，`services` 调 ADK / DB；不引入 domain/usecase/repository 术语与目录。

---

## 6. 功能迭代计划

### 里程碑 M0 — 工程脚手架（0.5～1 天）

- [ ] 初始化 `backend/` FastAPI + 配置校验 + 结构化日志  
- [ ] 接入 PostgreSQL，建最小表；确认 pgvector 扩展可用  
- [ ] 复制 ongrid `web/`，删掉 §2.2 页面与死链；Sidebar / routes 只留工作台  
- [ ] 前后端联通 `/healthz`；文档对齐 API 前缀（建议 `/api/v1`）

**验收：** 前端能打开登录后壳子；后端健康检查通过。

---

### 里程碑 M1 — 配置 · 模型 + 对话最小闭环（核心）

- [ ] **配置-模型**：CRUD Provider（base_url / api_key / models[] / default_model）、连通性探测  
- [ ] `GET /chat/models`：**仅**返回配置中的模型（供首页/对话下拉）  
- [ ] 会话 CRUD；选中模型写入会话；非法模型拒绝  
- [ ] ADK `Runner` 流式对话（无工具也可先通）  
- [ ] 前端 Home / ChatThread 对接新后端（保留原 UI，改 api client）

**验收（真实数据）：**

- DeepSeek：`https://api.deepseek.com`，模型 `deepseek-flash`  
- 对话下拉只有配置里的模型；未配置则列表为空且不可发非法模型  

**环境变量（本地 `.env`，禁止提交密钥）：**

```env
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=***          # 使用你提供的测试密钥，写入本地 .env
LLM_MODEL=deepseek-flash
DATABASE_URL=postgresql+asyncpg://...
```

---

### 里程碑 M2 — 技能 + 工具审批（ADK）

- [x] 技能落盘 / 入库，经 `SkillToolset` 挂到 Agent（`discovery_mode=LAZY`）  
- [x] 危险工具 `FunctionTool(..., require_confirmation=True)` 或 MCP `require_confirmation`  
- [x] SSE 推送 pending confirmation；前端行内 Approve/Reject 回传 ADK  
- [x] `/approvals` 投影列表（只读展示 + 决策入口，决策走 ADK，不自研执行）

**验收：** 触发需审批工具 → 对话框出现审批卡 → 同意后工具执行，拒绝后中止；全链路有业务日志。

---

### 里程碑 M3 — MCP + tool_search

- [ ] MCP Server CRUD / 探测 / tools cache（管理面）  
- [ ] 运行时：`McpToolset` **不**把全量 tool schema 塞进初始上下文  
- [ ] 使用 ADK `tool_search`（或锁定版本官方等价 API）搜索后再调用  
- [ ] 非 trusted 工具默认 `require_confirmation`

**验收：** 注册真实 MCP → Agent 初始上下文无全量工具列表 → 搜索命中后可调用；日志可区分 search / load / call。

---

### 里程碑 M4 — 智能体管理

- [ ] 用户自定义 Agent：name / instruction / 允许的 skill·mcp 范围 / 默认模型  
- [ ] 全部用 `LlmAgent` 实例化；列表页对接 `/agents`  
- [ ] 会话绑定 `agent_id`；切换 Agent 显式失败条件写清（进行中 turn 不可切等）

**验收：** 创建 Agent → 在对话选用 → 行为与 instruction / 工具范围一致。

---

### 里程碑 M5 — 知识库（PG 向量）

- [x] 文档上传、切块、调用嵌入服务写入 pgvector（维度 **2560**）  
- [x] 检索 API；通过 ADK 工具（官方检索 Tool / Skill，禁止自研 RAG loop）暴露给 Agent  
- [x] 前端 Knowledge 页对接（docs/upload/search；vault/repos 同步仍为 P0 外 501）  

**嵌入测试配置：**

```env
EMBEDDING_BASE_URL=http://10.241.100.240:16013
EMBEDDING_MODEL=br-embedding
EMBEDDING_DIM=2560
```

**验收：** 入库真实文档 → 向量检索命中 → 对话中可引用检索结果。

---

### 里程碑 M6 — 硬化与清理

- [x] 设置·模型：`/system-settings?category=llm` + `/integrations/llm/{test,validate-and-save,models,invalidate}` 已对接 `llm_providers`  
- [x] 设置·助理：`/system-settings?category=agent` + PUT  
- [x] 设置·凭证：`/secrets` + `/credential-types`（Fernet 由 AUTH_TOKEN 派生）  
- [x] 日志脱敏（Bearer / api_key / sk- 片段）  
- [x] 对话 stop、`GET /version`、助理 output_locale / llm_timeout 生效  
- [x] Skills 页去掉 Marketplace 扩展安装（P0 不做）  
- [x] 真实环境回归清单（见 §9，本地已实测：模型探测 / 对话 / web_search / 知识库检索）  
- [x] 更广前端死代码清零（未挂路由 AIOps/旧 settings 页已删；侧栏与 `routes.ts` 对齐 live 路由）  
- [x] README：启动方式（手动启停 uvicorn，无 `--reload`）

---

## 7. 核心数据流

```text
浏览器 Chat
   │  POST message / SSE
   ▼
FastAPI api/chat
   │  校验 session.model ∈ 配置-模型
   ▼
adk_runtime.runner
   │  Runner.run_async(LlmAgent)
   ├─ SkillToolset (LAZY / search_skills)
   ├─ MCP：tool_search → 再调用具体 MCP tool
   └─ require_confirmation → SSE pending → 用户决策 → 继续/中止
   ▼
事件投影 → PG（会话消息、审批单据）+ 前端渲染
```

知识库：

```text
上传文档 → 切块 → Embedding(br-embedding, 2560) → pgvector
Agent 检索工具（ADK）→ 回填上下文 → 生成回答
```

---

## 8. API 面（与现有前端对齐的目标清单）

保持 ongrid 前端已用的路径语义，后端用 FastAPI 重写：

| 域 | 关键接口 |
| --- | --- |
| Auth | 登录 / 当前用户 |
| Models | 设置 CRUD；`GET /chat/models` |
| Chat | sessions CRUD；发消息 SSE；stop；改 session model |
| Agents | list / get / create / update / delete |
| Skills | list / get；（执行若保留则经 ADK，不另起执行器） |
| MCP | servers CRUD；test/probe |
| Approvals | list / get；approve / reject（转发 ADK confirmation） |
| Knowledge | docs CRUD；search |

契约字段缺失或不符 → **4xx / 明确错误**，不做默认填充。

---

## 9. 测试策略

| 用例 | 真实依赖 | 通过标准 |
| --- | --- | --- |
| 模型配置探测 | DeepSeek API | probe 成功且模型进 `/chat/models` |
| 多轮对话 | DeepSeek + PG | SSE 完整；消息落库 |
| 工具审批 | ADK confirmation + 前端 | 批准执行 / 拒绝中止 |
| MCP tool_search | 真实 MCP Server | 初始无全量工具；搜索后可调 |
| 知识库 | 嵌入服务 + pgvector | 维度 2560；检索命中 |

无法连接依赖时：`pytest.skip("原因")`，禁止 mock 冒充通过。

---

## 10. 非目标（本阶段明确不做）

- ongrid Go 后端复用或 Eino runtime  
- 监控/设备/K8s/工作流/IM  
- 多租户组织体系（可后续加）  
- 自研 Agent 框架、自研 tool_search、自研审批执行器  

---

## 11. 建议实施顺序（开发排期）

1. M0 脚手架 + 前端裁剪  
2. M1 模型配置 + 对话（先打通价值闭环）  
3. M2 审批（安全闸门）  
4. M3 MCP + tool_search  
5. M4 Agent 管理  
6. M5 知识库  
7. M6 硬化  

---

## 12. 已确认决策（2026-11-08）

1. **认证**：P0 单管理员 `admin@local`，密码 = `AUTH_TOKEN`  
2. **知识库 Repos**：P0 不做  
3. **工作流**：删除  
4. **ADK**：`google-adk`（当前锁定环境 2.14.x）+ LiteLLM  
5. **Session**：方案 B — 业务 PG + ADK `DatabaseSessionService`；审批用 `before_tool_callback` + `request_confirmation`  

6. **包管理**：uv + 阿里云镜像  
7. **PG**：WSL Docker `pgvector/pgvector:pg16` → 宿主机 `54329`  
8. **启停**：`scripts/backend-*.ps1`；禁止 `--reload`  
9. **禁止兜底修补代码**  
10. **侧边栏新增「工具」**：内置 Tool 列表 `/tools`  
11. **全局助理页面引用**：会话落库 `page_context`（页面名 + 页面自声明字段），回合开始注入人设；不采集 DOM 文本 / URL / 表单值

## 13. 当前落地进度

- [x] M0：后端脚手架、PG、启停脚本、前端裁剪骨架、Tools 页  
- [x] M1：配置种子模型、`/aiops/models`、ADK 流式对话（意图→Tool 卡→正文打字机）  
- [x] M2：审批回传闭环 + 本地 Skill（`SkillToolset` LAZY + `/skills`）  
- [x] M3：MCP CRUD/探测 + `SearchableToolset`（MCP 工具不进初始上下文，搜索后加载）  
- [x] M4：Agent CRUD（内置 default + 用户助理；会话绑定 agent_id；人设进 LlmAgent instruction；MCP 按 tools 白名单筛选）  
- [x] M5：知识库向量（docs CRUD/upload/search + pgvector + `query_knowledge` 等内置 Tool；vault/repos 不同步）  
- [x] M6：设置模型/助理/凭证、web_search、stop/version、日志脱敏；残留死页可继续清理  
- [x] 契约复核（2026-11-09）：前端请求体与后端 Pydantic 契约逐字段对齐；`page_context` 落库并注入 prompt；删除指向不存在端点的死代码（`postMessage` / `updateSecret` / `deleteSetting` / 知识库 repos+SSH / refresh-token 机制）；删除必然失败的「内置知识库同步」按钮

---
