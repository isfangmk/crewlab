# 后端全接口并发压测（Locust）

不手工枚举端点：从 FastAPI 的 OpenAPI schema 反射全部路由，分类后对真实后端 +
真实数据库 + 真 Redis 发请求，按端点收集响应时间（p50/p95/p99/max）。

## 目录

- `discovery.py`：反射 `create_app().openapi()`，把端点分类为
  `read` / `write` / `write_excluded` / `excluded`，并从 schema 生成最小合法 body
  与必填 query 样例。
- `seed.py`：造 `__perf__` 专属数据并产出 `manifest.json`（真实 id 池）。
- `locustfile.py`：Locust 任务集 + event hook 计时；写报告到 `PERF_REPORT`。
- `env.py`：读取 `.env` 与登录拿 token（绝不打印 token / 凭据）。
- `manifest.json`：seed 产出，随数据清理失效；重跑 seed 可再造。

## 用法

在 `backend/` 下：

```bash
# 1. 造数据（幂等：先清旧 __perf__ 数据）
.venv/Scripts/python.exe -m perf.seed

# 2. 看端点分类（-u/-r 任意，仅闪一下）
.venv/Scripts/python.exe -m perf.discovery

# 3. 串行基线（1 用户，30 秒）
PERF_REPORT=perf/report_before_serial.json \
  .venv/Scripts/python.exe -m locust -f perf/locustfile.py --headless \
  -u 1 -r 1 -t 30s --host http://127.0.0.1:18080 --only-summary

# 4. 并发梯度（把 u 换成 10/30/50）
PERF_REPORT=perf/report_before_u10.json \
  .venv/Scripts/python.exe -m locust -f perf/locustfile.py --headless \
  -u 10 -r 5 -t 60s --host http://127.0.0.1:18080 --only-summary

# 5. 优化后复测：换成 report_after_*.json，同样参数重跑

# 6. 生成前后对比表（读 report_before_*/report_after_*）：
.venv/Scripts/python.exe -m perf.compare

# 7. 清理 __perf__ 数据（会话/消息/记忆 + Redis 队列 key）
.venv/Scripts/python.exe -m perf.seed --clean
```

> **Git Bash 注意**：`PERF_PATHS` 的值以 `/api/...` 开头会被 MSYS 自动转成
> `C:/Program Files/Git/api/...`，导致过滤失效。命令前加 `MSYS_NO_PATHCONV=1`。

## 纳入 / 排除

- **纳入读**：所有 GET（除 SSE/流式/外部网络端点）。
- **纳入写**：operationId 在白名单内、可用 `__perf__` tag 隔离的会话/队列/记忆 CRUD。
- **排除**：SSE 长连接（`/events`、`/messages/stream`、`/approve/stream`）、真调
  LLM 或外部网络（playground、tools/sandbox run、skill install、reindex、触发器）、
  登录等——其"响应时间"不代表接口性能，且会烧钱/造不可清理数据。
- 带资源 id 的端点（如 `/secrets/{secret_id}/usage`）用占位 id，走完整
  路由→鉴权→校验→404，计时只到校验层；报告 failures 会体现，属预期。

## 报告格式

`PERF_REPORT` 指定的 JSON：每个 `(method, path)` 的
`count/min/p50/p95/p99/max/avg`，加全局 `failures` 映射。
