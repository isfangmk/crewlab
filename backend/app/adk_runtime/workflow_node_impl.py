"""各类画布节点的具体实现。

每个 builder 接收 WorkflowNode 与运行上下文，返回一个可被 ADK 图调度的节点。
调度与循环语义完全由 ADK 负责，这里只表达「这一步做什么」。
"""

from __future__ import annotations

import asyncio
import http.client
import inspect
import json
import logging
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from app.adk_runtime.workflow_nodes import CanvasNode, Routed, serialize
from app.schemas.workflow import WorkflowNode  # noqa: F401 — 供 builder 签名使用
from app.services import workflow_vars

logger = logging.getLogger(__name__)

# 代码节点允许调用的内置函数白名单说明（写进节点说明，供前端提示）
CODE_NODE_CONTRACT = "在代码里定义 run(state) -> Any，返回值的 JSON 可序列化结果会写入 output_key。"


class StartNode(CanvasNode):
    """开始节点：把运行输入写进共享状态，后续节点用 {{变量}} 读取。"""

    async def execute(self, *, ctx: Any, state: dict[str, Any]) -> Any:
        from app.services.workflow_service import initial_state

        values = self._tools.get("run_values")
        if values is None:
            raise RuntimeError("开始节点缺少运行输入")
        state = initial_state(self.node_def, dict(values))
        # 恢复运行时状态里还有人工节点的产出（不在开始节点声明的变量里），
        # 必须原样带进共享状态：否则下游读 {{note}} 拿不到用户刚提交的值，
        # 人工节点透传时还会用自己的占位结果把它覆盖掉。
        for key, value in values.items():
            state.setdefault(key, value)
        for key, value in state.items():
            ctx.state[key] = value
        logger.info("开始节点写入状态 node=%s keys=%s", self.node_def.id, sorted(state))
        return state


class EndNode(CanvasNode):
    """结束节点：把上游结果作为整次运行的输出。"""

    async def execute(self, *, ctx: Any, state: dict[str, Any]) -> Any:
        if self.node_def.output_key != "":
            return workflow_vars.render_value(
                "{{" + self.node_def.output_key + "}}", state
            )
        return {key: state[key] for key in sorted(state)}


class JoinNodeImpl(CanvasNode):
    """汇合点：把各前驱输出按节点名聚合。

    ADK 默认不等前驱，节点会按先到的那条入边执行；汇合点必须等所有入边跑完，
    否则并行分支里慢的那条会被后续节点的中止（人工节点、试跑收工）直接取消。
    """

    @property
    def _requires_all_predecessors(self) -> bool:
        return True

    async def execute(self, *, ctx: Any, state: dict[str, Any]) -> Any:
        return {key: state[key] for key in sorted(state)}


class KnowledgeNode(CanvasNode):
    """知识检索：查询语句支持 {{变量}}。"""

    async def execute(self, *, ctx: Any, state: dict[str, Any]) -> Any:
        from app.services.knowledge_service import search_for_agent
        from app.services.knowledge_scope import KnowledgeScope

        node = self.node_def
        query = workflow_vars.render(node.query, state)
        scope = (
            KnowledgeScope(paths=tuple(node.paths), doc_ids=())
            if node.paths
            else None
        )
        logger.info(
            "知识检索节点 node=%s query_len=%s paths=%s limit=%s",
            node.id,
            len(query),
            list(node.paths),
            node.limit,
        )
        return await search_for_agent(query, node.limit, scope=scope)


class SearchNode(CanvasNode):
    """联网搜索：固定走 DeepSeek 搜索凭证。"""

    async def execute(self, *, ctx: Any, state: dict[str, Any]) -> Any:
        from app.services.web_search_service import deepseek_web_search

        base_url = self._tools.get("web_search_base_url")
        api_key = self._tools.get("web_search_api_key")
        if not base_url or not api_key:
            raise LookupError("联网搜索节点缺少 DeepSeek 搜索凭证")
        node = self.node_def
        query = workflow_vars.render(node.query, state)
        return await deepseek_web_search(
            base_url=str(base_url), api_key=str(api_key), query=query, count=node.limit
        )


class McpNode(CanvasNode):
    """MCP 工具调用：按 server + tool 直接调，不等 LLM 决策。"""

    async def execute(self, *, ctx: Any, state: dict[str, Any]) -> Any:
        node = self.node_def
        args = workflow_vars.render_map(node.arguments, state)
        server = self._tools.get("mcp_servers", {}).get(node.mcp_server)
        if server is None:
            raise LookupError(f"MCP Server 不存在或未启用: {node.mcp_server}")
        from app.services import mcp_service

        toolset = await mcp_service.build_mcp_toolset(self.run_ctx.db, server)
        try:
            tools = await toolset.get_tools()
            target = next((t for t in tools if t.name == node.mcp_tool), None)
            if target is None:
                raise LookupError(
                    f"MCP Server {node.mcp_server} 没有工具: {node.mcp_tool}"
                )
            logger.info(
                "MCP 节点调用 node=%s server=%s tool=%s args=%s",
                node.id,
                node.mcp_server,
                node.mcp_tool,
                serialize(args),
            )
            result = await target.run_async(args=args, tool_context=ctx)
        finally:
            # toolset 是 MCP session manager，必须显式关闭
            await toolset.close()
        return result


class HttpNode(CanvasNode):
    """HTTP 请求：只允许 http/https，超时由节点 timeout 兜底。"""

    async def execute(self, *, ctx: Any, state: dict[str, Any]) -> Any:
        node = self.node_def
        url = workflow_vars.render(node.url, state)
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"HTTP 节点只支持 http/https: {url}")
        headers = {
            key: workflow_vars.render(value, state)
            for key, value in node.headers.items()
        }
        body = workflow_vars.render(node.body, state) if node.body != "" else None
        if body is not None and "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"
        logger.info(
            "HTTP 节点请求 node=%s method=%s host=%s path=%s body_len=%s",
            node.id,
            node.method,
            parsed.netloc,
            parsed.path,
            len(body or ""),
        )
        return await asyncio.to_thread(
            _http_request, node.method, url, headers, body
        )


def _http_request(
    method: str, url: str, headers: dict[str, str], body: str | None
) -> dict[str, Any]:
    data = None if body is None else body.encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            text = response.read().decode("utf-8", errors="replace")
            return {
                "status": response.status,
                "headers": dict(response.headers.items()),
                "body": _maybe_json(text),
            }
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        return {"status": exc.code, "headers": dict(exc.headers.items()), "body": _maybe_json(text)}
    except (urllib.error.URLError, socket.timeout, http.client.HTTPException) as exc:
        raise ConnectionError(f"HTTP 请求失败: {type(exc).__name__}: {exc}") from exc


def _maybe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


class CodeNode(CanvasNode):
    """代码节点：在受限命名空间里执行 run(state)。

    只暴露纯计算能力，不提供 import / open / eval，避免画布代码碰到宿主资源。
    需要用外部服务请改用 HTTP / MCP 节点。
    """

    async def execute(self, *, ctx: Any, state: dict[str, Any]) -> Any:
        code = self.node_def.code
        namespace: dict[str, Any] = {
            "json": json,
            "len": len,
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
            "list": list,
            "dict": dict,
            "sum": sum,
            "min": min,
            "max": max,
            "sorted": sorted,
            "enumerate": enumerate,
            "range": range,
            "abs": abs,
            "round": round,
            "zip": zip,
        }
        compiled = compile(code, f"<workflow:{self.node_def.id}>", "exec")
        exec(compiled, namespace, namespace)  # noqa: S102 — 受限命名空间内执行画布代码
        run = namespace.get("run")
        if not callable(run):
            raise ValueError(f"代码节点必须定义 run(state): {self.node_def.id}")
        result: Any = run(dict(state))
        if inspect.isawaitable(result):
            result = await result
        json.dumps(result, ensure_ascii=False, default=str)
        return result


class AssignNode(CanvasNode):
    """变量赋值：按顺序写多个状态变量。"""

    async def execute(self, *, ctx: Any, state: dict[str, Any]) -> Any:
        assigned: dict[str, Any] = {}
        # 逐个赋值时要能看到前面刚写的变量，所以边写边更新本地视图
        local = dict(state)
        for key, template in self.node_def.assignments.items():
            value = workflow_vars.render_value(template, local)
            ctx.state[key] = value
            local[key] = value
            assigned[key] = value
        logger.info("变量赋值节点 node=%s keys=%s", self.node_def.id, sorted(assigned))
        return assigned


class SwitchNode(CanvasNode):
    """条件分支：返回 route 让 ADK 选择出边。"""

    async def execute(self, *, ctx: Any, state: dict[str, Any]) -> Any:
        node = self.node_def
        branches = list(node.branches)
        if node.branch_mode == "model":
            branch, value = await self._decide_by_model(branches, state)
            return Routed(route=branch, value=value)
        result = workflow_vars.resolve_conditional(node.expression, state)
        # 表达式模式：branches[0] 为真分支，branches[1] 为假分支
        branch = branches[0] if result else branches[1]
        logger.info(
            "条件分支 node=%s expr=%s result=%s branch=%s",
            node.id,
            node.expression,
            result,
            branch,
        )
        return Routed(route=branch, value={"branch": branch, "result": result})

    async def _decide_by_model(
        self, branches: list[str], state: dict[str, Any]
    ) -> tuple[str, Any]:
        node = self.node_def
        agent = self._tools.get("switch_agent")
        if agent is None:
            raise LookupError(f"条件分支缺少模型装配: {node.id}")
        from google.adk.runners import InMemoryRunner
        from google.genai import types

        options = " / ".join(branches)
        prompt = (
            f"{workflow_vars.render(node.expression, state)}\n\n"
            f"只允许回答以下之一：{options}"
        )
        runner = InMemoryRunner(agent=agent, app_name="workflow_branch")
        try:
            session = await runner.session_service.create_session(
                app_name="workflow_branch", user_id="workflow"
            )
            answer = ""
            async for event in runner.run_async(
                user_id="workflow",
                session_id=session.id,
                new_message=types.Content(
                    role="user", parts=[types.Part(text=prompt)]
                ),
            ):
                if event.content is None or event.content.parts is None:
                    continue
                for part in event.content.parts:
                    if part.text:
                        answer += part.text
        finally:
            await runner.close()
        picked = next((b for b in branches if b in answer), None)
        if picked is None:
            raise ValueError(
                f"模型没有给出合法分支 node={node.id} answer={answer!r} branches={branches}"
            )
        logger.info("模型分支 node=%s picked=%s", node.id, picked)
        return picked, {"branch": picked, "reason": answer}


# 循环节点的两个出口路由名
LOOP_ROUTE_CONTINUE = "loop"
LOOP_ROUTE_EXIT = "exit"


class LoopNode(CanvasNode):
    """循环条件：判断是否继续下一轮，返回 loop / exit 路由。"""

    async def execute(self, *, ctx: Any, state: dict[str, Any]) -> Any:
        node = self.node_def
        # 轮次记在状态里，避免依赖 ADK 内部计数
        counter_key = f"__loop_{node.id}"
        iteration = int(state.get(counter_key, 0)) + 1
        ctx.state[counter_key] = iteration
        if node.expression == "":
            keep_going = iteration < node.max_iterations
        else:
            keep_going = workflow_vars.resolve_conditional(node.expression, state)
            if iteration >= node.max_iterations:
                keep_going = False
        route = LOOP_ROUTE_CONTINUE if keep_going else LOOP_ROUTE_EXIT
        logger.info(
            "循环节点 node=%s iteration=%s max=%s route=%s",
            node.id,
            iteration,
            node.max_iterations,
            route,
        )
        return Routed(
            route=route, value={"iteration": iteration, "continue": keep_going}
        )


class GateNode(CanvasNode):
    """人工审批 / 人工输入节点。

    执行到这里就把运行置为 waiting：节点本身只负责把“等什么”记到状态与结果里，
    真正的暂停与恢复由运行服务控制（ADK 图会继续跑，但下游不会被用户看到）。
    """

    async def execute(self, *, ctx: Any, state: dict[str, Any]) -> Any:
        node = self.node_def
        prompt = workflow_vars.render(node.prompt, state) if node.prompt else ""
        ctx.state[f"__gate_{node.id}"] = {
            "kind": node.kind,
            "prompt": prompt,
            "output_key": node.output_key,
        }
        # 置位后（无论本次是否首次命中）都不再允许下游执行
        self.run_ctx.gate_hit = node.id
        logger.info("人工节点命中 node=%s kind=%s", node.id, node.kind)
        return {"gate": node.id, "kind": node.kind, "prompt": prompt}


NODE_BUILDERS: dict[str, type[CanvasNode]] = {
    "start": StartNode,
    "approval": GateNode,
    "input": GateNode,
    "end": EndNode,
    "join": JoinNodeImpl,
    "knowledge": KnowledgeNode,
    "search": SearchNode,
    "mcp": McpNode,
    "http": HttpNode,
    "code": CodeNode,
    "assign": AssignNode,
    "switch": SwitchNode,
    "loop": LoopNode,
}
