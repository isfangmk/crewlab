"""工作流画布定义：节点类型、结构校验与 ADK 图装配前的准备。

画布由前端 React Flow 产出，这里只认 nodes / edges 两种结构。
字段本地化文案走后端（title / title_en），与内置工具分组保持一致。

数据流转统一走「共享状态」：节点用 `{{变量}}` 引用上游字段，执行结果按节点的
`output_key` 写回状态，因此连线只表达控制流，不表达数据流。
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# ADK 节点名会进 node_path 与日志，限制成标识符，避免出现 `/`、`@` 等路径分隔符。
_NODE_ID_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
# 变量名：与节点 ID 同规则，便于 {{var}} 插值时不会误吞标点。
_VAR_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
# {{ var }} 插值；允许空格，不允许嵌套。
_PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

# ADK START 节点的保留名，画布上不允许占用。
# 只保留 ADK 真正占用的名字：`start` 是用户常用的节点 ID，不能一并禁掉。
_RESERVED_NODE_IDS = {"__START__"}

NodeKind = Literal[
    "start",
    "end",
    "join",
    "switch",
    "loop",
    "agent",
    "knowledge",
    "search",
    "mcp",
    "http",
    "code",
    "assign",
    "approval",
    "input",
]

# 节点分组：前端节点库按这个顺序分栏展示。
NODE_GROUPS: dict[str, tuple[str, str]] = {
    "flow": ("编排", "Flow"),
    "model": ("模型", "Model"),
    "action": ("工具与动作", "Tools & actions"),
    "human": ("人工协同", "Human-in-the-loop"),
}

# 每种节点的标题与分组；顺序即前端节点库里的展示顺序。
NODE_KINDS: dict[str, tuple[str, str, str]] = {
    "start": ("开始", "Start", "flow"),
    "end": ("结束", "End", "flow"),
    "join": ("汇合", "Join", "flow"),
    "switch": ("条件分支", "Switch", "flow"),
    "loop": ("循环", "Loop", "flow"),
    "agent": ("LLM 节点", "LLM node", "model"),
    "knowledge": ("知识检索", "Knowledge search", "action"),
    "search": ("联网搜索", "Web search", "action"),
    "mcp": ("MCP 工具", "MCP tool", "action"),
    "http": ("HTTP 请求", "HTTP request", "action"),
    "code": ("代码", "Code", "action"),
    "assign": ("变量赋值", "Set variables", "action"),
    "approval": ("人工审批", "Approval", "human"),
    "input": ("人工输入", "User input", "human"),
}

# 只能有一个、且必须有入边/出边的结构节点。
_SINGLETON_KINDS = {"start", "loop"}
_TERMINAL_KINDS = {"end"}

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


class WorkflowVariable(BaseModel):
    """一个工作流变量（用于开始节点的输入、赋值节点的输出）。

    required 表示运行必须由用户提供，因此与 default 互斥；
    default 为 None 才是「没设默认值」，空字符串是合法的字符串默认值。
    """

    name: str = Field(min_length=1, max_length=64)
    type: Literal["string", "number", "boolean", "json"] = "string"
    description: str = Field(default="", max_length=256)
    required: bool = False
    default: Any | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not _VAR_PATTERN.fullmatch(value):
            raise ValueError(f"变量名只能包含字母、数字、下划线，且以字母或下划线开头: {value}")
        return value

    @model_validator(mode="after")
    def _check_required_and_default(self) -> "WorkflowVariable":
        if self.required and self.default is not None:
            raise ValueError(
                f"变量 {self.name} 是必填项，不能设置默认值（必填意味着运行时必须由用户提供）"
            )
        if self.default is not None:
            self._check_default_type()
        return self

    def _check_default_type(self) -> None:
        """默认值必须与声明的类型一致；不做隐式转换，避免悄悄改掉用户填的值。"""

        value = self.default
        if self.type == "string":
            if not isinstance(value, str):
                raise ValueError(f"变量 {self.name} 是文本类型，默认值必须是字符串")
            return
        if self.type == "number":
            # bool 是 int 的子类，必须排除，否则 true 会被当成 1
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"变量 {self.name} 是数字类型，默认值必须是数字")
            return
        if self.type == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"变量 {self.name} 是布尔类型，默认值必须是 true/false")
            return
        return


class WorkflowNode(BaseModel):
    """一个图节点。各 kind 只允许填自己那部分字段，其余必须为空。"""

    id: str = Field(min_length=1, max_length=64)
    kind: NodeKind
    label: str = Field(default="", max_length=64)
    x: float = 0
    y: float = 0

    # 执行结果写回哪个状态变量；空串表示不写回（仅用副作用/中断的节点可以不写）
    output_key: str = Field(default="", max_length=64)

    # start：声明运行输入
    variables: list[WorkflowVariable] = Field(default_factory=list)

    # agent：节点所使用的模型（配置-模型里的 provider + 模型 id）
    provider: str = Field(default="", max_length=64)
    model: str = Field(default="", max_length=128)
    # agent：节点级指令
    instruction: str = Field(default="", max_length=8000)

    # knowledge / search / http 等节点的主参数，支持 {{变量}} 插值
    query: str = Field(default="", max_length=2000)
    limit: int = 5
    # knowledge：限定检索目录（空 = 全库）
    paths: list[str] = Field(default_factory=list)

    # mcp：server 名与工具原名
    mcp_server: str = Field(default="", max_length=128)
    mcp_tool: str = Field(default="", max_length=256)
    # mcp / http：参数表，值支持 {{变量}} 插值
    arguments: dict[str, str] = Field(default_factory=dict)

    # http
    method: str = Field(default="GET", max_length=8)
    url: str = Field(default="", max_length=2000)
    headers: dict[str, str] = Field(default_factory=dict)
    body: str = Field(default="", max_length=20000)

    # code：Python 片段，必须定义 run(state) -> Any
    code: str = Field(default="", max_length=20000)

    # assign：按顺序写多个变量
    assignments: dict[str, str] = Field(default_factory=dict)

    # switch：分支名列表，出边的 route 必须落在其中
    branches: list[str] = Field(default_factory=list)
    # switch：如何选分支。expr 支持 {{var}}，也可用 model 让 LLM 判断
    expression: str = Field(default="", max_length=2000)
    branch_mode: Literal["expr", "model"] = "expr"

    # loop：最大轮次（必须有出口条件，ADK 不允许无条件回边）
    max_iterations: int = Field(default=5, ge=1, le=100)

    # approval / input：给用户看的文案
    prompt: str = Field(default="", max_length=2000)

    # 节点级超时（秒）与重试次数
    timeout_seconds: float | None = None
    retry_count: int = 0

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if value in _RESERVED_NODE_IDS:
            raise ValueError(f"节点 ID 不能使用保留名: {value}")
        if _NODE_ID_PATTERN.fullmatch(value) is None:
            raise ValueError(
                f"节点 ID 只能包含字母、数字、下划线，且以字母或下划线开头: {value}"
            )
        return value

    @field_validator("output_key")
    @classmethod
    def _check_output_key(cls, value: str) -> str:
        if value == "":
            return value
        if not _VAR_PATTERN.fullmatch(value):
            raise ValueError(f"output_key 只能包含字母、数字、下划线: {value}")
        return value

    @field_validator("retry_count")
    @classmethod
    def _check_retry(cls, value: int) -> int:
        if value < 0 or value > 5:
            raise ValueError("retry_count 范围是 0..5")
        return value

    @field_validator("timeout_seconds")
    @classmethod
    def _check_timeout(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value <= 0 or value > 3600:
            raise ValueError("timeout_seconds 范围是 (0, 3600]")
        return value

    @field_validator("method")
    @classmethod
    def _check_method(cls, value: str) -> str:
        upper = value.upper()
        if upper not in HTTP_METHODS:
            raise ValueError(f"不支持的 HTTP 方法: {value}（可选 {list(HTTP_METHODS)}）")
        return upper

    @model_validator(mode="after")
    def _check_kind_fields(self) -> WorkflowNode:
        kind = self.kind
        if kind == "start":
            if len(self.variables) == 0:
                raise ValueError(f"开始节点必须声明至少一个输入变量: {self.id}")
            names = [v.name for v in self.variables]
            if len(set(names)) != len(names):
                raise ValueError(f"开始节点变量名重复: {self.id}")
            self._reject(kind, agent=True, query=True, mcp=True, http=True, code=True)
        elif kind == "agent":
            if self.provider == "" or self.model == "":
                raise ValueError(f"LLM 节点必须指定模型: {self.id}")
            if self.instruction == "":
                raise ValueError(f"LLM 节点必须有指令: {self.id}")
            self._reject(kind, variables=True, query=True, mcp=True, http=True, code=True)
        elif kind == "knowledge":
            if self.query == "":
                raise ValueError(f"知识检索节点必须有 query: {self.id}")
            self._reject(kind, variables=True, agent=True, mcp=True, http=True, code=True)
        elif kind == "search":
            if self.query == "":
                raise ValueError(f"联网搜索节点必须有 query: {self.id}")
            self._reject(kind, variables=True, agent=True, mcp=True, http=True, code=True)
        elif kind == "mcp":
            if self.mcp_server == "" or self.mcp_tool == "":
                raise ValueError(f"MCP 节点必须指定 mcp_server 与 mcp_tool: {self.id}")
            self._reject(kind, variables=True, agent=True, query=True, http=True, code=True)
        elif kind == "http":
            if self.url == "":
                raise ValueError(f"HTTP 节点必须有 url: {self.id}")
            self._reject(kind, variables=True, agent=True, query=True, mcp=True, code=True)
        elif kind == "code":
            if self.code.strip() == "":
                raise ValueError(f"代码节点必须有 code: {self.id}")
            self._reject(kind, variables=True, agent=True, query=True, mcp=True, http=True)
        elif kind == "assign":
            if len(self.assignments) == 0:
                raise ValueError(f"变量赋值节点必须有至少一个赋值: {self.id}")
            self._reject(kind, variables=True, agent=True, query=True, mcp=True, http=True, code=True)
        elif kind == "switch":
            if len(self.branches) < 2:
                raise ValueError(f"条件分支至少要两个分支: {self.id}")
            if len(set(self.branches)) != len(self.branches):
                raise ValueError(f"分支名重复: {self.id}")
            for branch in self.branches:
                if not _VAR_PATTERN.fullmatch(branch):
                    raise ValueError(f"分支名只能是标识符: {branch}")
            if self.branch_mode == "expr" and self.expression == "":
                raise ValueError(f"表达式模式必须填写表达式: {self.id}")
            self._reject(kind, variables=True, agent=True, query=True, mcp=True, http=True, code=True)
        elif kind == "loop":
            if self.max_iterations < 1:
                raise ValueError(f"循环轮次至少为 1: {self.id}")
            self._reject(kind, variables=True, agent=True, query=True, mcp=True, http=True, code=True)
        elif kind == "approval":
            if self.prompt == "":
                raise ValueError(f"人工审批节点必须有说明文案: {self.id}")
            self._reject(kind, variables=True, agent=True, query=True, mcp=True, http=True, code=True)
        elif kind == "input":
            if self.prompt == "":
                raise ValueError(f"人工输入节点必须有提示文案: {self.id}")
            self._reject(kind, variables=True, agent=True, query=True, mcp=True, http=True, code=True)
        else:
            # join / end 只允许位置、标签与 output_key
            self._reject(kind, variables=True, agent=True, query=True, mcp=True, http=True, code=True)
        return self

    def _reject(self, kind: str, **flags: bool) -> None:
        """把不属于该 kind 的字段显式判错，避免画布上填了却不生效。"""

        checks: dict[str, Any] = {
            "variables": self.variables,
            "agent": self.provider or self.model or self.instruction,
            "query": self.query or self.limit != 5 or self.paths,
            "mcp": self.mcp_server or self.mcp_tool or self.arguments,
            "http": (self.url or self.method != "GET" or self.headers or self.body),
            "code": self.code,
        }
        for key, value in checks.items():
            if not flags.get(key):
                continue
            has_value = bool(value)
            if has_value:
                raise ValueError(f"{kind} 节点不能设置 {key} 字段: {self.id}")


class WorkflowEdge(BaseModel):
    """一条连线；route 为空表示无条件边。"""

    id: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=64)
    target: str = Field(min_length=1, max_length=64)
    route: str = Field(default="", max_length=64)

    @field_validator("route")
    @classmethod
    def _check_route(cls, value: str) -> str:
        if value != "" and not _VAR_PATTERN.fullmatch(value):
            raise ValueError(f"route 只能包含字母、数字、下划线: {value}")
        return value


class WorkflowDefinition(BaseModel):
    """整张画布。"""

    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]

    @model_validator(mode="after")
    def _check_graph(self) -> WorkflowDefinition:
        by_id: dict[str, WorkflowNode] = {}
        for node in self.nodes:
            if node.id in by_id:
                raise ValueError(f"节点 ID 重复: {node.id}")
            by_id[node.id] = node

        starts = [n.id for n in self.nodes if n.kind == "start"]
        if len(starts) != 1:
            raise ValueError(f"必须有且只有一个开始节点，当前 {len(starts)} 个")
        start_id = starts[0]

        edge_keys: set[tuple[str, str, str]] = set()
        for edge in self.edges:
            if edge.source not in by_id:
                raise ValueError(f"连线起点不存在: {edge.source}")
            if edge.target not in by_id:
                raise ValueError(f"连线终点不存在: {edge.target}")
            key = (edge.source, edge.target, edge.route)
            if key in edge_keys:
                raise ValueError(f"连线重复: {edge.source} -> {edge.target} route={edge.route}")
            edge_keys.add(key)

        # 开始节点不能有入边；结束节点不能有出边
        for edge in self.edges:
            if edge.target == start_id:
                raise ValueError("开始节点不能有入边")
            if by_id[edge.source].kind == "end":
                raise ValueError(f"结束节点不能有出边: {edge.source}")

        # 条件分支的出边 route 必须落在 branches 里，且每个分支都要有出边
        for node in self.nodes:
            if node.kind != "switch":
                continue
            outgoing = [e for e in self.edges if e.source == node.id]
            if len(outgoing) == 0:
                raise ValueError(f"条件分支没有出边: {node.id}")
            declared = set(node.branches)
            used = {e.route for e in outgoing}
            if "" in used:
                raise ValueError(f"条件分支的出边必须带 route: {node.id}")
            unknown = sorted(used - declared)
            if unknown:
                raise ValueError(f"条件分支出边的 route 未在 branches 声明: {node.id} {unknown}")
            missing = sorted(declared - used)
            if missing:
                raise ValueError(f"条件分支缺少分支出边: {node.id} {missing}")

        # 汇合点不接受带路由的入边
        for edge in self.edges:
            if by_id[edge.target].kind == "join" and edge.route != "":
                raise ValueError(f"汇合点不接受带路由的入边: {edge.target}")

        # 从开始节点出发的可达性
        adjacency: dict[str, list[str]] = {n.id: [] for n in self.nodes}
        for edge in self.edges:
            adjacency[edge.source].append(edge.target)
        reached: set[str] = set()
        stack = [start_id]
        while stack:
            current = stack.pop()
            if current in reached:
                continue
            reached.add(current)
            stack.extend(adjacency[current])
        unreachable = sorted(set(by_id) - reached)
        if unreachable:
            raise ValueError(f"以下节点从开始节点不可达: {unreachable}")

        return self

    def require_start(self) -> WorkflowNode:
        for node in self.nodes:
            if node.kind == "start":
                return node
        raise ValueError("缺少开始节点")

    def starts(self) -> list[str]:
        """开始节点的直接后继：ADK 的 START 出边目标。"""

        start = self.require_start()
        return [e.target for e in self.edges if e.source == start.id]

    def incoming_count(self) -> dict[str, int]:
        counts = {n.id: 0 for n in self.nodes}
        for edge in self.edges:
            counts[edge.target] += 1
        return counts


def declared_variables(definition: WorkflowDefinition) -> list[WorkflowVariable]:
    return definition.require_start().variables


def unknown_placeholders(definition: WorkflowDefinition) -> list[str]:
    """找出引用了但没有任何节点产出的变量，保存时提示（不阻断）。"""

    produced = {v.name for v in declared_variables(definition)}
    for node in definition.nodes:
        if node.output_key != "":
            produced.add(node.output_key)
        if node.kind == "assign":
            produced.update(node.assignments.keys())
    missing: set[str] = set()
    for node in definition.nodes:
        for text in _node_texts(node):
            for name in _PLACEHOLDER_PATTERN.findall(text):
                if name not in produced:
                    missing.add(name)
    return sorted(missing)


def _node_texts(node: WorkflowNode) -> list[str]:
    texts = [node.instruction, node.query, node.url, node.body, node.expression, node.code]
    texts.extend(node.arguments.values())
    texts.extend(node.assignments.values())
    texts.extend(node.headers.values())
    return [t for t in texts if t != ""]


def parse_definition(raw: str) -> WorkflowDefinition:
    """把库里的 JSON 解析成画布定义；坏数据直接报错，不做兜底。"""

    data = json.loads(raw)
    if not isinstance(data, dict):
        raise TypeError("工作流定义必须是 JSON 对象")
    return WorkflowDefinition.model_validate(data)


def node_kind_title(kind: str) -> dict[str, str]:
    entry = NODE_KINDS.get(kind)
    if entry is None:
        raise ValueError(f"未知节点类型: {kind}")
    return {"key": kind, "title": entry[0], "title_en": entry[1], "group": entry[2]}


def node_group_title(group: str) -> dict[str, str]:
    entry = NODE_GROUPS.get(group)
    if entry is None:
        raise ValueError(f"未知节点分组: {group}")
    return {"key": group, "title": entry[0], "title_en": entry[1]}


def graph_warnings(definition: WorkflowDefinition) -> list[str]:
    """画布层面的提示（不阻断保存，但运行时会按 ADK 语义生效）。"""

    warnings: list[str] = []
    counts = definition.incoming_count()
    by_id = {n.id: n for n in definition.nodes}
    for node_id, count in counts.items():
        if by_id[node_id].kind in {"join", "start", "loop"}:
            continue
        if count > 1:
            if _is_branch_merge(node_id, definition, by_id):
                # 同一个条件分支的不同 route 互斥，一次运行只会走其中一条，
                # 汇合到同一个下游不会重复执行，不该提示。
                continue
            warnings.append(
                f"节点 {node_id} 有 {count} 条入边且不是汇合点，"
                "ADK 会按每条入边各执行一次；如需合并请插入汇合点。"
            )
    if not any(n.kind == "end" for n in definition.nodes):
        warnings.append("没有结束节点：运行结果取最后一个节点的输出。")
    missing = unknown_placeholders(definition)
    if missing:
        warnings.append(f"以下变量被引用但没有节点产出: {missing}")
    return warnings


def _is_branch_merge(
    node_id: str, definition: WorkflowDefinition, by_id: dict[str, WorkflowNode]
) -> bool:
    """入边是否分别来自同一个条件分支的不同 route。

    这种汇合一次运行只会走其中一条分支，不会重复执行节点，只是把分支重新合流。
    每条入边都向上回溯到最近的条件分支（跨过中间节点），只要它们都属于同一个
    条件分支、且 route 互不相同，就认定是互斥汇合。
    """

    incoming = [e for e in definition.edges if e.target == node_id]
    origins = [_route_origin(edge.source, definition, by_id) for edge in incoming]
    if any(origin is None for origin in origins):
        return False
    switch_ids = {origin[0] for origin in origins if origin is not None}
    routes = {origin[1] for origin in origins if origin is not None}
    return len(switch_ids) == 1 and len(routes) == len(origins)


def _route_origin(
    node_id: str, definition: WorkflowDefinition, by_id: dict[str, WorkflowNode]
) -> tuple[str, str] | None:
    """向上回溯到最近的条件分支，返回 (switch 节点, route)。

    回溯过程中遇到开始节点或循环节点就无法判定互斥，返回 None 让调用方按普通
    多入边处理（宁可多提示，也不要漏掉真的会重复执行的情况）。
    """

    hits: set[tuple[str, str]] = set()
    seen: set[str] = set()
    stack = [node_id]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        for edge in definition.edges:
            if edge.target != current:
                continue
            source = by_id[edge.source]
            if source.kind == "switch":
                hits.add((source.id, edge.route))
            elif source.kind in {"start", "loop"}:
                return None
            else:
                stack.append(source.id)
    if len(hits) != 1:
        return None
    return next(iter(hits))


def node_summary(node: WorkflowNode) -> dict[str, Any]:
    """节点在 API 上的扁平表示。"""

    return node.model_dump()
