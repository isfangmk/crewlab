"""FastAPI 入口。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import (
    agents,
    approvals,
    audit,
    auth,
    chat,
    health,
    integrations_llm,
    knowledge,
    mcp,
    mentions,
    models,
    observability,
    sandbox,
    secrets,
    skills,
    system_settings,
    tools,
    version,
    workflows,
)
from app.config import get_settings
from app.db import SessionLocal, engine
from app.logging_setup import setup_logging
from app.middleware_request_id import RequestIdMiddleware
from app.models_orm import Base
from app.services.auth_service import ensure_bootstrap_admin
from app.services.llm_config import ensure_seed_provider
from app.services.app_settings_service import (
    ensure_agent_runtime,
    ensure_knowledge_settings,
    ensure_system_timezone,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    # 每次进程启动覆盖写入 logs/backend.log
    setup_logging(settings.log_level)
    logger.info("启动 AI 工作台后端 app=%s log_file=logs/backend.log", settings.app_name)

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)

    from app.adk_runtime.session_store import prepare_adk_session_tables

    await prepare_adk_session_tables()

    async with SessionLocal() as db:
        await ensure_bootstrap_admin(db)
        await ensure_seed_provider(db)
        await ensure_agent_runtime(db)
        await ensure_knowledge_settings(db)
        await ensure_system_timezone(db)
        # 向量表 tool_name 必须与 wire 规则一致，否则检索装配会查错名。
        from app.services.tool_search_index import assert_embedding_names_consistent

        await assert_embedding_names_consistent(db)

    yield
    await engine.dispose()
    logger.info("后端已关闭")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(version.router, prefix="/api/v1")
    app.include_router(models.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")
    app.include_router(approvals.router, prefix="/api/v1")
    app.include_router(mcp.router, prefix="/api/v1")
    app.include_router(agents.router, prefix="/api/v1")
    app.include_router(tools.router, prefix="/api/v1")
    app.include_router(skills.router, prefix="/api/v1")
    app.include_router(knowledge.router, prefix="/api/v1")
    app.include_router(mentions.router, prefix="/api/v1")
    app.include_router(secrets.router, prefix="/api/v1")
    app.include_router(system_settings.router, prefix="/api/v1")
    app.include_router(integrations_llm.router, prefix="/api/v1")
    app.include_router(observability.router, prefix="/api/v1")
    app.include_router(sandbox.router, prefix="/api/v1")
    app.include_router(audit.router, prefix="/api/v1")
    app.include_router(workflows.router, prefix="/api/v1")
    return app


app = create_app()
