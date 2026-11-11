"""工作流各节点的 ADK 执行实现。

每个画布节点都是一个 google.adk.workflow.Node 子类实例。调度、并行、汇合、
循环回边全部交给 ADK 图执行器；这里只实现「单节点做什么」。

数据流转：节点用 {{变量}} 读共享状态，结果按 output_key 写回。
需要暂停的节点（审批 / 人工输入）不在此实现，由运行服务在运行层面拦截。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from google.adk.events import Event, EventActions
from google.adk.workflow import Edge, Node, RetryConfig, Workflow
from pydantic import PrivateAttr
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.workflow import WorkflowDefinition, WorkflowNode
from app.services import workflow_vars

logger = logging.getLogger(__name__)


@dataclass
class NodeOutcome:
    """一次节点执行的结果，供运行服务落库。"""

    node_id: str
    node_kind: str
    attempt: int
    status: str
    output: Any
    error: str
    duration_ms: int
    route: str = ""


@dataclass
class RunContext:
    """一次运行的共享容器。"""

    definition: WorkflowDefinition
    db: AsyncSession
    outcomes: list[NodeOutcome] = field(default_factory=list)
    attempts: dict[str, int] = field(default_factory=dict)
    # 命中人工节点后置位；后续节点必须拒绝执行，否则会在没人确认时跑下游
    gate_hit: str | None = None
    # 单节点试跑：跑到 stop_after 这个节点就收工，下游不执行
    stop_after: str | None = None
    stopped: bool = False

    def next_attempt(self, node_id: str) -> int:
        attempt = self.attempts.get(node_id, 0) + 1
        self.attempts[node_id] = attempt
        return attempt


def serialize(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return repr(value)


class WorkflowGateBlocked(Exception):
    """运行已停在人工节点，下游节点因此未执行（不是失败）。"""


class WorkflowStopAfter(Exception):
    """单节点试跑跑到目标节点后主动收工，下游未执行（不是失败）。"""


@dataclass
class Routed:
    """带路由的节点输出：route 由 ADK 用来匹配 Edge.route。"""

    route: str
    value: Any = None


def build_llm_callbacks(
    *, node: WorkflowNode, run_ctx: RunContext
) -> tuple[Any, Any, Any]:
    """LLM 节点的结果写回与执行记录。

    agent 节点是 ADK 的 LlmAgent，不是 CanvasNode，不走统一的 output_key 写回，
    所以用它的回调补上；否则画布上的 {{节点}} 变量永远是空的，节点执行记录里
    也看不到这一步。

    after_agent_callback 只拿得到上下文、拿不到本次模型响应，因此写回放在
    after_model_callback：那里能拿到 LlmResponse 的正文。
    """

    started_at: dict[str, float] = {}
    attempt: dict[str, int] = {}

    async def before(_callback_context: Any) -> None:
        attempt["value"] = run_ctx.next_attempt(node.id)
        started_at["value"] = asyncio.get_running_loop().time()
        return None

    def _record(status: str, text: str, error: str) -> None:
        now = asyncio.get_running_loop().time()
        current = attempt.get("value", 1)
        outcome = NodeOutcome(
            node_id=node.id,
            node_kind=node.kind,
            attempt=current,
            status=status,
            output=text,
            error=error,
            duration_ms=int((now - started_at.get("value", now)) * 1000),
        )
        existing = next(
            (o for o in run_ctx.outcomes if o.node_id == node.id and o.attempt == current),
            None,
        )
        if existing is None:
            run_ctx.outcomes.append(outcome)
            return
        # 同一次执行可能产生多段响应，只保留最后一段，避免记录里出现重复行
        existing.status = outcome.status
        existing.output = outcome.output
        existing.error = outcome.error
        existing.duration_ms = outcome.duration_ms

    async def after_model(callback_context: Any, response: Any) -> None:
        content = getattr(response, "content", None)
        parts = getattr(content, "parts", None)
        # 只取正文：思考过程（thought）不该写进节点输出，也不该传给下游
        text = (
            "".join(
                part.text or ""
                for part in parts
                if not getattr(part, "thought", False)
            )
            if parts
            else ""
        )
        if node.output_key != "":
            callback_context.state[node.output_key] = text
        _record("succeeded", text, "")
        logger.info(
            "工作流 LLM 节点完成 node=%s output_key=%s chars=%s",
            node.id,
            node.output_key,
            len(text),
        )
        return None

    async def on_error(_callback_context: Any, _request: Any, error: Exception) -> None:
        _record("failed", "", f"{type(error).__name__}: {error}")
        return None

    return before, after_model, on_error


class CanvasNode(Node):
    """所有画布节点的基类：统一计时、记录结果、按 output_key 写回状态。"""

    _node_def: WorkflowNode = PrivateAttr()
    _run_ctx: RunContext = PrivateAttr()
    _tools: dict[str, Any] = PrivateAttr(default_factory=dict)
    _attempt: int = PrivateAttr(default=0)

    def __init__(
        self,
        *,
        node: WorkflowNode,
        run_ctx: RunContext,
        tools: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._node_def = node
        self._run_ctx = run_ctx
        self._tools = {} if tools is None else tools

    @property
    def node_def(self) -> WorkflowNode:
        return self._node_def

    @property
    def run_ctx(self) -> RunContext:
        return self._run_ctx

    @classmethod
    def build(
        cls,
        *,
        node: WorkflowNode,
        run_ctx: RunContext,
        tools: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> CanvasNode:
        """构造节点：pydantic 模型的 __init__ 签名无法带自定义参数，这里统一入口。"""

        return cls(node=node, run_ctx=run_ctx, tools=tools, **kwargs)

    def _state(self, ctx: Any) -> dict[str, Any]:
        """读取共享状态快照。

        ADK 的 ctx.state 是自定义映射而不是 dict，必须用 to_dict() 取全量快照，
        直接 dict(state) 会按迭代协议报错。
        """

        state = getattr(ctx, "state", None)
        if state is None:
            raise RuntimeError(f"节点缺少 state: {self._node_def.id}")
        to_dict = getattr(state, "to_dict", None)
        if not callable(to_dict):
            raise TypeError(f"节点状态无法快照: {self._node_def.id}")
        raw = to_dict()
        if not isinstance(raw, dict):
            raise TypeError(f"state.to_dict() 必须返回 dict: {self._node_def.id}")
        return raw

    async def execute(self, *, ctx: Any, state: dict[str, Any]) -> Any:
        """子类实现节点逻辑；默认抛错，避免漏实现被静默跳过。"""

        raise NotImplementedError(
            f"节点未实现 execute: {self._node_def.id} kind={self._node_def.kind} "
            f"ctx={type(ctx).__name__} state_keys={sorted(state)}"
        )

    async def run_node_impl(
        self, *, ctx: Any, node_input: Any
    ) -> AsyncGenerator[Any, None]:
        node = self._node_def
        # 已经停在人工节点时，下游节点不得执行（等于跳过确认），直接抛错让图停下；
        # 这种中止由运行服务识别为「等待人工」而不是失败。
        # 人工节点本身也算下游：两个审批/输入节点连着时，第一次停下后不能再
        # 悄悄跑掉第二个，否则用户只会看到最后一个，前一个等于被跳过。
        if self._run_ctx.gate_hit is not None:
            raise WorkflowGateBlocked(
                f"工作流停在人工节点 {self._run_ctx.gate_hit}，节点 {node.id} 未执行"
            )
        # 单节点试跑已经跑到目标节点：下游不能再跑，否则等于偷偷执行了整条链路
        if self._run_ctx.stopped:
            raise WorkflowStopAfter(
                f"单节点试跑在 {self._run_ctx.stop_after} 结束，节点 {node.id} 未执行"
            )
        attempt = self._run_ctx.next_attempt(node.id)
        self._attempt = attempt
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        state = self._state(ctx)
        try:
            raw = await self.execute(ctx=ctx, state=state)
        except Exception as exc:
            self._run_ctx.outcomes.append(
                NodeOutcome(
                    node_id=node.id,
                    node_kind=node.kind,
                    attempt=attempt,
                    status="failed",
                    output=None,
                    error=f"{type(exc).__name__}: {exc}",
                    duration_ms=int((loop.time() - started_at) * 1000),
                )
            )
            logger.exception(
                "工作流节点失败 node=%s kind=%s attempt=%s", node.id, node.kind, attempt
            )
            raise

        duration_ms = int((loop.time() - started_at) * 1000)
        route = ""
        value = raw
        if isinstance(raw, Routed):
            route = raw.route
            value = raw.value
        if node.output_key != "":
            ctx.state[node.output_key] = value

        self._run_ctx.outcomes.append(
            NodeOutcome(
                node_id=node.id,
                node_kind=node.kind,
                attempt=attempt,
                status="succeeded",
                output=value,
                error="",
                duration_ms=duration_ms,
                route=route,
            )
        )
        logger.info(
            "工作流节点完成 node=%s kind=%s attempt=%s route=%s duration_ms=%s",
            node.id,
            node.kind,
            attempt,
            route,
            duration_ms,
        )
        # 单节点试跑：目标节点跑完就收工，不再往下游推进
        if self._run_ctx.stop_after == node.id:
            self._run_ctx.stopped = True
            raise WorkflowStopAfter(f"单节点试跑停在 {node.id}")
        if route == "":
            yield value
            return
        yield Event(
            output=value,
            actions=EventActions(route=route),
        )


def build_edges(definition: WorkflowDefinition, node_map: dict[str, Any]) -> list[Any]:
    """画布连线 → ADK Edge。

    开始节点会作为真实节点执行（它要写初始状态），所以从 ADK 的 START 连到它，
    之后再走它自己的出边。
    """

    from google.adk.workflow import START

    start_id = definition.require_start().id
    edges: list[Any] = [Edge(from_node=START, to_node=node_map[start_id])]
    for edge in definition.edges:
        edges.append(
            Edge(
                from_node=node_map[edge.source],
                to_node=node_map[edge.target],
                route=None if edge.route == "" else edge.route,
            )
        )
    return edges


class PassThroughNode(CanvasNode):
    """已确认过的人工节点：恢复时直接透传，不再停一次。"""

    async def execute(self, *, ctx: Any, state: dict[str, Any]) -> Any:
        key = self._node_def.output_key
        # 人工输入的值已经在状态里：透传不能用一个占位对象把它覆盖掉
        if key != "" and key in state:
            return state[key]
        return {"gate": self._node_def.id, "approved": True}


class _ValidationNode(Node):
    """校验用空节点：不读状态、不写状态。"""

    async def run_node_impl(
        self, *, ctx: Any, node_input: Any
    ) -> AsyncGenerator[Any, None]:
        yield node_input


def build_validation_graph(definition: WorkflowDefinition) -> Workflow:
    """只做结构校验：节点全部用空实现顶替，不查库、不触达模型。"""

    nodes: dict[str, Any] = {
        node.id: _ValidationNode(name=node.id) for node in definition.nodes
    }
    edges = build_edges(definition, nodes)
    return Workflow(name="validation", edges=edges)


def build_node(
    *,
    node: WorkflowNode,
    run_ctx: RunContext,
    tools: dict[str, Any],
) -> Node:
    """按 kind 构造具体的节点实例。"""

    from app.adk_runtime.workflow_node_impl import NODE_BUILDERS

    builder = NODE_BUILDERS.get(node.kind)
    if builder is None:
        raise ValueError(f"不支持的节点类型: {node.kind}")
    retry_config = (
        None
        if node.retry_count <= 0
        else RetryConfig(max_attempts=node.retry_count + 1)
    )
    built: Node = builder.build(
        node=node,
        run_ctx=run_ctx,
        tools=tools,
        name=node.id,
        description=node.label,
        retry_config=retry_config,
        timeout=node.timeout_seconds,
    )
    return built


def render_text(text: str, state: dict[str, Any]) -> str:
    return workflow_vars.render(text, state)


def preview(value: Any, limit: int = 4000) -> str:
    text = serialize(value)
    return text if len(text) <= limit else text[:limit] + "…"
