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

from pydantic import PrivateAttr

from app.adk_runtime.workflow_nodes import (
    CanvasNode,
    Routed,
    serialize,
    template_input,
    template_values,
    upstream_outputs,
)
from app.schemas.workflow import WorkflowNode  # noqa: F401 — 供 builder 签名使用
from app.services import workflow_vars

logger = logging.getLogger(__name__)

# 代码节点允许调用的内置函数白名单说明（写进节点说明，供前端提示）
CODE_NODE_CONTRACT = "在代码里定义 run(state) -> Any，返回值的 JSON 可序列化结果会写入 output_key。"

# 代码节点可用的内置函数：只给纯计算能力，切断 open / __import__ / eval / compile，
# 画布代码因此碰不到宿主文件与进程。缺什么能力就用 HTTP / MCP 节点。
_CODE_BUILTIN_NAMES = (
    "abs",
    "all",
    "any",
    "bool",
    "dict",
    "divmod",
    "enumerate",
    "filter",
    "float",
    "format",
    "frozenset",
    "int",
    "isinstance",
    "issubclass",
    "len",
    "list",
    "map",
    "max",
    "min",
    "next",
    "pow",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "sorted",
    "str",
    "sum",
    "tuple",
    "zip",
)

# 代码里常用到的异常类型，允许显式抛出，便于把失败原因说清楚
_CODE_EXCEPTION_NAMES = (
    "ArithmeticError",
    "AssertionError",
    "AttributeError",
    "Exception",
    "IndexError",
    "KeyError",
    "RuntimeError",
    "StopIteration",
    "TypeError",
    "ValueError",
    "ZeroDivisionError",
)


def _code_builtins() -> dict[str, Any]:
    """代码节点的内置函数白名单。"""

    import builtins

    allowed = {name: getattr(builtins, name) for name in _CODE_BUILTIN_NAMES}
    allowed.update(
        {name: getattr(builtins, name) for name in _CODE_EXCEPTION_NAMES}
    )
    return allowed


# 沙盒脚本的输出标记：容器里跑完把结果打到 stdout，宿主机靠它把
# 「脚本输出」和「结果信封」分开。用户的 print 也会进 stdout，
# 所以标记必须是不会被正常打印撞上的形式。
SANDBOX_RESULT_MARKER = "__ONGRID_SANDBOX_RESULT__"

# 沙盒脚本的信封字段：
#   result：run(state) 的返回值
#   reads：代码实际读到的状态键（面板的「输入」）
#   error：失败信息
SANDBOX_SCRIPT_TEMPLATE = '''
import json as _json
import traceback as _traceback

_STATE = _json.loads({state_json!r})
_READS = {{}}


class _TrackedState(dict):
    """记录代码读到了哪些键；与宿主侧受限模式的语义一致。"""

    def __init__(self, source):
        super().__init__(source)

    def _hit(self, key):
        value = dict.__getitem__(self, key)
        _READS[str(key)] = value
        return value

    def _note(self, key):
        name = str(key)
        _READS[name] = dict.__getitem__(self, key) if dict.__contains__(self, key) else None

    def __getitem__(self, key):
        return self._hit(key)

    def __iter__(self):
        # 故意覆盖迭代器：整份复制只有在不走 C 层快速路径时才会逐个键取值，
        # 否则整份复制不会被记录，面板就看不到它读了什么。
        return dict.__iter__(self)

    def __contains__(self, key):
        self._note(key)
        return dict.__contains__(self, key)

    def get(self, key, default=None):
        if dict.__contains__(self, key):
            return self._hit(key)
        _READS[str(key)] = default
        return default

    def setdefault(self, key, default=None):
        if dict.__contains__(self, key):
            return self._hit(key)
        _READS[str(key)] = default
        return dict.setdefault(self, key, default)

    def pop(self, key, *args):
        self._note(key)
        return dict.pop(self, key, *args)

    def keys(self):
        for key in dict.keys(self):
            _READS[str(key)] = dict.__getitem__(self, key)
        return dict.keys(self)

    def values(self):
        for key in dict.keys(self):
            _READS[str(key)] = dict.__getitem__(self, key)
        return dict.values(self)

    def items(self):
        for key in dict.keys(self):
            _READS[str(key)] = dict.__getitem__(self, key)
        return dict.items(self)

    def copy(self):
        for key in dict.keys(self):
            _READS[str(key)] = dict.__getitem__(self, key)
        return dict(self)


def _emit(payload):
    print({marker!r} + _json.dumps(payload, ensure_ascii=False, default=str))


try:
    _namespace = {{"__name__": "__main__"}}
    exec(compile({code!r}, "<workflow-sandbox>", "exec"), _namespace, _namespace)
    _run = _namespace.get("run")
    if not callable(_run):
        raise ValueError("代码节点必须定义 run(state)")
    _value = _run(_TrackedState(_STATE))
    _emit({{"result": _value, "reads": _READS, "error": ""}})
except BaseException as _exc:  # noqa: BLE001
    _emit({{"result": None, "reads": _READS, "error": _traceback.format_exc()}})
'''


def _build_sandbox_script(*, code: str, state: dict[str, Any]) -> str:
    """把节点代码包成能在沙盒里独立跑起来的脚本。

    沙盒是另一个进程，节点代码拿不到宿主的状态对象：
    所以状态以 JSON 传进去，返回值以 JSON 传回来。
    """

    state_json = json.dumps(state, ensure_ascii=False, default=str)
    return SANDBOX_SCRIPT_TEMPLATE.format(
        state_json=state_json,
        code=code,
        marker=SANDBOX_RESULT_MARKER,
    )


def _parse_sandbox_payload(stdout: str) -> dict[str, Any]:
    """从沙盒 stdout 里取出结果信封。

    用户代码的 print 会混在 stdout 里，所以按标记行从后往前找；
    找不到说明脚本没跑到发结果那步，视为失败。
    """

    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if not stripped.startswith(SANDBOX_RESULT_MARKER):
            continue
        raw = stripped[len(SANDBOX_RESULT_MARKER) :]
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"沙盒结果解析失败: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("沙盒结果格式非法：不是对象")
        return payload
    raise RuntimeError("沙盒没有返回结果信封")


def _parse_sandbox_reads(stdout: str) -> dict[str, Any]:
    """取出代码读到的状态键；结果信封缺失时返回空。"""

    try:
        payload = _parse_sandbox_payload(stdout)
    except RuntimeError:
        return {}
    reads = payload.get("reads")
    if not isinstance(reads, dict):
        return {}
    return {str(key): value for key, value in reads.items()}


class StartNode(CanvasNode):
    """开始节点：把运行输入写进共享状态，后续节点用 {{变量}} 读取。"""

    def input_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        # 开始节点没有上游：它的输入就是这一轮提交的全部参数
        values = self._tools.get("run_values")
        if values is None:
            return {}
        return dict(values)

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

    def input_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        key = self.node_def.output_key
        if key == "":
            # 没选输出变量时结束节点把整份状态当成输出，它读的确实就是全部
            return {name: state[name] for name in sorted(state)}
        if key not in state:
            return {}
        return {key: state[key]}

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

    def input_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        """汇合点的输入是各条入边带过来的产出，不是整份状态。

        前驱节点都没声明结果变量时汇合点确实只是把整份状态继续往下带，
        此时按全量展示，不假装它只读了几个键。
        """

        merged = upstream_outputs(self.node_def, self.run_ctx.definition, state)
        if merged != {}:
            return merged
        return {name: state[name] for name in sorted(state)}

    async def execute(self, *, ctx: Any, state: dict[str, Any]) -> Any:
        return {key: state[key] for key in sorted(state)}


class KnowledgeNode(CanvasNode):
    """知识检索：查询语句支持 {{变量}}。"""

    def input_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        return template_input(self.node_def.query, state, "query")

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

    def input_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        return template_input(self.node_def.query, state, "query")

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

    def input_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        node = self.node_def
        # 调用目标与参数从哪来，是这一步最该被看清的东西
        inputs: dict[str, Any] = {"server": node.mcp_server, "tool": node.mcp_tool}
        for template in node.arguments.values():
            inputs.update(template_values(template, state))
        return inputs

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

    def input_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        node = self.node_def
        # 方法、URL 与各字段里引用的变量：这就是这一条请求的全部输入
        inputs: dict[str, Any] = {"method": node.method}
        inputs.update(template_input(node.url, state, "url"))
        for value in node.headers.values():
            inputs.update(template_values(value, state))
        inputs.update(template_values(node.body, state))
        return inputs

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


class _TrackedState(dict[str, Any]):
    """记录下来代码实际读了哪些键。

    代码节点拿到的本来就是整份 state，静态扫描只能猜：遍历一下、转手传给别的
    函数，读的键就说不清了。运行时记录是实测结果，也没得猜。

    只要代码按名字取到了某个键，就算读了这个键：包括取值、取默认值、
    判断存在、遍历键名这些写法。
    """
    def __init__(self, source: dict[str, Any]) -> None:
        super().__init__(source)
        self.reads: dict[str, Any] = {}

    def _hit(self, key: Any) -> Any:
        value = dict.__getitem__(self, key)
        self.reads[str(key)] = value
        return value

    def _note(self, key: Any) -> None:
        """记一笔「按这个名字找过」：状态里没有时记 None。"""

        name = str(key)
        self.reads[name] = dict.__getitem__(self, key) if dict.__contains__(self, key) else None

    def __getitem__(self, key: Any) -> Any:
        return self._hit(key)

    def __iter__(self) -> Any:
        # 故意覆盖迭代器：dict(state) / {**state} 只有在不走 C 层快速路径时才会
        # 逐个键取值，否则整份复制不会被记录，面板就看不到它读了什么。
        return dict.__iter__(self)

    def __contains__(self, key: Any) -> bool:
        self._note(key)
        return dict.__contains__(self, key)

    def get(self, key: Any, default: Any = None) -> Any:
        if dict.__contains__(self, key):
            return self._hit(key)
        # 没取到值也记一笔：代码确实按这个名字找过，只是当时状态里没有
        self.reads[str(key)] = default
        return default

    def setdefault(self, key: Any, default: Any = None) -> Any:
        if dict.__contains__(self, key):
            return self._hit(key)
        self.reads[str(key)] = default
        return dict.setdefault(self, key, default)

    def pop(self, key: Any, *args: Any) -> Any:
        self._note(key)
        return dict.pop(self, key, *args)

    def keys(self) -> Any:
        self._hit_names()
        return dict.keys(self)

    def values(self) -> Any:
        self._hit_all()
        return dict.values(self)

    def items(self) -> Any:
        self._hit_all()
        return dict.items(self)

    def copy(self) -> dict[str, Any]:
        self._hit_all()
        return dict(self)

    def _hit_names(self) -> None:
        for key in dict.keys(self):
            self.reads[str(key)] = dict.__getitem__(self, key)

    def _hit_all(self) -> None:
        self._hit_names()


class CodeNode(CanvasNode):
    """代码节点：在沙盒里执行 run(state)。

    默认走沙盒：画布上的代码是不可信输入（用户随手贴一段），
    在宿主进程里执行等于把宿主进程交给它。沙盒里跑则碰不到宿主机，
    代价是每次执行要开一次容器（几秒）。

    节点代码的契约与原来一致：定义 run(state) -> Any，
    返回值要能 JSON 序列化。跨沙盒边界只能传 JSON，
    所以返回非 JSON 值会被转成字符串（datetime 等）。

    读的是哪几个键要跑一趟才知道，所以这里自己执行一遍 run(state)：执行前
    跑一次拿到真实读取清单（面板在运行中就要看到输入），execute 复用这次结果，
    代码只跑一次。

    execution_mode = "restricted" 时回到旧的受限命名空间执行：
    纯计算、不能碰宿主资源，但也不能用第三方库、没有沙盒开销。
    """

    # 单个节点实例串行执行：提前执行的调用与 execute 不会撞在一起
    _lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)
    # 提前执行已经算出、等着 execute 收下的结果
    _planned: Any = PrivateAttr(default=None)
    _has_plan: bool = PrivateAttr(default=False)
    # 本次执行读到的状态键（面板的「输入」）
    _reads: dict[str, Any] = PrivateAttr(default_factory=dict)

    def input_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        return dict(self._reads)

    async def execute(self, *, ctx: Any, state: dict[str, Any]) -> Any:
        async with self._lock:
            # 运行前已经为面板跑过一次，直接复用，别让画布代码执行两遍
            if self._has_plan:
                self._has_plan = False
                return self._planned
            return await self._run_code(state)

    async def before_execute(self, state: dict[str, Any]) -> None:
        """节点开始前由基类调用：先跑一遍代码，把真实读取的键登记下来。"""

        async with self._lock:
            self._planned = await self._run_code(state)
            self._has_plan = True

    async def _run_code(self, state: dict[str, Any]) -> Any:
        self._reads = {}
        if self.node_def.execution_mode == "restricted":
            return await self._run_restricted(state)
        return await self._run_sandbox(state)

    async def _run_restricted(self, state: dict[str, Any]) -> Any:
        """受限模式：进程内执行，只给纯计算用的内置函数。"""

        code = self.node_def.code
        namespace: dict[str, Any] = {
            "json": json,
            "__builtins__": _code_builtins(),
        }
        compiled = compile(code, f"<workflow:{self.node_def.id}>", "exec")
        exec(compiled, namespace, namespace)  # noqa: S102 — 受限命名空间内执行画布代码
        run = namespace.get("run")
        if not callable(run):
            raise ValueError(f"代码节点必须定义 run(state): {self.node_def.id}")
        tracked = _TrackedState(state)
        try:
            result: Any = run(tracked)
            if inspect.isawaitable(result):
                result = await result
            json.dumps(result, ensure_ascii=False, default=str)
        finally:
            # 抛错时也留下已经读到的键：失败原因往往就出在这些值上
            self._reads = dict(tracked.reads)
        return result

    async def _run_sandbox(self, state: dict[str, Any]) -> Any:
        """沙盒模式：代码在容器里跑，宿主机只拿结果与读过的键。"""

        from app.services import sandbox_service

        script = _build_sandbox_script(
            code=self.node_def.code,
            state=state,
        )
        result = await sandbox_service.run_code(
            code=script,
            ctx=sandbox_service.SandboxContext(
                source=sandbox_service.SOURCE_WORKFLOW,
                source_ref=self.node_def.id,
                workflow_run_id=self.run_ctx.run_uid,
            ),
            libraries=list(self.node_def.libraries),
        )
        # 无论成功失败都把读到的键登记下来：失败原因往往就出在这些值上
        self._reads = _parse_sandbox_reads(result.stdout)
        if result.status != sandbox_service.STATUS_SUCCEEDED:
            raise RuntimeError(
                f"沙盒执行失败: {result.error or result.stderr or '未知原因'}"
            )
        payload = _parse_sandbox_payload(result.stdout)
        # 脚本自己把异常收进了信封，所以退出码是 0 也不代表代码跑成功了
        error = payload.get("error")
        if isinstance(error, str) and error != "":
            raise RuntimeError(f"代码节点执行失败:\n{error}")
        return payload["result"]


class AssignNode(CanvasNode):
    """变量赋值：按顺序写多个状态变量。"""

    def input_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        # 赋值节点读的是模板里引用到的上游变量；纯固定值赋值确实不读任何变量
        inputs: dict[str, Any] = {}
        for template in self.node_def.assignments.values():
            inputs.update(template_values(template, state))
        return inputs

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

    def input_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        # 判定依据与被判定的值一起给：只看路由看不出为什么走这条
        inputs: dict[str, Any] = {"expression": self.node_def.expression}
        inputs.update(template_values(self.node_def.expression, state))
        return inputs

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

    def input_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        node = self.node_def
        iteration = int(state.get(f"__loop_{node.id}", 0)) + 1
        inputs: dict[str, Any] = {
            "iteration": iteration,
            "max_iterations": node.max_iterations,
        }
        inputs.update(template_values(node.expression, state))
        return inputs

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

    def input_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        # 人工节点要人看的就是这条待确认的提示，以及里面引用的上游变量
        return template_input(self.node_def.prompt, state, "prompt")

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
