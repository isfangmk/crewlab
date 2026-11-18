"""助理列表与用户自定义助理 CRUD。"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.deps import require_auth, require_not_viewer
from app.db import get_db
from app.services.agent import agent_service, agent_version_service, playground_service
from app.services.knowledge.knowledge_scope import KnowledgeScope, parse_scope, validate_scope
from app.services.observability.audit_service import write_audit
from app.services.platform.auth_service import AuthUser

router = APIRouter(tags=["agents"])

logger = logging.getLogger(__name__)


class AgentExampleInput(BaseModel):
    # 空值与长度上限由 agent_service.parse_examples 统一校验，保证错误口径一致（400）
    input: str
    output: str


class KnowledgeScopeInput(BaseModel):
    paths: list[str] = Field(default_factory=list)
    doc_ids: list[str] = Field(default_factory=list)


class UserAgentInput(BaseModel):
    name: str | None = Field(
        default=None,
        description="助理唯一标识，用于接口、会话与委托引用；创建后不可修改",
    )
    display_name: str = Field(
        min_length=1,
        max_length=64,
        description="面向用户的显示名称，可使用中文；不参与运行时标识",
    )
    description: str = Field(min_length=1)
    when_to_use: str
    system_prompt: str = Field(min_length=1)
    critical_reminder: str
    allowed_tools: list[str]
    disallowed_tools: list[str]
    permission_mode: str
    model: str
    max_turns: int
    # 治理：工具结果回喂模型的字符上限；null/缺省 = 继承全局默认
    max_tool_result_chars: int | None = None
    output_format: str
    examples: list[AgentExampleInput]
    knowledge_scope: KnowledgeScopeInput
    # None = 继承全部技能；数组 = 只装配这些（可为空数组 = 不装配任何技能）
    skills: list[str] | None = None
    # 子助理委托：name 列表；空/缺省 = 不委托
    sub_agents: list[str] = []
    # 本次保存的变更摘要，只进版本快照，不进助理配置本身
    change_summary: str = ""


def _agent_audit_detail(agent: object) -> dict:
    """审计只记录装配结果，不落 system_prompt 全文（可能很长且含业务语料）。"""

    scope = getattr(agent, "knowledge_scope")
    return {
        "display_name": getattr(agent, "display_name"),
        "model": getattr(agent, "model"),
        "permission_mode": getattr(agent, "permission_mode"),
        "max_turns": getattr(agent, "max_turns"),
        "max_tool_result_chars": getattr(agent, "max_tool_result_chars", None),
        "tools": list(getattr(agent, "tools")),
        "disallowed_tools": list(getattr(agent, "disallowed_tools")),
        "system_prompt_chars": len(getattr(agent, "system_prompt")),
        "output_format": getattr(agent, "output_format"),
        "examples": len(getattr(agent, "examples")),
        "knowledge_scope_paths": list(scope.paths),
        "knowledge_scope_docs": len(scope.doc_ids),
        "skills": getattr(agent, "skills"),
        "sub_agents": list(getattr(agent, "sub_agents")),
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
    meta = await agent_version_service.list_current_meta(db)
    out = []
    for a in items:
        m = meta.get(a.name, {})
        out.append(
            agent_service.agent_to_wire(
                a,
                version=int(m.get("version", 0)),
                updated_at=m.get("updated_at"),
            )
        )
    return {"items": out, "total": len(out)}


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
    version = await agent_version_service.current_version(db, name)
    meta = await agent_version_service.list_current_meta(db)
    return agent_service.agent_to_wire(
        agent,
        version=version,
        updated_at=meta.get(name, {}).get("updated_at"),
    )


@router.post("/agents/custom")
async def create_user_agent(
    body: UserAgentInput,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    if body.name is None or body.name == "":
        raise HTTPException(status_code=400, detail="创建助理必须提供 name")
    scope = await _resolve_agent_scope(db, body)
    allowed_tools, disallowed_tools = await _resolve_agent_tools(db, body)
    try:
        agent = await agent_service.create_user_agent(
            db,
            name=body.name,
            display_name=body.display_name,
            description=body.description,
            when_to_use=body.when_to_use,
            system_prompt=body.system_prompt,
            critical_reminder=body.critical_reminder,
            allowed_tools=allowed_tools,
            disallowed_tools=disallowed_tools,
            permission_mode=body.permission_mode,
            model=body.model,
            max_turns=body.max_turns,
            max_tool_result_chars=body.max_tool_result_chars,
            output_format=body.output_format,
            examples=tuple(item.model_dump() for item in body.examples),
            knowledge_scope=scope,
            skills=body.skills,
            sub_agents=tuple(body.sub_agents),
            created_by=user.id,
            change_summary=body.change_summary,
        )
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="agent.create",
        resource_type="agent",
        resource_id=agent.name,
        detail=_agent_audit_detail(agent),
    )
    version = await agent_version_service.current_version(db, agent.name)
    return agent_service.agent_to_wire(agent, version=version)


@router.patch("/agents/custom/{name}")
async def update_user_agent(
    name: str,
    body: UserAgentInput,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    scope = await _resolve_agent_scope(db, body)
    allowed_tools, disallowed_tools = await _resolve_agent_tools(db, body)
    try:
        agent = await agent_service.update_user_agent(
            db,
            name,
            display_name=body.display_name,
            description=body.description,
            when_to_use=body.when_to_use,
            system_prompt=body.system_prompt,
            critical_reminder=body.critical_reminder,
            allowed_tools=allowed_tools,
            disallowed_tools=disallowed_tools,
            permission_mode=body.permission_mode,
            model=body.model,
            max_turns=body.max_turns,
            max_tool_result_chars=body.max_tool_result_chars,
            output_format=body.output_format,
            examples=tuple(item.model_dump() for item in body.examples),
            knowledge_scope=scope,
            skills=body.skills,
            sub_agents=tuple(body.sub_agents),
            created_by=user.id,
            change_summary=body.change_summary,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="agent.update",
        resource_type="agent",
        resource_id=agent.name,
        detail=_agent_audit_detail(agent),
    )
    version = await agent_version_service.current_version(db, agent.name)
    return agent_service.agent_to_wire(agent, version=version)


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    change_summary: str = ""


@router.get("/agents/custom/{name}/versions")
async def list_agent_versions(
    name: str,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        await agent_service.get_agent(db, name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    items = await agent_version_service.list_versions(db, name)
    return {"items": items, "total": len(items)}


@router.get("/agents/custom/{name}/versions/{version}")
async def get_agent_version(
    name: str,
    version: int,
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    try:
        return await agent_version_service.get_version(db, name, version)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/agents/custom/{name}/rollback")
async def rollback_agent(
    name: str,
    body: RollbackRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    try:
        agent = await agent_service.rollback_user_agent(
            db,
            name,
            body.version,
            actor_user_id=user.id,
            change_summary=body.change_summary,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="agent.rollback",
        resource_type="agent",
        resource_id=name,
        detail={"restored_from_version": body.version},
    )
    version = await agent_version_service.current_version(db, name)
    return agent_service.agent_to_wire(agent, version=version)


class PlaygroundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: UserAgentInput
    content: str = Field(min_length=1)
    provider: str = ""
    model: str = ""
    thinking_enabled: bool = False
    web_search_enabled: bool = False
    locale: str | None = None


@router.post("/agents/playground/stream")
async def playground_stream(
    body: PlaygroundRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
):
    """草稿助理试跑：不落会话、不落观测，跑完即弃。"""

    draft = body.agent
    if draft.name is None or draft.name == "":
        raise HTTPException(status_code=400, detail="试跑前请先填写助理标识 name")
    scope = await _resolve_agent_scope(db, draft)
    allowed_tools, disallowed_tools = await _resolve_agent_tools(db, draft)
    try:
        agent = await agent_service.resolve_draft_agent(
            db,
            name=draft.name,
            display_name=draft.display_name,
            description=draft.description,
            when_to_use=draft.when_to_use,
            system_prompt=draft.system_prompt,
            critical_reminder=draft.critical_reminder,
            allowed_tools=allowed_tools,
            disallowed_tools=disallowed_tools,
            permission_mode=draft.permission_mode,
            model=draft.model,
            max_turns=draft.max_turns,
            max_tool_result_chars=draft.max_tool_result_chars,
            output_format=draft.output_format,
            examples=tuple(item.model_dump() for item in draft.examples),
            knowledge_scope=scope,
            skills=draft.skills,
            sub_agents=tuple(draft.sub_agents),
        )
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def gen():
        try:
            # 试跑不落会话/观测，审计是唯一记录：只记谁试跑了哪个草稿
            await write_audit(
                actor_user_id=user.id,
                action="agent.playground",
                resource_type="agent",
                resource_id=draft.name or "",
            )
            async for frame in playground_service.stream_preview(
                db,
                agent=agent,
                content=body.content,
                provider=body.provider,
                model=body.model,
                thinking_enabled=body.thinking_enabled,
                web_search_enabled=body.web_search_enabled,
                locale=body.locale,
            ):
                yield {
                    "event": frame.event,
                    "data": json.dumps(frame.data, ensure_ascii=False),
                }
        except Exception as exc:
            logger.exception("Playground 试跑失败")
            yield {
                "event": "error",
                "data": json.dumps({"error": str(exc)}, ensure_ascii=False),
            }

    return EventSourceResponse(gen(), sep="\n")


@router.delete("/agents/custom/{name}")
async def delete_user_agent_custom(
    name: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> None:
    try:
        await agent_service.delete_user_agent(db, name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="agent.delete",
        resource_type="agent",
        resource_id=name,
    )


@router.delete("/agents/{name}")
async def delete_agent(
    name: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> None:
    try:
        await agent_service.delete_user_agent(db, name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit(
        actor_user_id=user.id,
        action="agent.delete",
        resource_type="agent",
        resource_id=name,
    )
