"""统一任务图定义：主代理一次下发节点与依赖，编排器据此展开成 DAG 执行。

一张图里 depends_on 表达全部拓扑语义：
  - 空依赖 = 起点，与其余起点并行起跑；
  - 单依赖 = 串行后继；
  - 多依赖 = 汇合，等全部前驱完成后才起跑。

节点用 spec 内的局部 id 互相引用（如 A / B / C），落库时另分配 node_uid
作事件路由标识。显式 edges 作为 depends_on 的补充，便于表达更复杂的连线。
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator

# 节点局部 id：字母开头，字母/数字/下划线/短横线，最长 64。
# 字母开头是为了与编排器注入的 start/end/join 保留 id 区分。
_NODE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
# 节点指定助理的标识：与 UserAgent 命名规则同款（小写字母开头的标识）。
_AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

# 编排器保留 id：任务节点不能用，否则与 start/end/join 冲突。
_RESERVED_NODE_IDS = {"start", "end", "join", "__start__", "__START__"}

# 工具名哨兵：显式声明「不挂任何工具」，区别于未声明的全量。
NO_TOOLS_SENTINEL = "__none__"

_MAX_NODES = 16
_MAX_TOOLS_PER_NODE = 8


class TaskGraphNodeSpec(BaseModel):
    """一个任务节点：局部 id 供依赖引用，node_uid 在落库时生成。"""

    id: str = Field(min_length=1, max_length=64, description="节点局部 id，供依赖引用，须字母开头且最长 64，不能用编排器保留名")
    name: str = Field(default="", max_length=128, description="节点展示名称，最长 128；留空时用默认名")
    target: str = Field(min_length=1, max_length=2000, description="节点目标，描述本节点要处理的对象或任务目标，最长 2000")
    instruction: str = Field(default="", max_length=8000, description="节点执行指令，告诉该节点具体做什么，最长 8000；留空用默认指令")
    tools: list[str] = Field(default_factory=list, description="本节点明确要调用的工具名列表，最多 8 个；生成图前先规划好每节点要用的工具并写进来（含指定 agent_id 的节点），节点执行时按声明直挂、不再 search→load 消耗 token；留空才由该助理的 searchable 目录自行检索")
    skills: list[str] = Field(default_factory=list, description="本节点明确要装配的技能 key 列表，最多 8 个；生成图前先规划好每节点要用的技能并写进来（含指定 agent_id 的节点），节点执行时按声明装配；留空 = 不装配任何技能")
    agent_id: str = Field(default="", max_length=64, description="可选：指定本节点复用该助理的人设/工具/知识范围；留空用通用节点人设")
    depends_on: list[str] = Field(default_factory=list, description="依赖的前驱节点局部 id 列表；空表示起点并行执行，多依赖需全部完成才启动")

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        node_id = value.strip()
        if not _NODE_ID_RE.match(node_id):
            raise ValueError(f"节点 id 不合法: {value!r}")
        if node_id.lower() in {item.lower() for item in _RESERVED_NODE_IDS}:
            raise ValueError(f"节点 id 是编排器保留字: {node_id}")
        return node_id

    @field_validator("agent_id")
    @classmethod
    def _validate_agent_id(cls, value: str) -> str:
        agent_id = str(value).strip()
        if agent_id == "":
            return ""
        if not _AGENT_ID_RE.fullmatch(agent_id):
            raise ValueError(f"agent_id 不合法: {value!r}")
        if agent_id in ("default", "platform-assistant"):
            raise ValueError(f"agent_id 不能为内置助理: {agent_id}")
        return agent_id

    @field_validator("tools")
    @classmethod
    def _validate_tools(cls, value: list[str]) -> list[str]:
        tools = [str(item).strip() for item in value if str(item).strip()]
        if len(tools) > _MAX_TOOLS_PER_NODE:
            raise ValueError(f"单节点工具数不能超过 {_MAX_TOOLS_PER_NODE}")
        return tools

    @field_validator("skills")
    @classmethod
    def _validate_skills(cls, value: list[str]) -> list[str]:
        skills = [str(item).strip() for item in value if str(item).strip()]
        if len(skills) > _MAX_TOOLS_PER_NODE:
            raise ValueError(f"单节点技能数不能超过 {_MAX_TOOLS_PER_NODE}")
        return skills

    @field_validator("depends_on")
    @classmethod
    def _validate_depends_on(cls, value: list[str]) -> list[str]:
        deps: list[str] = []
        for item in value:
            dep = str(item).strip()
            if dep and dep not in deps and dep != NO_TOOLS_SENTINEL:
                deps.append(dep)
        return deps


class TaskGraphEdgeSpec(BaseModel):
    """显式依赖边：source 完成后 target 才可执行；与 depends_on 合并去重。"""

    source: str = Field(min_length=1, max_length=64, description="依赖边起点节点 id；该节点完成后 target 才可执行")
    target: str = Field(min_length=1, max_length=64, description="依赖边终点节点 id；须等待 source 完成")


class TaskGraphSpec(BaseModel):
    """完整任务图：节点集合 + 依赖关系。校验通过后才能提交执行。"""

    title: str = Field(default="", max_length=256, description="任务图标题，最长 256；留空用默认标题")
    question: str = Field(default="", max_length=8000, description="本次任务图要解答的问题或总目标，最长 8000；留空用默认")
    nodes: list[TaskGraphNodeSpec] = Field(..., min_length=1, max_length=_MAX_NODES, description=f"任务节点列表，至少 1 个、最多 {_MAX_NODES} 个")
    edges: list[TaskGraphEdgeSpec] = Field(default_factory=list, description="显式依赖边列表，与节点的 depends_on 合并去重")

    @field_validator("nodes")
    @classmethod
    def _validate_nodes(cls, value: list[TaskGraphNodeSpec]) -> list[TaskGraphNodeSpec]:
        ids = [node.id for node in value]
        if len(ids) != len(set(ids)):
            raise ValueError("节点 id 不能重复")
        return value

    @model_validator(mode="after")
    def _normalize_and_validate(self) -> TaskGraphSpec:
        by_id = {node.id: node for node in self.nodes}

        # 显式 edges 合并进 depends_on，统一成一份依赖视图。
        for edge in self.edges:
            source, target = edge.source.strip(), edge.target.strip()
            if target not in by_id or source not in by_id:
                raise ValueError(f"依赖边引用了不存在的节点: {source} -> {target}")
            if source == target:
                raise ValueError(f"节点不能依赖自身: {source}")
            if source not in by_id[target].depends_on:
                by_id[target].depends_on.append(source)

        for node in self.nodes:
            for dep in node.depends_on:
                if dep not in by_id:
                    raise ValueError(f"节点 {node.id} 依赖了不存在的节点: {dep}")

        # 无环校验：本图的边都是无条件的，成环会被底层 DAG 引擎直接拒绝。
        # 拓扑排序走不完即说明有环。
        indegree = {node.id: len(node.depends_on) for node in self.nodes}
        consumers: dict[str, list[str]] = {node.id: [] for node in self.nodes}
        for node in self.nodes:
            for dep in node.depends_on:
                consumers[dep].append(node.id)
        queue = [node_id for node_id, degree in indegree.items() if degree == 0]
        visited = 0
        while queue:
            current = queue.pop()
            visited += 1
            for consumer in consumers[current]:
                indegree[consumer] -= 1
                if indegree[consumer] == 0:
                    queue.append(consumer)
        if visited != len(self.nodes):
            raise ValueError("任务图存在环，无法执行")
        return self

    def dependencies_of(self, node_id: str) -> tuple[str, ...]:
        """取节点的全部前驱（已合并显式边），稳定顺序。"""

        for node in self.nodes:
            if node.id == node_id:
                return tuple(node.depends_on)
        return ()

    def terminal_node_ids(self) -> tuple[str, ...]:
        """取没有后继的节点（图终点），按声明顺序。"""

        depended: set[str] = set()
        for node in self.nodes:
            depended.update(node.depends_on)
        return tuple(node.id for node in self.nodes if node.id not in depended)
