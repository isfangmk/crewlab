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
from collections.abc import AsyncGenerator, Awaitable, Callable
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
    # 节点执行前看到的状态快照，即面板上的「输入」
    node_input: Any
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
    # 实时事件回调：节点开始/结束即时推给运行服务，未订阅时为 None
    on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None

    def next_attempt(self, node_id: str) -> int:
        attempt = self.attempts.get(node_id, 0) + 1
        self.attempts[node_id] = attempt
        return attempt

    async def emit(self, event: dict[str, Any]) -> None:
        """推送一条运行事件；没有订阅者时什么都不做。"""

        if self.on_event is None:
            return
        await self.on_event(event)


def state_snapshot(ctx: Any) -> dict[str, Any]:
    """取 ADK 上下文的共享状态快照。

    ctx.state 是自定义映射而不是 dict，必须用 to_dict() 取全量快照，
    直接 dict(state) 会按迭代协议报错。
    """

    state = getattr(ctx, "state", None)
    if state is None:
        raise RuntimeError("上下文缺少 state")
    to_dict = getattr(state, "to_dict", None)
    if not callable(to_dict):
        raise TypeError(f"state 无法快照: {type(state).__name__}")
    raw = to_dict()
    if not isinstance(raw, dict):
        raise TypeError("state.to_dict() 必须返回 dict")
    return raw


def upstream_outputs(
    node: WorkflowNode, definition: WorkflowDefinition, state: dict[str, Any]
) -> dict[str, Any]:
    """入边带过来的上游产出：按前驱节点的结果变量取值。

    ADK 往下游节点传的就是上游节点的输出，所以这就是这一步真正拿到的输入。
    前驱没声明结果变量时拿不到可命名的值，返回空交由调用方决定展示什么。
    """

    by_id = {item.id: item for item in definition.nodes}
    merged: dict[str, Any] = {}
    for edge in definition.edges:
        if edge.target != node.id:
            continue
        source = by_id.get(edge.source)
        key = "" if source is None else source.output_key
        if key != "" and key in state:
            merged[key] = state[key]
    return merged


def template_values(text: str, state: dict[str, Any]) -> dict[str, Any]:
    """模板里引用到的变量及其当前值。

    只按名字取值，缺的变量不硬凑成空值——面板要的是「这一步实际吃到了什么」，
    而不是一份补全过的参数表。
    """

    return {
        name: state[name]
        for name in workflow_vars.referenced_names(text)
        if name in state
    }


def template_input(text: str, state: dict[str, Any], literal_key: str) -> dict[str, Any]:
    """模板类节点的输入：引用变量时列出变量值，固定文本时列出这段文本。

    节点自己声明这次读了什么，面板照着渲染。
    固定文本没有引用任何变量，把原文放出来，才不至于整块输入是空的。
    """

    values = template_values(text, state)
    if values != {} or text.strip() == "":
        return values
    return {literal_key: text}


def serialize(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return repr(value)


class WorkflowGateBlocked(Exception):
    """运行已停在人工节点，下游节点因此未执行（不是失败）。"""


class WorkflowStopAfter(Exception):
    """单节点试跑跑到目标节点后主动收工，下游未执行（不是失败）。"""


class WorkflowStopped(Exception):
    """用户点了「停止运行」，这一次运行在下一个事件边界收工。"""


@dataclass
class Routed:
    """带路由的节点输出：route 由 ADK 用来匹配 Edge.route。"""

    route: str
    value: Any = None


def build_llm_callbacks(
    *, node: WorkflowNode, run_ctx: RunContext
) -> tuple[Any, Any, Any]:
    """LLM 节点的结果写回与执行记录。

    agent 节点是 ADK 的 LlmAgent，不是 CanvasNode，不走统一的 output_key 写回、
    也不会自己发运行事件，所以这两件事都在回调里补上；否则画布上的 {{节点}}
    变量永远是空的，面板看不到这一步的输入，流式运行时这个节点也不会亮。

    after_agent_callback 只拿得到上下文、拿不到本次模型响应，因此写回放在
    after_model_callback：那里能拿到 LlmResponse 的正文。
    """

    started_at: dict[str, float] = {}
    attempt: dict[str, int] = {}
    node_input: dict[str, Any] = {}

    async def before(callback_context: Any) -> None:
        attempt["value"] = run_ctx.next_attempt(node.id)
        started_at["value"] = asyncio.get_running_loop().time()
        state = state_snapshot(callback_context)
        # 模型节点拿到的是上游节点的产出（ADK 把它当成本轮用户消息）；
        # 上游没留下可命名的结果变量时，退到指令里引用的变量。
        inputs = upstream_outputs(node, run_ctx.definition, state)
        if inputs == {}:
            inputs = template_input(node.instruction, state, "instruction")
        node_input["value"] = inputs
        await run_ctx.emit(
            {
                "event": "node_started",
                "node_id": node.id,
                "node_kind": node.kind,
                "label": node.label,
                "attempt": attempt["value"],
                # 输入在开始前就已知：实时面板不必等落库就能看到这一步吃了什么
                "input": inputs,
            }
        )
        return None

    async def _record(status: str, text: str, error: str) -> None:
        now = asyncio.get_running_loop().time()
        current = attempt.get("value", 1)
        duration_ms = int((now - started_at.get("value", now)) * 1000)
        outcome = NodeOutcome(
            node_id=node.id,
            node_kind=node.kind,
            attempt=current,
            status=status,
            node_input=node_input.get("value", {}),
            output=text,
            error=error,
            duration_ms=duration_ms,
        )
        existing = next(
            (o for o in run_ctx.outcomes if o.node_id == node.id and o.attempt == current),
            None,
        )
        if existing is None:
            run_ctx.outcomes.append(outcome)
        else:
            # 同一次执行可能产生多段响应，只保留最后一段，避免记录里出现重复行
            existing.status = outcome.status
            existing.output = outcome.output
            existing.error = outcome.error
            existing.duration_ms = outcome.duration_ms
        await run_ctx.emit(
            {
                "event": "node_finished",
                "node_id": node.id,
                "node_kind": node.kind,
                "status": status,
                # 输出与落库同口径：结构化结果也能被实时面板原样展示
                "output": outcome.output,
                # 输入同样随事件下发：订阅方不必等落库就能对照这步吃了什么
                "input": outcome.node_input,
                "error": error,
                "duration_ms": duration_ms,
            }
        )

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
        await _record("succeeded", text, "")
        logger.info(
            "工作流 LLM 节点完成 node=%s output_key=%s chars=%s",
            node.id,
            node.output_key,
            len(text),
        )
        return None

    async def on_error(_callback_context: Any, _request: Any, error: Exception) -> None:
        await _record("failed", "", f"{type(error).__name__}: {error}")
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
        """读取共享状态快照；出错时带上节点 id，便于定位是哪一步。"""

        try:
            return state_snapshot(ctx)
        except (RuntimeError, TypeError) as exc:
            raise RuntimeError(f"节点 {self._node_def.id} 状态读取失败: {exc}") from exc

    async def before_execute(self, state: dict[str, Any]) -> None:
        """节点执行前的准备：默认什么都不做。

        输入要提前算才能实时展示，但「读到了什么」对有些节点只有跑一趟才知道
        （代码节点），这类节点在这里先执行一遍并登记，execute 再复用这次结果。
        """

        # 基类不读状态；保留参数是为了让子类签名一致
        del state

    def input_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        """这一步的输入：节点自己给出读了哪些变量、值是什么。

        每个节点类型都声明自己的输入（画布上它读的是它配置里/代码里真正用到的
        东西），而不是把整份共享状态倒给用户。
        调用时机是节点执行之前——面板要能一边跑一边看到输入，不能等跑完。
        """

        raise NotImplementedError(
            f"节点未声明输入: {self._node_def.id} kind={self._node_def.kind} "
            f"state_keys={sorted(state)}"
        )

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
        node_input_snapshot: dict[str, Any] = {}
        try:
            # 输入要在运行前算好：代码节点得先跑一趟才知道读了哪些键，开始节点
            # 执行时又会把其余运行参数写进状态，事后再快照会把「它自己写进去的」
            # 也算成输入。准备阶段炸了同样算这个节点失败，不能悄悄溜出去。
            await self.before_execute(state)
            node_input_snapshot = self.input_snapshot(state)
            # 节点开始：前端据此把节点点亮并跟动画布；输入一并下发，面板可以立刻展示
            await self._run_ctx.emit(
                {
                    "event": "node_started",
                    "node_id": node.id,
                    "node_kind": node.kind,
                    "label": node.label,
                    "attempt": attempt,
                    "input": node_input_snapshot,
                }
            )
            raw = await self.execute(ctx=ctx, state=state)
        except Exception as exc:
            duration_ms = int((loop.time() - started_at) * 1000)
            node_input_snapshot = self.input_snapshot(state)
            self._run_ctx.outcomes.append(
                NodeOutcome(
                    node_id=node.id,
                    node_kind=node.kind,
                    attempt=attempt,
                    status="failed",
                    node_input=node_input_snapshot,
                    output=None,
                    error=f"{type(exc).__name__}: {exc}",
                    duration_ms=duration_ms,
                )
            )
            logger.exception(
                "工作流节点失败 node=%s kind=%s attempt=%s", node.id, node.kind, attempt
            )
            await self._run_ctx.emit(
                {
                    "event": "node_finished",
                    "node_id": node.id,
                    "node_kind": node.kind,
                    "status": "failed",
                    # 失败也要带上输入：面板要能对着它看是哪个值出了问题
                    "input": node_input_snapshot,
                    "error": f"{type(exc).__name__}: {exc}",
                    "duration_ms": duration_ms,
                }
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
                # 输入是节点启动那一刻算好的快照：同一次执行从头到尾只算这一次，
                # 面板上的输入输出才对得上同一份数据
                node_input=node_input_snapshot,
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
        # 节点结束：前端用状态码上色；route 让条件分支只亮实际走的那条边
        await self._run_ctx.emit(
            {
                "event": "node_finished",
                "node_id": node.id,
                "node_kind": node.kind,
                "status": "succeeded",
                "input": node_input_snapshot,
                "output": value,
                "route": route,
                "duration_ms": duration_ms,
            }
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

    def input_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        # 确认过的节点：输入就是当初问的问题加上这次提交的答案
        inputs = template_input(self._node_def.prompt, state, "prompt")
        key = self._node_def.output_key
        if key != "" and key in state:
            inputs[key] = state[key]
        return inputs

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


# 落库的输入保留「变量」结构，只把单个值收口：
# 直接截断整段 JSON 会把它切成非法文本，前端就只能退化成一段纯文本，
# 没法按变量逐行展示这一步到底读了什么。
INPUT_VALUE_LIMIT = 600
INPUT_LIST_LIMIT = 20


def _shrink(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= INPUT_VALUE_LIMIT else value[:INPUT_VALUE_LIMIT] + "…"
    if isinstance(value, list):
        return [_shrink(item) for item in value[:INPUT_LIST_LIMIT]]
    if isinstance(value, dict):
        return {str(key): _shrink(item) for key, item in value.items()}
    return value


def preview_input(value: dict[str, Any]) -> str:
    """节点输入的落库格式：键还在，值按上限截断，结果仍是合法 JSON。

    以 __ 开头的是运行服务自己的记账（已确认的人工节点、循环轮次、人工占位），
    不是节点读的数据，落库前剔掉，免得面板里冒出用户看不懂的键。
    """

    visible = {
        str(key): _shrink(item)
        for key, item in value.items()
        if not str(key).startswith("__")
    }
    return serialize(visible)
