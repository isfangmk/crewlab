"""任务图工具：主代理一次下发完整 DAG，用户确认后按拓扑执行。

submit_task_graph 建草稿（draft 态），get_task_graph 取进度快照，
update_task_graph 在草稿态调整整图。节点用局部 id 与 depends_on 表达依赖：
空依赖并行起跑，多依赖汇合，形成并行/串行/混合统一拓扑。
"""

from __future__ import annotations

import logging
from typing import Any

from app.adk_runtime.headless import WorkerBudget
from app.db import SessionLocal
from app.schemas.task_graph import TaskGraphNodeSpec, TaskGraphSpec

logger = logging.getLogger(__name__)


def _parse_spec(nodes_json: str, edges_json: str, title: str, question: str) -> TaskGraphSpec:
    """解析并校验图定义 JSON；抛 ValueError 由工具转成 error 结果。"""

    import json

    raw_nodes = json.loads(nodes_json)
    if not isinstance(raw_nodes, list):
        raise ValueError("nodes_json 必须是数组")
    nodes = [TaskGraphNodeSpec.model_validate(item) for item in raw_nodes]
    raw_edges = json.loads(edges_json or "[]")
    from app.schemas.task_graph import TaskGraphEdgeSpec

    edges = [TaskGraphEdgeSpec.model_validate(item) for item in raw_edges]
    return TaskGraphSpec(
        title=title,
        question=question,
        nodes=nodes,
        edges=edges,
    )


async def submit_task_graph(
    title: str,
    question: str,
    nodes_json: str,
    edges_json: str = "[]",
    max_steps_per_node: int = 12,
    timeout_seconds_per_node: int = 300,
    token_budget_per_node: int = 150000,
    tool_context: Any = None,
) -> dict:
    """提交一张任务图草稿，用户确认后按依赖拓扑执行，收尾自动汇总进对话。

    title: 图标题（短，概括任务）。
    question: 总目标：用户要解决的问题，随上下文传给每个节点。
    nodes_json: 节点 JSON 数组，每项 {"id","name","target","instruction","tools","depends_on"}：
      - id 是局部标识（字母开头的英文，如 a/b/extract），仅供 depends_on 引用，不展示给用户；
      - name 是展示名，必须是具体任务动作（如「查询最近24小时告警」「统计各级别日志条数」），
        禁止用 A/B/C、"并行节点"、"节点1" 这类 id 或拓扑角色名——用户看到的是 name，要能一眼看出这步干什么；
      - target 是本节点要排查/执行的目标；instruction 是具体要求；
      - tools 是本节点所需工具名数组（["__none__"] 表示不挂任何工具，留空挂默认只读集）；
      - depends_on 是前驱 id 数组：留空=与其它起点并行，多个=等全部前驱完成的汇合点。
      1..16 个节点。
    edges_json: 额外依赖边 JSON 数组 [{"source","target"}]，与 depends_on 合并；一般留空。
    max_steps_per_node: 每节点最大模型调用次数。
    timeout_seconds_per_node: 每节点墙钟超时秒数。
    token_budget_per_node: 每节点 token 软限，超限中止该节点。

    创建后图处于待确认草稿态，用户在任务图卡确认后执行；过程中各节点状态实时更新，
    全部收口后写一条带证据与图表的汇总进对话流。
    """

    from app.models.chat import ChatSession
    from app.services.taskgraph import task_graph_service

    session = getattr(tool_context, "session", None) if tool_context is not None else None
    session_id = getattr(session, "id", "") or ""
    if not session_id:
        return {"error": "缺少会话上下文，无法提交任务图"}
    if not title.strip():
        return {"error": "title 不能为空"}
    if not question.strip():
        return {"error": "question 不能为空"}
    try:
        spec = _parse_spec(nodes_json, edges_json, title.strip(), question.strip())
    except Exception as exc:  # noqa: BLE001 - JSON/pydantic 校验错误统一转 error 结果
        return {"error": f"任务图定义不合法: {exc}"}

    budget = WorkerBudget(
        max_steps=max(1, int(max_steps_per_node)),
        timeout_seconds=max(1, int(timeout_seconds_per_node)),
        token_budget=max(0, int(token_budget_per_node)),
    )
    async with SessionLocal() as db:
        session_row = await db.get(ChatSession, session_id)
        created_by = session_row.owner_user_id if session_row is not None else None
        graph = await task_graph_service.submit_graph(
            db,
            session_id=session_id,
            spec=spec,
            budget=budget,
            created_by=created_by,
        )
        graph_uid = graph.graph_uid

    logger.info(
        "submit_task_graph 创建任务图 graph_uid=%s title=%s nodes=%s",
        graph_uid,
        title,
        len(spec.nodes),
    )
    async with SessionLocal() as db:
        result = await task_graph_service.tool_result_wire(db, graph_uid)
    result["message"] = (
        "任务图已创建并等待用户确认；用户确认后按依赖拓扑执行，全部收口后自动汇总。"
    )
    return result


async def get_task_graph(graph_uid: str) -> dict:
    """按图标识取执行进度快照：各节点状态、结论、证据与用量。

    graph_uid: 任务图标识（submit_task_graph 返回或任务图卡参数里）。
    """

    if not graph_uid.strip():
        return {"error": "graph_uid 不能为空"}
    from app.services.taskgraph import task_graph_service

    async with SessionLocal() as db:
        wire = await task_graph_service.get_graph_wire(db, graph_uid.strip())
    if wire is None:
        return {"error": f"任务图不存在: {graph_uid}"}
    return wire


async def update_task_graph(
    graph_uid: str, nodes_json: str, edges_json: str = "[]"
) -> dict:
    """按用户意见调整任务图节点与依赖（整图替换）；仅草稿态可改。

    graph_uid: 任务图标识。
    nodes_json: 新的节点 JSON 数组，格式同 submit_task_graph。
    edges_json: 额外依赖边，格式同 submit_task_graph。
    """

    if not graph_uid.strip():
        return {"error": "graph_uid 不能为空"}
    from app.services.taskgraph import task_graph_service

    try:
        async with SessionLocal() as db:
            graph = await task_graph_service.get_graph_row(db, graph_uid.strip())
            if graph is None:
                return {"error": f"任务图不存在: {graph_uid}"}
            spec = _parse_spec(nodes_json, edges_json, graph.title, graph.question)
            ok = await task_graph_service.update_spec(db, graph_uid.strip(), spec)
            if not ok:
                return {"error": "任务图不存在或已开始执行，无法调整"}
            return await task_graph_service.tool_result_wire(db, graph_uid.strip())
    except Exception as exc:  # noqa: BLE001 - JSON/pydantic 校验错误统一转 error 结果
        return {"error": f"任务图定义不合法: {exc}"}
