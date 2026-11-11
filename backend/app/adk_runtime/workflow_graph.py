"""把画布定义装配成可执行的 ADK Workflow 图。

执行完全交给 google.adk.workflow：每个画布节点是一个 Node 实例，
连线是 Edge（可带 route），汇合是带多入边的节点，循环靠带回边的路由。
这里只做「画布 → ADK 对象」的翻译与运行上下文准备，不自己写调度。
"""

from __future__ import annotations

import logging
from typing import Any

from google.adk.workflow import Workflow
from sqlalchemy.ext.asyncio import AsyncSession

from app.adk_runtime.agent_factory import build_switch_agent, build_workflow_agent
from app.adk_runtime.workflow_nodes import (
    RunContext,
    build_edges,
    build_llm_callbacks,
    build_node,
    build_validation_graph,
)
from app.schemas.workflow import WorkflowDefinition, WorkflowNode

logger = logging.getLogger(__name__)


class RunAssembly:
    """一次运行的装配结果：ADK 图 + 运行上下文。"""

    def __init__(self, *, graph: Workflow, run_ctx: RunContext) -> None:
        self.graph = graph
        self.run_ctx = run_ctx


async def _load_tools(db: AsyncSession, definition: WorkflowDefinition) -> dict[str, Any]:
    """按画布用到的能力准备外部依赖；用不到的不去碰。"""

    tools: dict[str, Any] = {}
    kinds = {n.kind for n in definition.nodes}
    if "search" in kinds:
        from app.services.web_search_service import resolve_deepseek_search_cred

        base_url, api_key = await resolve_deepseek_search_cred(db)
        tools["web_search_base_url"] = base_url
        tools["web_search_api_key"] = api_key
    if "mcp" in kinds:
        from app.services import mcp_service

        servers = await mcp_service.list_enabled_servers(db)
        tools["mcp_servers"] = {server.name: server for server in servers}
        logger.info(
            "工作流 MCP 节点可用 Server=%s 需要=%s",
            sorted(tools["mcp_servers"]),
            sorted({n.mcp_server for n in definition.nodes if n.kind == "mcp"}),
        )
    if any(n.kind == "switch" and n.branch_mode == "model" for n in definition.nodes):
        tools["switch_llm"] = await _default_llm(db)
    return tools


async def _default_llm(db: AsyncSession) -> Any:
    from app.services.llm_config import get_default_selection, list_enabled_providers

    provider_id, model_name = await get_default_selection(db)
    providers = {p.provider_id: p for p in await list_enabled_providers(db)}
    provider = providers.get(provider_id)
    if provider is None:
        raise LookupError(f"默认 provider 已停用: {provider_id}")
    return provider, model_name


async def _build_agent_node(
    *, db: AsyncSession, node: WorkflowNode, run_ctx: RunContext
) -> Any:
    """LLM 节点：按节点声明的 provider+model 解析模型。

    节点只做一次模型调用，工具由专门的知识检索 / 联网搜索 / MCP 节点承担，
    所以这里不挂任何工具。
    """

    from app.services.llm_config import resolve_model

    provider = await resolve_model(db, node.provider, node.model)
    logger.info(
        "工作流节点模型 node=%s provider=%s model=%s",
        node.id,
        node.provider,
        node.model,
    )
    agent = build_workflow_agent(
        node_id=node.id,
        llm=provider,
        model_name=node.model,
        instruction=node.instruction,
    )
    before, after_model, on_error = build_llm_callbacks(node=node, run_ctx=run_ctx)
    agent.before_agent_callback = before
    agent.after_model_callback = after_model
    agent.on_model_error_callback = on_error
    return agent


def _build_passthrough(
    *, node: WorkflowNode, run_ctx: RunContext, tools: dict[str, Any]
) -> Any:
    """已确认过的人工节点：装配成透传节点。"""

    from app.adk_runtime.workflow_nodes import PassThroughNode

    built: Any = PassThroughNode.build(
        node=node,
        run_ctx=run_ctx,
        tools=tools,
        name=node.id,
        description=node.label,
    )
    return built


async def assemble(
    *,
    db: AsyncSession,
    definition: WorkflowDefinition,
    run_values: dict[str, Any],
    approved_gates: set[str] | None = None,
    stop_after: str | None = None,
) -> RunAssembly:
    """装配可执行的 ADK 图；run_values 交给开始节点写进共享状态。

    approved_gates 是已确认过的人工节点，恢复时把它们换成直通节点。
    stop_after 用于单节点试跑：跑到该节点就收工。
    """

    run_ctx = RunContext(definition=definition, db=db, stop_after=stop_after)
    tools = await _load_tools(db, definition)
    tools["run_values"] = run_values
    approved = set() if approved_gates is None else approved_gates
    node_map: dict[str, Any] = {}

    for node in definition.nodes:
        if node.kind in ("approval", "input") and node.id in approved:
            node_map[node.id] = _build_passthrough(
                node=node, run_ctx=run_ctx, tools=tools
            )
            logger.info("人工节点已确认，直通 node=%s", node.id)
            continue
        if node.kind == "agent":
            agent = await _build_agent_node(db=db, node=node, run_ctx=run_ctx)
            agent.name = node.id
            node_map[node.id] = agent
            logger.info(
                "工作流 LLM 节点装配 node=%s provider=%s model=%s",
                node.id,
                node.provider,
                node.model,
            )
            continue
        node_tools = dict(tools)
        if node.kind == "switch" and node.branch_mode == "model":
            provider, model_name = tools["switch_llm"]
            node_tools["switch_agent"] = build_switch_agent(
                node_id=f"{node.id}_decider",
                llm=provider,
                model_name=model_name,
                instruction="你是工作流的分支判定器，只回答给定选项中的一个词。",
            )
        node_map[node.id] = build_node(node=node, run_ctx=run_ctx, tools=node_tools)

    edges = build_edges(definition, node_map)
    logger.info(
        "装配 ADK Workflow 图 nodes=%s edges=%s",
        sorted(node_map),
        [(e.from_node.name, e.to_node.name, e.route) for e in edges],
    )
    graph = Workflow(name="workflow", edges=edges)
    return RunAssembly(graph=graph, run_ctx=run_ctx)


def validate(definition: WorkflowDefinition) -> None:
    """结构校验：不查库、不触达模型。"""

    build_validation_graph(definition)
