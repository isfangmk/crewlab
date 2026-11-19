"""助理列表与用户自定义助理 CRUD。"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Path
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
    """少样本示例：一条用户输入及其期望输出。"""

    # 空值与长度上限由 agent_service.parse_examples 统一校验，保证错误口径一致（400）
    input: str = Field(description="示例的用户输入文本")
    output: str = Field(description="示例对应的期望输出文本")


class KnowledgeScopeInput(BaseModel):
    """助理可用的知识范围：知识库目录与文档 id 的并集。"""

    paths: list[str] = Field(default_factory=list, description="可用的知识库目录路径列表；为空表示不按目录限定")
    doc_ids: list[str] = Field(default_factory=list, description="可用的文档 id 列表；为空表示不额外指定文档")


class UserAgentInput(BaseModel):
    """用户自定义助理的完整配置请求体。"""

    name: str | None = Field(
        default=None,
        description="助理唯一标识，用于接口、会话与委托引用；创建后不可修改",
    )
    display_name: str = Field(
        min_length=1,
        max_length=64,
        description="面向用户的显示名称，可使用中文；不参与运行时标识",
    )
    description: str = Field(min_length=1, description="助理用途说明，至少 1 字")
    when_to_use: str = Field(description="何时使用该助理的说明，供其他助理判断是否委托")
    system_prompt: str = Field(min_length=1, description="系统提示词，定义助理角色与行为，至少 1 字")
    critical_reminder: str = Field(description="必须遵守的额外提醒；留空表示没有")
    allowed_tools: list[str] = Field(description="允许使用的 MCP 工具名列表；空数组不挂载任何 MCP（内置工具始终常驻，不在此列）")
    disallowed_tools: list[str] = Field(description="禁止使用的工具名列表；空数组表示不额外禁用")
    permission_mode: str = Field(description="权限模式：read-only 只读，空串为默认（不额外限制）")
    model: str = Field(description="运行时使用的模型标识")
    max_turns: int = Field(description="最大推理轮数；0 表示不限轮数，不能为负数")
    # 治理：工具结果回喂模型的字符上限；null/缺省 = 继承全局默认
    max_tool_result_chars: int | None = Field(default=None, description="工具结果回喂模型的字符上限；留空继承全局默认")
    # 治理：上下文压缩触发 token 阈值；null = 继承全局默认，0 = 关闭
    context_compaction_threshold: int | None = Field(default=None, description="上下文压缩触发的 token 阈值；留空继承全局默认，0 关闭")
    output_format: str = Field(description="输出格式：free 自由文本、json JSON 对象、checklist 清单")
    examples: list[AgentExampleInput] = Field(description="少样本示例列表")
    knowledge_scope: KnowledgeScopeInput = Field(description="助理可访问的知识范围")
    # None = 继承全部技能；数组 = 只装配这些（可为空数组 = 不装配任何技能）
    skills: list[str] | None = Field(default=None, description="要装配的技能 key 列表；留空继承全部技能，空数组不装配任何技能")
    # 子助理委托：name 列表；空/缺省 = 不委托
    sub_agents: list[str] = Field(default=[], description="可委托的子助理 name 列表；留空不委托")
    # 本次保存的变更摘要，只进版本快照，不进助理配置本身
    change_summary: str = Field(default="", description="本次保存的变更摘要，只进版本快照，不进助理配置本身")


def _agent_audit_detail(agent: object) -> dict:
    """审计只记录装配结果，不落 system_prompt 全文（可能很长且含业务语料）。"""

    scope = getattr(agent, "knowledge_scope")
    return {
        "display_name": getattr(agent, "display_name"),
        "model": getattr(agent, "model"),
        "permission_mode": getattr(agent, "permission_mode"),
        "max_turns": getattr(agent, "max_turns"),
        "max_tool_result_chars": getattr(agent, "max_tool_result_chars", None),
        "context_compaction_threshold": getattr(agent, "context_compaction_threshold", None),
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
    """校验技能勾选（key 必须存在）；工具白名单按原样透传。"""

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
    """列出全部助理（内置 + 用户自定义），含版本与更新时间。"""

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
    name: str = Path(description="要获取的助理 name（先通过列表获取）"),
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> dict:
    """按 name 获取单个助理详情，含当前版本号。"""

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
    body: UserAgentInput = Body(description="新助理的完整配置；name 必填且创建后不可改"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """新建用户自定义助理，保存后生成版本 1 快照。"""

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
            context_compaction_threshold=body.context_compaction_threshold,
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
    name: str = Path(description="要更新的助理 name"),
    body: UserAgentInput = Body(description="助理的完整新配置"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """按 name 更新用户自定义助理，保存后生成新版本快照。"""

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
            context_compaction_threshold=body.context_compaction_threshold,
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
    """助理回滚的 request body。"""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1, description="要恢复到的目标版本号，从 1 开始")
    change_summary: str = Field(default="", description="本次回滚的变更摘要，只进版本快照")


@router.get("/agents/custom/{name}/versions")
async def list_agent_versions(
    name: str = Path(description="要列出版本的助理 name"),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """列出助理的历史版本快照。"""

    try:
        await agent_service.get_agent(db, name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    items = await agent_version_service.list_versions(db, name)
    return {"items": items, "total": len(items)}


@router.get("/agents/custom/{name}/versions/{version}")
async def get_agent_version(
    name: str = Path(description="助理 name"),
    version: int = Path(description="要查看的版本号，从 1 开始"),
    db: AsyncSession = Depends(get_db),
    _user: AuthUser = Depends(require_auth),
) -> dict:
    """按 name 与版本号获取助理的历史版本快照。"""

    try:
        return await agent_version_service.get_version(db, name, version)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/agents/custom/{name}/rollback")
async def rollback_agent(
    name: str = Path(description="要回滚的助理 name"),
    body: RollbackRequest = Body(description="回滚目标版本与变更摘要"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> dict:
    """把助理回滚到指定的历史版本，内容以快照为准。"""

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
    """草稿助理试跑的 request body。"""

    model_config = ConfigDict(extra="forbid")

    agent: UserAgentInput = Field(description="要试跑的草稿助理完整配置，name 必填")
    content: str = Field(min_length=1, description="本次试跑的用户输入内容，至少 1 字")
    provider: str = Field(default="", description="模型服务商标识；留空用默认服务商")
    model: str = Field(default="", description="覆盖模型标识；留空用草稿助理配置的模型")
    thinking_enabled: bool = Field(default=False, description="是否开启模型思考模式")
    web_search_enabled: bool = Field(default=False, description="是否开启联网搜索")
    locale: str | None = Field(default=None, description="语言/地区标识（如 zh-CN）；留空用默认")


@router.post("/agents/playground/stream")
async def playground_stream(
    body: PlaygroundRequest = Body(description="草稿助理配置与本次试跑输入"),
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
            context_compaction_threshold=draft.context_compaction_threshold,
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
    name: str = Path(description="要删除的用户助理 name"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> None:
    """按 name 删除用户自定义助理及其版本快照。"""

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
    name: str = Path(description="要删除的助理 name"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_not_viewer),
) -> None:
    """按 name 删除助理，内置助理不允许删除。"""

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
