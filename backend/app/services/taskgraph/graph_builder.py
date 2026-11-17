"""图定义生成：把 TaskGraphSpec 转成底层可执行的 WorkflowDefinition DAG。

依赖语义全部落在图结构上（不是提示词路由）：
  - 无依赖节点：start 直接扇出，彼此并行；
  - 单依赖节点：前驱 → 本节点，串行；
  - 多依赖节点：插入 join 汇合节点等全部前驱完成，再放行到本任务节点。

前驱的输出数据不经图状态传递——节点执行时按依赖从数据库读已落库的结果，
join 只作调度屏障。这样避免扁平 state key 的数据载荷与命名空间问题。
"""

from __future__ import annotations

from app.schemas.task_graph import TaskGraphSpec
from app.schemas.workflow import (
    TaskSpec,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowVariable,
)

# 编排器保留节点 id（不与任务节点局部 id 冲突）
_START = "start"
_END = "end"
_JOIN_PREFIX = "join_"
_END_JOIN = "join_end"


def _task_node(
    node_id: str, spec, *, x: float, y: float, label: str = ""
) -> WorkflowNode:
    return WorkflowNode(
        id=node_id,
        kind="task",
        label=label or getattr(spec, "name", "") or node_id,
        x=x,
        y=y,
        output_key=f"{node_id}_result",
        task=TaskSpec(
            target=spec.target,
            instruction=spec.instruction,
            tools=list(spec.tools),
        ),
    )


def _depths(spec: TaskGraphSpec) -> dict[str, int]:
    """按依赖计算每个节点的层深（0 = 起点层），用于拓扑布局。"""

    by_id = {node.id: node for node in spec.nodes}
    memo: dict[str, int] = {}

    def depth(node_id: str) -> int:
        if node_id in memo:
            return memo[node_id]
        deps = by_id[node_id].depends_on
        value = 0 if not deps else 1 + max(depth(dep) for dep in deps)
        memo[node_id] = value
        return value

    for node in spec.nodes:
        depth(node.id)
    return memo


def build_definition(spec: TaskGraphSpec) -> WorkflowDefinition:
    """把任务图规格编译成 WorkflowDefinition。

    多依赖节点前插 join 汇合点；多个终点经 join_end 汇合到 end。
    任务节点 id 直接用局部 id，编排服务的 task_runner 据此路由回节点行。
    """

    depths = _depths(spec)
    by_depth: dict[int, list[str]] = {}
    for node in spec.nodes:
        by_depth.setdefault(depths[node.id], []).append(node.id)

    # 同层纵向居中展开
    positions: dict[str, tuple[float, float]] = {}
    for level, ids in by_depth.items():
        for index, node_id in enumerate(ids):
            positions[node_id] = (
                40 + level * 220,
                (index - (len(ids) - 1) / 2) * 160,
            )

    nodes: list[WorkflowNode] = [
        WorkflowNode(
            id=_START,
            kind="start",
            label="任务图",
            x=40,
            y=0,
            variables=[
                WorkflowVariable(
                    name="graph_uid", type="string", description="任务图标识"
                )
            ],
        )
    ]
    edges: list[WorkflowEdge] = []

    for node in spec.nodes:
        nx, ny = positions[node.id]
        nodes.append(_task_node(node.id, node, x=nx, y=ny))

        deps = node.depends_on
        if not deps:
            edges.append(
                WorkflowEdge(id=f"e_start_{node.id}", source=_START, target=node.id)
            )
        elif len(deps) == 1:
            edges.append(
                WorkflowEdge(id=f"e_{deps[0]}_{node.id}", source=deps[0], target=node.id)
            )
        else:
            # 多依赖：join 作屏障等全部前驱，再单边放行到任务节点。
            join_id = f"{_JOIN_PREFIX}{node.id}"
            jx, jy = nx - 110, ny
            nodes.append(WorkflowNode(id=join_id, kind="join", label="等全部前驱", x=jx, y=jy))
            for dep in deps:
                edges.append(
                    WorkflowEdge(id=f"e_{dep}_{join_id}", source=dep, target=join_id)
                )
            edges.append(
                WorkflowEdge(id=f"e_{join_id}_{node.id}", source=join_id, target=node.id)
            )

    terminals = list(spec.terminal_node_ids())
    end_x = max((positions[t][0] for t in terminals), default=40) + 220
    if len(terminals) == 1:
        edges.append(
            WorkflowEdge(id="e_end", source=terminals[0], target=_END)
        )
    else:
        # 多终点：join_end 等全部终点收口再进 end。
        nodes.append(
            WorkflowNode(id=_END_JOIN, kind="join", label="等全部收口", x=end_x, y=0)
        )
        for terminal in terminals:
            edges.append(
                WorkflowEdge(id=f"e_{terminal}_{_END_JOIN}", source=terminal, target=_END_JOIN)
            )
        edges.append(WorkflowEdge(id="e_end", source=_END_JOIN, target=_END))
        end_x += 200

    nodes.append(WorkflowNode(id=_END, kind="end", label="图收口", x=end_x, y=0))

    definition = WorkflowDefinition(nodes=nodes, edges=edges)
    return definition


def display_template() -> WorkflowDefinition:
    """任务图的画布展示模板（不直接执行）：动态占位节点表达任意 DAG。"""

    return WorkflowDefinition(
        nodes=[
            WorkflowNode(
                id=_START,
                kind="start",
                label="任务图",
                x=40,
                y=0,
                variables=[
                    WorkflowVariable(
                        name="graph_uid", type="string", description="任务图标识"
                    )
                ],
            ),
            WorkflowNode(
                id="dynamic_graph",
                kind="task",
                label="任务 · 按依赖动态展开",
                x=300,
                y=0,
                task=TaskSpec(
                    target="运行时由任务图定义展开：节点按 depends_on 编排并行/串行/汇合",
                    instruction="模板占位节点：画布展示动态 DAG 形态，实际执行由每次提交生成的定义承载",
                    tools=[],
                ),
            ),
            WorkflowNode(id=_END, kind="end", label="收口", x=560, y=0),
        ],
        edges=[
            WorkflowEdge(id="e1", source=_START, target="dynamic_graph"),
            WorkflowEdge(id="e2", source="dynamic_graph", target=_END),
        ],
    )
