"""FastAPI 入口。"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import (
    agents,
    approval_rules,
    approvals,
    artifacts,
    audit,
    auth,
    chat,
    evals,
    health,
    integrations_llm,
    investigations,
    knowledge,
    knowledge_sources,
    mcp,
    memory,
    mentions,
    models,
    observability,
    publish,
    sandbox,
    secrets,
    shares,
    skills,
    system_settings,
    tools,
    triggers,
    usage,
    version,
    workflows,
)
from app.config import get_settings
from app.db import SessionLocal, engine
from app.logging_setup import setup_logging
from app.middleware_request_id import RequestIdMiddleware
from app.models import Base
from app.services.platform.app_settings_service import (
    ensure_agent_runtime,
    ensure_knowledge_settings,
    ensure_system_timezone,
)
from app.services.platform.auth_service import ensure_bootstrap_admin
from app.services.platform.llm_config import ensure_seed_provider

logger = logging.getLogger(__name__)


def _openai_error_type(status_code: int) -> tuple[str, str | None]:
    """OpenAI 客户端的错误类型口径；站内接口不受影响。"""

    if status_code == 401:
        return "authentication_error", "invalid_api_key"
    if status_code == 404:
        return "invalid_request_error", "not_found"
    if status_code == 429:
        return "rate_limit_error", "rate_limit_exceeded"
    if status_code >= 500:
        return "server_error", None
    return "invalid_request_error", None


async def _openai_http_exception_handler(request, exc: Exception):
    """/v1/* 用 OpenAI 的 {"error": {...}} 结构，其余路由保持 FastAPI 默认。"""

    if not isinstance(exc, StarletteHTTPException):
        raise exc
    if not request.url.path.startswith("/v1/"):
        return await http_exception_handler(request, exc)
    detail = exc.detail
    message = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
    error_type, code = _openai_error_type(exc.status_code)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "message": message,
                "type": error_type,
                "param": None,
                "code": code,
            }
        },
        headers=exc.headers,
    )


async def _openai_validation_exception_handler(request, exc: Exception):
    """/v1/* 的参数校验失败按 OpenAI 口径返回 400，而不是 FastAPI 的 422。"""

    if not isinstance(exc, RequestValidationError):
        raise exc
    if not request.url.path.startswith("/v1/"):
        return await request_validation_exception_handler(request, exc)
    errors = exc.errors()
    first = errors[0] if errors else {}
    location = ".".join(
        str(part) for part in first.get("loc", ()) if part != "body"
    )
    message = str(first.get("msg", "请求参数无效"))
    if location != "":
        message = f"{location}: {message}"
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "message": message,
                "type": "invalid_request_error",
                "param": location or None,
                "code": "invalid_request",
            }
        },
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    # 每次进程启动覆盖写入 logs/backend.log
    setup_logging(settings.log_level)
    logger.info("启动 AI 工作台后端 app=%s log_file=logs/backend.log", settings.app_name)

    async with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)

    # 上次进程退出时可能留下 syncing 状态；启动后统一标为可重试的失败
    from app.services.knowledge.knowledge_source_service import recover_interrupted_syncs

    async with SessionLocal() as db:
        await recover_interrupted_syncs(db)

    # 向量存储与业务库分离：未启用时显式记录，向量依赖操作在调用点显式失败
    from app.vector_store import get_vector_store

    vector_store = get_vector_store()
    if vector_store is None:
        logger.warning(
            "向量存储未启用 VECTOR_BACKEND=%s：知识库切块/语义检索与 MCP 工具语义索引不可用",
            settings.vector_backend,
        )
    else:
        await vector_store.ensure_ready(settings.embedding_dim)

    from app.adk_runtime.session_store import prepare_adk_session_tables

    await prepare_adk_session_tables()

    async with SessionLocal() as db:
        await ensure_bootstrap_admin(db)
        await ensure_seed_provider(db)
        await ensure_agent_runtime(db)
        await ensure_knowledge_settings(db)
        await ensure_system_timezone(db)
        # 向量表 tool_name 必须与 wire 规则一致，否则检索装配会查错名。
        from app.services.mcp.tool_search_index import assert_embedding_names_consistent

        await assert_embedding_names_consistent(db)

    # 触发器调度循环随应用启停
    from app.services.workflow.trigger_scheduler import start_scheduler, stop_scheduler

    await start_scheduler()

    # 内置知识库文件同步：启动即导入一轮，之后按间隔轮询语料目录
    from app.services.knowledge.knowledge_vault_sync import start_vault_sync, stop_vault_sync

    await start_vault_sync()

    # 预热 litellm：ADK 到首个回合才懒加载它，冷导入约 10 秒会让进程的第一个问题白等；
    # 挪到无人等待的启动期（价格表已由 LITELLM_LOCAL_MODEL_COST_MAP 强制本地，不拉 GitHub）
    import asyncio
    import importlib

    warm_started = asyncio.get_running_loop().time()
    await asyncio.to_thread(importlib.import_module, "litellm")
    logger.info("litellm 预热完成 %.1fs", asyncio.get_running_loop().time() - warm_started)

    yield
    await stop_vault_sync()
    await stop_scheduler()
    await engine.dispose()
    logger.info("后端已关闭")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    # OpenAI 兼容端点使用标准错误体；其他路由维持 FastAPI 默认格式
    app.add_exception_handler(StarletteHTTPException, _openai_http_exception_handler)
    app.add_exception_handler(
        RequestValidationError, _openai_validation_exception_handler
    )
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Content-Disposition 带下载文件名：不暴露的话前端 fetch 读不到，
        # 下载落盘只能回退到 'download'
        expose_headers=["Content-Disposition"],
    )
    app.include_router(health.router)
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(version.router, prefix="/api/v1")
    app.include_router(artifacts.router, prefix="/api/v1")
    app.include_router(memory.router, prefix="/api/v1")
    app.include_router(models.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")
    app.include_router(approvals.router, prefix="/api/v1")
    app.include_router(approval_rules.router, prefix="/api/v1")
    app.include_router(mcp.router, prefix="/api/v1")
    app.include_router(agents.router, prefix="/api/v1")
    app.include_router(tools.router, prefix="/api/v1")
    app.include_router(skills.router, prefix="/api/v1")
    app.include_router(knowledge.router, prefix="/api/v1")
    app.include_router(knowledge_sources.router, prefix="/api/v1")
    app.include_router(mentions.router, prefix="/api/v1")
    app.include_router(secrets.router, prefix="/api/v1")
    app.include_router(shares.router, prefix="/api/v1")
    app.include_router(investigations.router, prefix="/api/v1")
    app.include_router(system_settings.router, prefix="/api/v1")
    app.include_router(integrations_llm.router, prefix="/api/v1")
    app.include_router(observability.router, prefix="/api/v1")
    app.include_router(usage.router, prefix="/api/v1")
    app.include_router(evals.router, prefix="/api/v1")
    app.include_router(sandbox.router, prefix="/api/v1")
    app.include_router(audit.router, prefix="/api/v1")
    app.include_router(workflows.router, prefix="/api/v1")
    app.include_router(triggers.router, prefix="/api/v1")
    app.include_router(publish.router, prefix="/api/v1")
    # OpenAI 兼容端点在根路径，便于外部 SDK 直接指向工作台
    app.include_router(publish.public_router)
    return app


app = create_app()
