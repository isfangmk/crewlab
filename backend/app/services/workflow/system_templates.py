"""系统编排模板：并行排查 / 计划执行两张工作流图的生成。

两种模式是同一编排语义的两种形状：并行 = 扇出等全部收口（join）再出结论，
串行 = 顺序链逐格推进。任务节点只描述「做什么」；「怎么执行」由编排发起方
在装配时经 task_runner 注入（无人值守执行内核留在原装配点）。
"""

from __future__ import annotations

from typing import Any

from app.schemas.workflow import (
    TaskSpec,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowVariable,
)


def _task_node(node_id: str, spec: dict[str, Any], output_key: str) -> WorkflowNode:
    return WorkflowNode(
        id=node_id,
        kind="task",
        label=str(spec.get("name") or node_id),
        output_key=output_key,
        task=TaskSpec(
            target=str(spec.get("target") or ""),
            instruction=str(spec.get("instruction") or ""),
            tools=[str(t) for t in (spec.get("tools") or [])],
        ),
    )


def investigation_definition(workers: list[dict[str, Any]]) -> WorkflowDefinition:
    """并行排查图：start → N 路 task（并行分支）→ join → end。

    join 等全部路终态才放行——「等待完成」由图结构保证；收口后的汇总结论
    由排查聚合在终态时写回对话流。总问题经运行输入（payload）进状态。
    """

    task_nodes = [_task_node(f"worker_{i}", spec, f"worker_{i}_result") for i, spec in enumerate(workers, start=1)]
    nodes: list[WorkflowNode] = [
        WorkflowNode(
            id="start",
            kind="start",
            label="排查问题",
            variables=[
                WorkflowVariable(name="question", type="string", description="要排查的总问题")
            ],
        ),
        *task_nodes,
        WorkflowNode(id="join", kind="join", label="等全部收口"),
        WorkflowNode(id="end", kind="end", label="收口"),
    ]
    edges: list[WorkflowEdge] = []
    for i in range(1, len(task_nodes) + 1):
        edges.append(WorkflowEdge(id=f"e_fan_out_{i}", source="start", target=f"worker_{i}"))
        edges.append(WorkflowEdge(id=f"e_fan_in_{i}", source=f"worker_{i}", target="join"))
    edges.append(WorkflowEdge(id="e_end", source="join", target="end"))
    return WorkflowDefinition(nodes=nodes, edges=edges)


def plan_definition(steps: list[dict[str, Any]]) -> WorkflowDefinition:
    """计划执行图（本段）：start → pending 步骤顺序链 → end。

    一段执行 = 一次运行；单步失败即暂停（下一步骤的入口闸收口），恢复时
    按剩余 pending 步骤生成新的一段。
    """

    task_nodes = [_task_node(f"step_{i}", spec, f"step_{i}_result") for i, spec in enumerate(steps, start=1)]
    nodes: list[WorkflowNode] = [
        WorkflowNode(
            id="start",
            kind="start",
            label="计划执行",
            variables=[
                WorkflowVariable(name="plan_uid", type="string", description="计划标识")
            ],
        ),
        *task_nodes,
        WorkflowNode(id="end", kind="end", label="段收口"),
    ]
    edges: list[WorkflowEdge] = []
    chain = ["start", *[f"step_{i}" for i in range(1, len(task_nodes) + 1)], "end"]
    for i in range(len(chain) - 1):
        edges.append(WorkflowEdge(id=f"e_chain_{i}", source=chain[i], target=chain[i + 1]))
    return WorkflowDefinition(nodes=nodes, edges=edges)
