"""助理列表与用户自定义助理 CRUD。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth
from app.db import get_db
from app.services import agent_service
from app.services.audit_service import write_audit
from app.services.auth_service import AuthUser
from app.services.knowledge_scope import KnowledgeScope, parse_scope, validate_scope

router = APIRouter(tags=["agents"])


class AgentExampleInput(BaseModel):
    # 空值与长度上限由 agent_service.parse_examples 统一校验，保证错误口径一致（400）
    input: str
    output: str


class KnowledgeScopeInput(BaseModel):
    paths: list[str] = Field(default_factory=list)
    doc_ids: list[str] = Field(default_factory=list)


class UserAgentInput(BaseModel):
    name: str | None = None
    description: str = Field(min_length=1)
    when_to_use: str
    system_prompt: str = Field(min_length=1)
    critical_reminder: str
    allowed_tools: list[str]
    disallowed_tools: list[str]
    permission_mode: str
    model: str
    max_turns: int
    output_format: str
    examples: list[AgentExampleInput]
    knowledge_scope: KnowledgeScopeInput
    # None = 继承全部技能；数组 = 只装配这些（可为空数组 = 不装配任何技能）
    skills: list[str] | None = None


def _agent_audit_detail(agent: object) -> dict:
    """审计只记录装配结果，不落 system_prompt 全文（可能很长且含业务语料）。"""

    scope = getattr(agent, "knowledge_scope")
    return {
        "model": getattr(agent, "model"),
        "permission_mode": getattr(agent, "permission_mode"),
        "max_turns": getattr(agent, "max_turns"),
        "tools": list(getattr(agent, "tools")),
        "disallowed_tools": list(getattr(agent, "disallowed_tools")),
        "system_prompt_chars": len(getattr(agent, "system_prompt")),
        "output_format": getattr(agent, "output_format"),
        "examples": len(getattr(agent, "examples")),
        "knowledge_scope_paths": list(scope.paths),
        "knowledge_scope_docs": len(scope.doc_ids),
        "skills": getattr(agent, "skills"),
    }


async def _resolve_agent_scope(
    db: AsyncSession, body: UserAgentInput
) -> KnowledgeScope:
    """解析并校验知识范围；不存在的目录/文档直接 400，不做静默降级。"""

    scope = parse_scope(
        {
            "paths": body.knowledge_scope.paths,
            "doc_ids": body.knowledge_scope.doc_ids,
        }
    )
    try:
        await validate_scope(db, scope)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return scope


async def _resolve_agent_tools(
    db: AsyncSession, body: UserAgentInput
) -> tuple[list[str], list[str]]:
    """校验技能勾选并把技能声明的工具并入白名单；违规直接 400。"""

    try:
        return await agent_service.validate_skill_binding(
            db,
            body.skills,
            body.allowed_tools,
            body.disallowed_tools,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/agents")
async def list_agents(
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    items = await agent_service.list_agents(db)
    return {
        "items": [agent_service.agent_to_wire(a) for a in items],
        "total": len(items),
    }


@router.get("/agents/{name}")
async def get_agent(
    name: str,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        agent = await agent_service.get_agent(db, name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return agent_service.agent_to_wire(agent)


@router.post("/agents/custom")
async def create_user_agent(
    body: UserAgentInput,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    if body.name is None or body.name == "":
        raise HTTPException(status_code=400, detail="创建助理必须提供 name")
    scope = await _resolve_agent_scope(db, body)
    allowed_tools, disallowed_tools = await _resolve_agent_tools(db, body)
    try:
        agent = await agent_service.create_user_agent(
            db,
            name=body.name,
            description=body.description,
            when_to_use=body.when_to_use,
            system_prompt=body.system_prompt,
            critical_reminder=body.critical_reminder,
            allowed_tools=allowed_tools,
            disallowed_tools=disallowed_tools,
            permission_mode=body.permission_mode,
            model=body.model,
            max_turns=body.max_turns,
            output_format=body.output_format,
            examples=tuple(item.model_dump() for item in body.examples),
            knowledge_scope=scope,
            skills=body.skills,
        )
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        db,
        actor_user_id=user.id,
        action="agent.create",
        resource_type="agent",
        resource_id=agent.name,
        detail=_agent_audit_detail(agent),
    )
    return agent_service.agent_to_wire(agent)


@router.patch("/agents/custom/{name}")
async def update_user_agent(
    name: str,
    body: UserAgentInput,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> dict:
    scope = await _resolve_agent_scope(db, body)
    allowed_tools, disallowed_tools = await _resolve_agent_tools(db, body)
    try:
        agent = await agent_service.update_user_agent(
            db,
            name,
            description=body.description,
            when_to_use=body.when_to_use,
            system_prompt=body.system_prompt,
            critical_reminder=body.critical_reminder,
            allowed_tools=allowed_tools,
            disallowed_tools=disallowed_tools,
            permission_mode=body.permission_mode,
            model=body.model,
            max_turns=body.max_turns,
            output_format=body.output_format,
            examples=tuple(item.model_dump() for item in body.examples),
            knowledge_scope=scope,
            skills=body.skills,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        db,
        actor_user_id=user.id,
        action="agent.update",
        resource_type="agent",
        resource_id=agent.name,
        detail=_agent_audit_detail(agent),
    )
    return agent_service.agent_to_wire(agent)


@router.delete("/agents/custom/{name}")
async def delete_user_agent_custom(
    name: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> None:
    try:
        await agent_service.delete_user_agent(db, name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        db,
        actor_user_id=user.id,
        action="agent.delete",
        resource_type="agent",
        resource_id=name,
    )


@router.delete("/agents/{name}")
async def delete_agent(
    name: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_auth),
) -> None:
    try:
        await agent_service.delete_user_agent(db, name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        db,
        actor_user_id=user.id,
        action="agent.delete",
        resource_type="agent",
        resource_id=name,
    )
