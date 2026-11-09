"""助理列表与用户自定义助理 CRUD。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_auth
from app.db import get_db
from app.services import agent_service

router = APIRouter(tags=["agents"])


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
    _user: str = Depends(require_auth),
) -> dict:
    if body.name is None or body.name == "":
        raise HTTPException(status_code=400, detail="创建助理必须提供 name")
    try:
        agent = await agent_service.create_user_agent(
            db,
            name=body.name,
            description=body.description,
            when_to_use=body.when_to_use,
            system_prompt=body.system_prompt,
            critical_reminder=body.critical_reminder,
            allowed_tools=body.allowed_tools,
            disallowed_tools=body.disallowed_tools,
            permission_mode=body.permission_mode,
            model=body.model,
            max_turns=body.max_turns,
        )
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return agent_service.agent_to_wire(agent)


@router.patch("/agents/custom/{name}")
async def update_user_agent(
    name: str,
    body: UserAgentInput,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    try:
        agent = await agent_service.update_user_agent(
            db,
            name,
            description=body.description,
            when_to_use=body.when_to_use,
            system_prompt=body.system_prompt,
            critical_reminder=body.critical_reminder,
            allowed_tools=body.allowed_tools,
            disallowed_tools=body.disallowed_tools,
            permission_mode=body.permission_mode,
            model=body.model,
            max_turns=body.max_turns,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return agent_service.agent_to_wire(agent)


@router.delete("/agents/custom/{name}")
async def delete_user_agent_custom(
    name: str,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> None:
    try:
        await agent_service.delete_user_agent(db, name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/agents/{name}")
async def delete_agent(
    name: str,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> None:
    try:
        await agent_service.delete_user_agent(db, name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
