# AI 工作台（ongrid-python3）

基于 ongrid Web 裁剪的 AI 工作台：后端 FastAPI + Google ADK，前端 React。

## 硬约束

- Agent / Tool / Skill / 对话 / 工具审批：只用 **google-adk**，禁止自研运行时
- **禁止兜底修补**；契约不符显式失败
- 对话框模型列表只来自「配置 → 模型」
- Tool ≠ Skill：侧边栏「工具」展示内置 Tool，「技能」展示 Skill
- 流式顺序：`tool_intent` → 工具卡/审批卡 → `assistant_delta` 正文打字机
- 改后端代码后 **必须** 用启停脚本重启（禁止 `--reload`）

详见 [`docs/AI工作台-功能迭代.md`](docs/AI工作台-功能迭代.md)。

## 依赖

- Python 3.12 + **uv**
- PostgreSQL（WSL Docker `pgvector/pgvector:pg16`，本机映射 `54329`）
- Node.js（前端）

## 后端

```powershell
cd backend
uv sync
copy .env.example .env   # 填入真实密钥
cd ..
.\scripts\backend-start.ps1
.\scripts\backend-restart.ps1   # 改代码后用这个
.\scripts\backend-stop.ps1
.\scripts\backend-typecheck.ps1   # basedpyright
```

健康检查：`http://127.0.0.1:18080/healthz`

登录：邮箱 = `.env` 中 `AUTH_ADMIN_EMAIL`，密码 = `.env` 中 `AUTH_TOKEN`

## 前端

```powershell
cd web
npm install
npm run dev
```

Vite 代理 `/api` → `http://127.0.0.1:18080`
