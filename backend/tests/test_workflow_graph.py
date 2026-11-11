"""工作流画布校验、变量插值与 ADK 图执行。

覆盖范围：
  - 画布结构校验走真实 Pydantic 校验；
  - ADK 图可执行性走 google.adk.workflow.Workflow 的真实 validate_graph；
  - 运行路径用真实 SQLAlchemy 会话 + 真实 ADK Runner 跑通分支、循环、人工节点。
不覆盖：LLM 节点与 MCP 节点的外部调用（需要真实模型服务 / MCP Server）。
"""

from __future__ import annotations

import json
import uuid

import pytest
from pydantic import ValidationError

from app.adk_runtime.workflow_graph import validate
from app.schemas.workflow import (
    WorkflowDefinition,
    WorkflowVariable,
    graph_warnings,
    unknown_placeholders,
)
from app.services import workflow_vars


def _new_sessionmaker():
    """每个用例独立建 engine：模块级 engine 会跨事件循环复用连接而报错。"""

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _node(node_id: str, kind: str, **extra: object) -> dict:
    base = {"id": node_id, "kind": kind}
    base.update(extra)
    return base


def _simple_flow() -> dict:
    return {
        "nodes": [
            _node("start", "start", variables=[{"name": "topic", "required": True}]),
            _node("prep", "assign", assignments={"q": "关于 {{topic}} 的资料"}),
            _node("end", "end", output_key="q"),
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "prep"},
            {"id": "e2", "source": "prep", "target": "end"},
        ],
    }


# ---------------- 结构校验 ----------------


def test_simple_flow_validates_against_adk() -> None:
    definition = WorkflowDefinition.model_validate(_simple_flow())
    validate(definition)
    assert definition.starts() == ["prep"]


def test_missing_start_rejected() -> None:
    payload = _simple_flow()
    payload["nodes"] = [n for n in payload["nodes"] if n["kind"] != "start"]

    with pytest.raises(ValidationError) as exc:
        WorkflowDefinition.model_validate(payload)
    assert "开始节点" in str(exc.value)


def test_duplicate_node_id_rejected() -> None:
    payload = _simple_flow()
    payload["nodes"].append(_node("prep", "join"))

    with pytest.raises(ValidationError) as exc:
        WorkflowDefinition.model_validate(payload)
    assert "节点 ID 重复" in str(exc.value)


def test_start_accepts_common_id() -> None:
    """`start` 是用户常用 ID，不能被当成 ADK 保留名禁掉。"""

    definition = WorkflowDefinition.model_validate(_simple_flow())
    assert definition.require_start().id == "start"


def test_switch_requires_route_on_every_edge() -> None:
    payload = {
        "nodes": [
            _node("start", "start", variables=[{"name": "x"}]),
            _node("sw", "switch", branches=["a", "b"], expression="{{x}} == 1"),
            _node("na", "assign", assignments={"k": "a"}),
            _node("nb", "assign", assignments={"k": "b"}),
            _node("end", "end"),
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "sw"},
            {"id": "e2", "source": "sw", "target": "na", "route": "a"},
            {"id": "e3", "source": "sw", "target": "nb", "route": "b"},
            {"id": "e4", "source": "na", "target": "end"},
            {"id": "e5", "source": "nb", "target": "end"},
        ],
    }
    # 最初合法
    WorkflowDefinition.model_validate(payload)
    # 少一个分支出边必须报错
    payload["edges"] = [e for e in payload["edges"] if e["id"] != "e3"]
    with pytest.raises(ValidationError) as exc:
        WorkflowDefinition.model_validate(payload)
    assert "分支" in str(exc.value)


def test_switch_rejects_undeclared_route() -> None:
    payload = _simple_flow()
    payload["nodes"].insert(
        1, _node("sw", "switch", branches=["a", "b"], expression="{{topic}} == 1")
    )
    payload["edges"] = [
        {"id": "e1", "source": "start", "target": "sw"},
        {"id": "e2", "source": "sw", "target": "prep", "route": "zzz"},
        {"id": "e3", "source": "prep", "target": "end"},
    ]
    with pytest.raises(ValidationError) as exc:
        WorkflowDefinition.model_validate(payload)
    assert "未在 branches 声明" in str(exc.value)


def test_http_node_rejects_bad_scheme_at_run_time_not_schema() -> None:
    """schema 只校验非空；协议白名单在执行时判断。"""

    payload = _simple_flow()
    payload["nodes"].insert(1, _node("fetch", "http", url="ftp://example.com"))
    payload["edges"] = [
        {"id": "e1", "source": "start", "target": "fetch"},
        {"id": "e2", "source": "fetch", "target": "prep"},
        {"id": "e3", "source": "prep", "target": "end"},
    ]
    WorkflowDefinition.model_validate(payload)


def test_end_node_cannot_have_outgoing() -> None:
    payload = _simple_flow()
    payload["edges"].append({"id": "e3", "source": "end", "target": "prep"})
    with pytest.raises(ValidationError) as exc:
        WorkflowDefinition.model_validate(payload)
    assert "结束节点" in str(exc.value)


def test_unreachable_node_rejected() -> None:
    payload = _simple_flow()
    payload["nodes"].append(_node("lost", "join"))
    payload["edges"].append({"id": "e3", "source": "lost", "target": "end"})
    with pytest.raises(ValidationError) as exc:
        WorkflowDefinition.model_validate(payload)
    assert "不可达" in str(exc.value)


def test_unknown_placeholder_reported_as_warning() -> None:
    payload = _simple_flow()
    payload["nodes"][1]["assignments"] = {"q": "{{nope}}"}
    definition = WorkflowDefinition.model_validate(payload)
    assert unknown_placeholders(definition) == ["nope"]
    assert any("nope" in w for w in graph_warnings(definition))


def test_switch_branch_merge_is_not_warned() -> None:
    """同一个条件分支的不同 route 汇合到同一节点，不会重复执行，不该提示。"""

    payload = {
        "nodes": [
            _node("start", "start", variables=[{"name": "x"}]),
            _node("sw", "switch", branches=["a", "b"], expression="{{x}} == 1"),
            _node("na", "assign", assignments={"k": "a"}),
            _node("nb", "assign", assignments={"k": "b"}),
            # 两条分支各经过一个中间节点后在 tail 汇合
            _node("mid", "assign", assignments={"m": "via-a"}),
            _node("tail", "assign", assignments={"z": "tail"}),
            _node("end", "end", output_key="z"),
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "sw"},
            {"id": "e2", "source": "sw", "target": "na", "route": "a"},
            {"id": "e3", "source": "sw", "target": "nb", "route": "b"},
            {"id": "e4", "source": "na", "target": "mid"},
            {"id": "e5", "source": "mid", "target": "tail"},
            {"id": "e6", "source": "nb", "target": "tail"},
            {"id": "e7", "source": "tail", "target": "end"},
        ],
    }
    definition = WorkflowDefinition.model_validate(payload)
    assert graph_warnings(definition) == []


def test_parallel_fan_in_still_warned() -> None:
    """真正的并行多入边会按每条边各执行一次，必须继续提示。"""

    payload = {
        "nodes": [
            _node("start", "start", variables=[{"name": "x"}]),
            _node("left", "assign", assignments={"a": "1"}),
            _node("right", "assign", assignments={"b": "2"}),
            _node("tail", "assign", assignments={"z": "tail"}),
            _node("end", "end", output_key="z"),
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "left"},
            {"id": "e2", "source": "start", "target": "right"},
            {"id": "e3", "source": "left", "target": "tail"},
            {"id": "e4", "source": "right", "target": "tail"},
            {"id": "e5", "source": "tail", "target": "end"},
        ],
    }
    definition = WorkflowDefinition.model_validate(payload)
    warnings = graph_warnings(definition)
    assert any("2 条入边" in w and "tail" in w for w in warnings), warnings


def test_required_variable_rejects_default() -> None:
    """必填 = 运行时必须由用户提供，因此不能同时有默认值。"""

    with pytest.raises(ValidationError) as exc:
        WorkflowVariable.model_validate(
            {"name": "text", "type": "string", "required": True, "default": "x"}
        )
    assert "必填" in str(exc.value)


def test_optional_variable_accepts_default() -> None:
    variable = WorkflowVariable.model_validate(
        {"name": "n", "type": "number", "required": False, "default": 5}
    )
    assert variable.default == 5


def test_default_type_mismatch_rejected() -> None:
    """默认值类型不匹配必须拒绝，不能悄悄转换。"""

    with pytest.raises(ValidationError) as exc:
        WorkflowVariable.model_validate(
            {"name": "n", "type": "number", "default": "5"}
        )
    assert "数字" in str(exc.value)

    # bool 是 int 子类，不能被当成数字
    with pytest.raises(ValidationError):
        WorkflowVariable.model_validate(
            {"name": "n", "type": "number", "default": True}
        )


def test_initial_state_uses_default_for_optional_only() -> None:
    """非必填没传就用默认值；必填没传则报错。"""

    from app.services.workflow_service import initial_state_of

    payload = _simple_flow()
    payload["nodes"][0]["variables"] = [
        {"name": "topic", "type": "string", "required": True},
        {"name": "limit", "type": "number", "required": False, "default": 7},
        {"name": "note", "type": "string", "required": False},
    ]
    definition = WorkflowDefinition.model_validate(payload)

    state = initial_state_of(definition, {"topic": "t"})
    assert state == {"topic": "t", "limit": 7}

    # 显式传值优先于默认值
    assert initial_state_of(definition, {"topic": "t", "limit": 2})["limit"] == 2

    with pytest.raises(ValueError) as exc:
        initial_state_of(definition, {})
    assert "topic" in str(exc.value)


def test_number_default_keeps_integer() -> None:
    """默认 3 不能变成 3.0，否则插值文案会显示成 "3.0"。"""

    from app.services.workflow_service import initial_state_of

    payload = _simple_flow()
    payload["nodes"][0]["variables"] = [
        {"name": "topic", "type": "string", "required": True},
        {"name": "limit", "type": "number", "required": False, "default": 3},
    ]
    definition = WorkflowDefinition.model_validate(payload)
    state = initial_state_of(definition, {"topic": "t"})
    assert state["limit"] == 3
    assert isinstance(state["limit"], int)
    assert f"{state['limit']}" == "3"


def test_boolean_variable_rejects_loose_text() -> None:
    """布尔变量只接受真正的 bool，不做 "1"/"yes" 这类猜测。"""

    from app.services.workflow_service import initial_state_of

    payload = _simple_flow()
    payload["nodes"][0]["variables"] = [
        {"name": "topic", "type": "string", "required": True},
        {"name": "force", "type": "boolean", "required": False},
    ]
    definition = WorkflowDefinition.model_validate(payload)
    assert initial_state_of(definition, {"topic": "t", "force": False})["force"] is False
    with pytest.raises(ValueError) as exc:
        initial_state_of(definition, {"topic": "t", "force": "yes"})
    assert "布尔" in str(exc.value)


# ---------------- 变量插值 ----------------


def test_render_keeps_type_for_whole_placeholder() -> None:
    state = {"items": [1, 2], "n": 3}
    assert workflow_vars.render_value("{{items}}", state) == [1, 2]
    assert workflow_vars.render_value("n={{n}}", state) == "n=3"


def test_render_missing_variable_raises() -> None:
    with pytest.raises(workflow_vars.MissingVariableError):
        workflow_vars.render("{{absent}}", {})


def test_conditional_operators() -> None:
    state = {"n": 5, "s": "abc"}
    assert workflow_vars.resolve_conditional("{{n}} > 4", state) is True
    assert workflow_vars.resolve_conditional("{{n}} <= 4", state) is False
    assert workflow_vars.resolve_conditional("{{s}} == abc", state) is True
    assert workflow_vars.resolve_conditional("{{s}} != abc", state) is False
    with pytest.raises(ValueError):
        workflow_vars.resolve_conditional("{{s}} > 1", state)


# ---------------- 节点输入范围 ----------------


def _built_node(node_payload: dict, definition: WorkflowDefinition, tools: dict | None = None) -> object:
    """按 kind 构造真实节点实例：输入快照必须由节点自己给出，不能靠猜配置。"""

    from app.adk_runtime.workflow_node_impl import NODE_BUILDERS
    from app.adk_runtime.workflow_nodes import RunContext

    node = next(n for n in definition.nodes if n.id == node_payload["id"])
    builder = NODE_BUILDERS[node.kind]
    run_ctx = RunContext(definition=definition, db=None)  # pyright: ignore[reportArgumentType]
    return builder.build(node=node, run_ctx=run_ctx, tools=tools or {}, name=node.id)


def _input_of(
    node_payload: dict,
    state: dict,
    tools: dict | None = None,
    extra_nodes: list[dict] | None = None,
    extra_edges: list[dict] | None = None,
) -> dict:
    definition = WorkflowDefinition.model_validate(
        {
            "nodes": [
                _node("start", "start", variables=[{"name": "topic", "required": False}]),
                node_payload,
                *(extra_nodes or []),
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": node_payload["id"]},
                *(extra_edges or []),
            ],
        }
    )
    built = _built_node(node_payload, definition, tools)
    return built.input_snapshot(state)  # type: ignore[attr-defined]


def test_start_node_input_is_submitted_values() -> None:
    definition = WorkflowDefinition.model_validate(_simple_flow())
    built = _built_node({"id": "start", "kind": "start"}, definition, {"run_values": {"topic": "巡检"}})
    # 开始节点的输入就是这一轮提交的参数，人工节点回填的值同样算在内
    assert built.input_snapshot({}) == {"topic": "巡检"}  # type: ignore[attr-defined]


def test_node_input_is_what_the_node_reads() -> None:
    """每个节点类型都给出输入，且只列自己真正读到的东西。"""

    state = {"topic": "磁盘告警", "score": 88, "noise": "噪音"}

    assert _input_of(_node("kb", "knowledge", query="{{topic}} 手册"), state) == {
        "topic": "磁盘告警"
    }
    # 固定查询串没读任何变量，输入就是这段查询
    assert _input_of(_node("kb2", "knowledge", query="固定查询"), state) == {
        "query": "固定查询"
    }
    assert _input_of(_node("search", "search", query="{{topic}} 公告"), state) == {
        "topic": "磁盘告警"
    }
    assert _input_of(
        _node("prep", "assign", assignments={"subject": "{{topic}} 的处理"}), state
    ) == {"topic": "磁盘告警"}
    assert _input_of(
        _node("sw", "switch", branches=["hi", "lo"], expression="{{score}} >= 80"),
        state,
        extra_nodes=[
            _node("sw_hi", "assign", assignments={"r": "hi"}),
            _node("sw_lo", "assign", assignments={"r": "lo"}),
            _node("sw_end", "end", output_key="r"),
        ],
        extra_edges=[
            {"id": "e2", "source": "sw", "target": "sw_hi", "route": "hi"},
            {"id": "e3", "source": "sw", "target": "sw_lo", "route": "lo"},
            {"id": "e4", "source": "sw_hi", "target": "sw_end"},
            {"id": "e5", "source": "sw_lo", "target": "sw_end"},
        ],
    ) == {"expression": "{{score}} >= 80", "score": 88}
    assert _input_of(
        _node("lp", "loop", max_iterations=3, expression="{{score}} < 100"),
        state,
        extra_nodes=[
            _node("lp_body", "assign", assignments={"r": "x"}),
            _node("lp_end", "end", output_key="r"),
        ],
        extra_edges=[
            {"id": "e2", "source": "lp", "target": "lp_body", "route": "loop"},
            {"id": "e3", "source": "lp_body", "target": "lp"},
            {"id": "e4", "source": "lp", "target": "lp_end", "route": "exit"},
        ],
    ) == {"iteration": 1, "max_iterations": 3, "score": 88}
    assert _input_of(_node("gate", "approval", prompt="确认 {{topic}}"), state) == {
        "topic": "磁盘告警"
    }
    assert _input_of(
        _node("http", "http", method="POST", url="http://ops/{{topic}}"), state
    ) == {"method": "POST", "topic": "磁盘告警"}
    assert _input_of(
        _node("mcp", "mcp", mcp_server="ops", mcp_tool="restart", arguments={"q": "{{topic}}"}),
        state,
    ) == {"server": "ops", "tool": "restart", "topic": "磁盘告警"}
    assert _input_of(_node("end", "end", output_key="score"), state) == {"score": 88}


def test_join_node_input_is_predecessor_outputs() -> None:
    """汇合点只列各入边带过来的产出，不把整份状态倒出来。"""

    payload = {
        "nodes": [
            _node("start", "start", variables=[{"name": "topic"}]),
            _node("a", "assign", output_key="from_a", assignments={"from_a": "A"}),
            _node("b", "assign", output_key="from_b", assignments={"from_b": "B"}),
            _node("join", "join"),
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "a"},
            {"id": "e2", "source": "start", "target": "b"},
            {"id": "e3", "source": "a", "target": "join"},
            {"id": "e4", "source": "b", "target": "join"},
        ],
    }
    definition = WorkflowDefinition.model_validate(payload)
    built = _built_node({"id": "join", "kind": "join"}, definition)
    assert built.input_snapshot({"from_a": "A", "from_b": "B", "noise": "x"}) == {  # type: ignore[attr-defined]
        "from_a": "A",
        "from_b": "B",
    }


@pytest.mark.asyncio
async def test_code_node_input_is_measured_at_runtime() -> None:
    """代码节点读哪些键是跑出来的，不是扫代码猜出来的。"""

    payload = {
        "nodes": [
            _node("start", "start", variables=[{"name": "topic"}]),
            _node(
                "score",
                "code",
                output_key="score",
                code="def run(state):\n    return str(state['kb']) + str(state.get('web', ''))",
            ),
            _node("end", "end", output_key="score"),
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "score"},
            {"id": "e2", "source": "score", "target": "end"},
        ],
    }
    definition = WorkflowDefinition.model_validate(payload)
    built = _built_node({"id": "score", "kind": "code"}, definition)
    state = {"kb": "知识", "web": "公告", "noise": "噪音"}
    await built.execute(ctx=None, state=state)  # type: ignore[attr-defined]
    assert built.input_snapshot(state) == {"kb": "知识", "web": "公告"}  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_code_node_input_keeps_all_keys_it_actually_copies() -> None:
    """整份复制 / 遍历也是读取：如实给全量，而不是空着。"""

    for code in (
        "def run(state):\n    data = dict(state)\n    return data['kb']",
        "def run(state):\n    return '|'.join(str(v) for v in state.values())",
        "def run(state):\n    return state.copy()['kb']",
    ):
        payload = {
            "nodes": [
                _node("start", "start", variables=[{"name": "topic"}]),
                _node("all", "code", output_key="out", code=code),
                _node("end", "end", output_key="out"),
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "all"},
                {"id": "e2", "source": "all", "target": "end"},
            ],
        }
        definition = WorkflowDefinition.model_validate(payload)
        built = _built_node({"id": "all", "kind": "code"}, definition)
        state = {"kb": "知识", "web": "公告"}
        await built.execute(ctx=None, state=state)  # type: ignore[attr-defined]
        assert built.input_snapshot(state) == state  # type: ignore[attr-defined]


def test_preview_input_keeps_variables_and_valid_json() -> None:
    """输入落库不能把整段 JSON 截断：键要留着，前端才能按变量逐行展示。"""

    from app.adk_runtime.workflow_nodes import preview_input

    raw = preview_input(
        {
            "kb": {"items": [{"excerpt": "x" * 900}]},
            "subject": "磁盘告警",
            "__approved_gates": ["gate"],
        }
    )
    parsed = json.loads(raw)
    # 内部记账键不该出现在面板里
    assert list(parsed) == ["kb", "subject"]
    assert parsed["subject"] == "磁盘告警"
    excerpt = parsed["kb"]["items"][0]["excerpt"]
    assert excerpt.endswith("…")
    assert len(excerpt) <= 601


# ---------------- 真实执行 ----------------


async def _run(definition_payload: dict, values: dict) -> tuple[object, list, object]:
    from app.models_orm.workflow import Workflow as WorkflowRow
    from app.services import workflow_service

    definition = WorkflowDefinition.model_validate(definition_payload)
    engine, sessionmaker = _new_sessionmaker()
    name = f"__test_wf_{uuid.uuid4().hex[:8]}__"
    async with sessionmaker() as db:
        row = WorkflowRow(
            name=name, description="", definition_json=definition.model_dump_json()
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        try:
            run = await workflow_service.execute_run(
                db, workflow=row, payload=values, triggered_by="test@local"
            )
            nodes = await workflow_service.list_node_runs(db, run.id)
            return run, nodes, db
        finally:
            await db.delete(row)
            await db.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_run_assign_and_end() -> None:
    run, nodes, _db = await _run(_simple_flow(), {"topic": "巡检"})
    assert run.status == "succeeded", run.error
    assert "巡检" in run.output_json
    assert [n.node_id for n in nodes] == ["start", "prep", "end"]


@pytest.mark.asyncio
async def test_node_runs_store_only_used_inputs() -> None:
    """落库的输入是「这一步读了什么」，不是整份共享状态。"""

    import json

    payload = {
        "nodes": [
            _node("start", "start", variables=[{"name": "topic", "required": True}]),
            _node("prep", "assign", assignments={"subject": "{{topic}} 处理"}),
            _node(
                "score",
                "code",
                output_key="score",
                code="def run(state):\n    return len(str(state.get('subject', '')))",
            ),
            _node("end", "end", output_key="score"),
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "prep"},
            {"id": "e2", "source": "prep", "target": "score"},
            {"id": "e3", "source": "score", "target": "end"},
        ],
    }
    run, nodes, _db = await _run(payload, {"topic": "磁盘告警"})
    assert run.status == "succeeded", run.error
    inputs = {n.node_id: json.loads(n.input_json) for n in nodes}
    assert inputs["start"] == {"topic": "磁盘告警"}
    assert inputs["prep"] == {"topic": "磁盘告警"}
    # 代码节点只读了 subject：topic 不该跟着混进它的输入
    assert inputs["score"] == {"subject": "磁盘告警 处理"}
    assert inputs["end"] == {"score": 7}


@pytest.mark.asyncio
async def test_run_switch_takes_declared_branch() -> None:
    payload = {
        "nodes": [
            _node("start", "start", variables=[{"name": "mode", "required": True}]),
            _node("sw", "switch", branches=["yes", "no"], expression="{{mode}} == hit"),
            _node("a", "assign", assignments={"r": "hit-branch"}),
            _node("b", "assign", assignments={"r": "miss-branch"}),
            _node("end", "end", output_key="r"),
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "sw"},
            {"id": "e2", "source": "sw", "target": "a", "route": "yes"},
            {"id": "e3", "source": "sw", "target": "b", "route": "no"},
            {"id": "e4", "source": "a", "target": "end"},
            {"id": "e5", "source": "b", "target": "end"},
        ],
    }
    run, nodes, _db = await _run(payload, {"mode": "hit"})
    assert run.status == "succeeded", run.error
    assert "hit-branch" in run.output_json
    routes = {n.node_id: n.route for n in nodes}
    assert routes["sw"] == "yes"


@pytest.mark.asyncio
async def test_run_loop_stops_on_condition() -> None:
    payload = {
        "nodes": [
            _node("start", "start", variables=[{"name": "x", "type": "number", "required": True}]),
            _node(
                "inc",
                "code",
                output_key="x",
                code="def run(state):\n    return int(state.get('x', 0)) + 1",
            ),
            _node("loopn", "loop", max_iterations=6, expression="{{x}} < 3"),
            _node("end", "end", output_key="x"),
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "loopn"},
            {"id": "e2", "source": "loopn", "target": "inc", "route": "loop"},
            {"id": "e3", "source": "inc", "target": "loopn"},
            {"id": "e4", "source": "loopn", "target": "end", "route": "exit"},
        ],
    }
    run, nodes, _db = await _run(payload, {"x": 0})
    assert run.status == "succeeded", run.error
    assert run.output_json == "3"
    loop_routes = [n.route for n in nodes if n.node_id == "loopn"]
    assert loop_routes[-1] == "exit"


@pytest.mark.asyncio
async def test_run_waits_at_approval_then_resumes() -> None:
    from app.services import workflow_service

    payload = {
        "nodes": [
            _node("start", "start", variables=[{"name": "x", "required": True}]),
            _node("gate", "approval", prompt="确认 {{x}}"),
            _node("after", "assign", assignments={"y": "post-{{x}}"}),
            _node("end", "end", output_key="y"),
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "gate"},
            {"id": "e2", "source": "gate", "target": "after"},
            {"id": "e3", "source": "after", "target": "end"},
        ],
    }
    definition = WorkflowDefinition.model_validate(payload)
    engine, sessionmaker = _new_sessionmaker()
    async with sessionmaker() as db:
        from app.models_orm.workflow import Workflow as WorkflowRow

        row = WorkflowRow(
            name=f"__test_wf_{uuid.uuid4().hex[:8]}__",
            description="",
            definition_json=definition.model_dump_json(),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        try:
            run = await workflow_service.execute_run(
                db, workflow=row, payload={"x": "v1"}, triggered_by="test@local"
            )
            assert run.status == "waiting"
            # 停在人工节点：文案里的变量要渲染，且下游不得执行
            assert "v1" in run.output_json
            before = {n.node_id for n in await workflow_service.list_node_runs(db, run.id)}
            assert "after" not in before

            resumed = await workflow_service.resume_run(
                db, run=run, decision="approve", payload={}
            )
            assert resumed.status == "succeeded", resumed.error
            assert "post-v1" in resumed.output_json
            after = [n.node_id for n in await workflow_service.list_node_runs(db, run.id)]
            # 恢复后节点表不重复
            assert after == ["start", "gate", "after", "end"]
        finally:
            await db.delete(row)
            await db.commit()


@pytest.mark.asyncio
async def test_missing_required_variable_fails_before_run() -> None:
    from app.services import workflow_service

    definition = WorkflowDefinition.model_validate(_simple_flow())
    with pytest.raises(ValueError) as exc:
        workflow_service.initial_state_of(definition, {})
    assert "topic" in str(exc.value)


# ---------------- 单节点试跑 ----------------


async def _probe(definition_payload: dict, node_id: str, values: dict) -> dict:
    from app.models_orm.workflow import Workflow as WorkflowRow
    from app.services import workflow_service

    definition = WorkflowDefinition.model_validate(definition_payload)
    engine, sessionmaker = _new_sessionmaker()
    async with sessionmaker() as db:
        row = WorkflowRow(
            name=f"__test_wf_{uuid.uuid4().hex[:8]}__",
            description="",
            definition_json=definition.model_dump_json(),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        try:
            return await workflow_service.run_single_node(
                db,
                workflow=row,
                definition=definition,
                node_id=node_id,
                payload=values,
                triggered_by="test@local",
            )
        finally:
            await db.delete(row)
            await db.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_probe_runs_upstream_and_stops_there() -> None:
    """试跑 prep：上游 start 真实执行，下游 end 不执行。"""

    result = await _probe(_simple_flow(), "prep", {"topic": "巡检"})
    assert result["status"] == "succeeded", result["error"]
    # assign 节点的结果按 output_key 写回，输出里必须是渲染后的真实变量值
    assert "巡检" in result["output"]


@pytest.mark.asyncio
async def test_probe_reports_target_failure() -> None:
    payload = {
        "nodes": [
            _node("start", "start", variables=[{"name": "x", "required": True}]),
            _node(
                "boom",
                "code",
                code="def run(state):\n    raise RuntimeError('节点内部炸了')",
            ),
            _node("end", "end", output_key="x"),
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "boom"},
            {"id": "e2", "source": "boom", "target": "end"},
        ],
    }
    result = await _probe(payload, "boom", {"x": "v"})
    assert result["status"] == "failed"
    assert "节点内部炸了" in result["error"]


@pytest.mark.asyncio
async def test_probe_rejects_human_node() -> None:
    payload = {
        "nodes": [
            _node("start", "start", variables=[{"name": "x", "required": True}]),
            _node("gate", "approval", prompt="确认 {{x}}"),
        ],
        "edges": [{"id": "e1", "source": "start", "target": "gate"}],
    }
    with pytest.raises(ValueError) as exc:
        await _probe(payload, "gate", {"x": "v"})
    assert "人工节点" in str(exc.value)


# ---------------- 并行汇合 ----------------


@pytest.mark.asyncio
async def test_join_waits_for_all_parallel_branches() -> None:
    """汇合点必须等所有入边跑完，否则慢的那支会被后续中止直接取消。"""

    payload = {
        "nodes": [
            _node("start", "start", variables=[{"name": "x", "required": True}]),
            _node("left", "assign", output_key="left", assignments={"a": "左-{{x}}"}),
            _node("right", "assign", output_key="right", assignments={"b": "右-{{x}}"}),
            _node("both", "join", output_key="both"),
            _node("end", "end", output_key="a"),
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "left"},
            {"id": "e2", "source": "start", "target": "right"},
            {"id": "e3", "source": "left", "target": "both"},
            {"id": "e4", "source": "right", "target": "both"},
            {"id": "e5", "source": "both", "target": "end"},
        ],
    }
    run, nodes, _db = await _run(payload, {"x": "v"})
    assert run.status == "succeeded", run.error

    joins = [n for n in nodes if n.node_id == "both"]
    assert len(joins) == 1, [n.node_id for n in nodes]
    # 汇合时两支都已跑完：聚合到的状态里两个分支的产出都在
    assert "左-v" in joins[0].output_json
    assert "右-v" in joins[0].output_json
    assert [n.node_id for n in nodes].count("end") == 1


# ---------------- LLM 节点写回 ----------------


def _agent_definition() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "nodes": [
                _node("start", "start", variables=[{"name": "x", "required": True}]),
                _node(
                    "plan",
                    "agent",
                    output_key="plan",
                    provider="deepseek",
                    model="deepseek-flash",
                    instruction="基于 {{x}} 出处置方案",
                ),
                _node("end", "end", output_key="plan"),
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "plan"},
                {"id": "e2", "source": "plan", "target": "end"},
            ],
        }
    )


class _FakeState:
    """ADK 的 State：赋值走 __setitem__，取全量快照走 to_dict()。"""

    def __init__(self, initial: dict[str, object] | None = None) -> None:
        self._data: dict[str, object] = {} if initial is None else dict(initial)

    def __getitem__(self, key: str) -> object:
        return self._data[key]

    def __setitem__(self, key: str, value: object) -> None:
        self._data[key] = value

    def to_dict(self) -> dict[str, object]:
        return dict(self._data)


@pytest.mark.asyncio
async def test_llm_node_callback_writes_output_key() -> None:
    """agent 节点不是画布节点，output_key 的写回靠模型回调补上。"""

    from types import SimpleNamespace
    from typing import Any, cast

    from app.adk_runtime.workflow_nodes import RunContext, build_llm_callbacks

    definition = _agent_definition()
    node = next(n for n in definition.nodes if n.id == "plan")
    run_ctx = RunContext(definition=definition, db=cast(Any, None))
    before, after_model, on_error = build_llm_callbacks(node=node, run_ctx=run_ctx)

    state = _FakeState({"x": "CPU 打满"})
    callback_context = SimpleNamespace(state=state)
    await before(callback_context)
    response = SimpleNamespace(
        content=SimpleNamespace(
            parts=[
                SimpleNamespace(text="这段是思考过程", thought=True),
                SimpleNamespace(text="1. 摘流量\n2. 重启"),
            ]
        )
    )
    await after_model(callback_context, response)

    # 只写正文：思考过程既不进输出变量，也不传给下游
    assert state["plan"] == "1. 摘流量\n2. 重启"
    assert len(run_ctx.outcomes) == 1
    outcome = run_ctx.outcomes[0]
    assert (outcome.node_id, outcome.node_kind, outcome.status) == (
        "plan",
        "agent",
        "succeeded",
    )
    # 输入只记指令里引用到的变量，不是整份状态
    assert outcome.node_input == {"x": "CPU 打满"}
    assert outcome.output == "1. 摘流量\n2. 重启"
    assert outcome.attempt == 1

    # 模型报错也要留一条失败记录，而不是整步凭空消失
    await before(callback_context)
    await on_error(callback_context, None, RuntimeError("上游 500"))
    assert run_ctx.outcomes[-1].status == "failed"
    assert "上游 500" in run_ctx.outcomes[-1].error
    assert run_ctx.outcomes[-1].attempt == 2


@pytest.mark.asyncio
async def test_llm_callbacks_tolerate_empty_response() -> None:
    """上游没有正文时写回空串，不能写进 None 让下游插值炸掉。"""

    from types import SimpleNamespace
    from typing import Any, cast

    from app.adk_runtime.workflow_nodes import RunContext, build_llm_callbacks

    definition = _agent_definition()
    node = next(n for n in definition.nodes if n.id == "plan")
    run_ctx = RunContext(definition=definition, db=cast(Any, None))
    before, after_model, _on_error = build_llm_callbacks(node=node, run_ctx=run_ctx)

    state = _FakeState({"x": "CPU 打满"})
    callback_context = SimpleNamespace(state=state)
    await before(callback_context)
    await after_model(callback_context, SimpleNamespace(content=None))

    assert state["plan"] == ""
    assert run_ctx.outcomes[0].output == ""


# ---------------- 多次人工恢复 ----------------


@pytest.mark.asyncio
async def test_resume_twice_keeps_earlier_gate_and_input() -> None:
    """连着两次人工恢复：第一次的审批记录与第二次的输入都不能丢。"""

    from app.models_orm.workflow import Workflow as WorkflowRow
    from app.services import workflow_service

    payload = {
        "nodes": [
            _node("start", "start", variables=[{"name": "x", "required": True}]),
            _node("gate", "approval", prompt="确认 {{x}}"),
            _node("note", "input", prompt="补充处置意见", output_key="note"),
            _node("end", "end", output_key="note"),
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "gate"},
            {"id": "e2", "source": "gate", "target": "note"},
            {"id": "e3", "source": "note", "target": "end"},
        ],
    }
    definition = WorkflowDefinition.model_validate(payload)
    engine, sessionmaker = _new_sessionmaker()
    async with sessionmaker() as db:
        row = WorkflowRow(
            name=f"__test_wf_{uuid.uuid4().hex[:8]}__",
            description="",
            definition_json=definition.model_dump_json(),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        try:
            run = await workflow_service.execute_run(
                db, workflow=row, payload={"x": "v1"}, triggered_by="test@local"
            )
            assert run.status == "waiting"
            assert '"gate": "gate"' in run.output_json

            run = await workflow_service.resume_run(
                db, run=run, decision="approve", payload={}
            )
            # 第一次审批通过后停在人工输入；第一次的审批不能要求重批
            assert run.status == "waiting", run.error
            assert '"gate": "note"' in run.output_json

            run = await workflow_service.resume_run(
                db, run=run, decision="approve", payload={"note": "先摘流量"}
            )
            assert run.status == "succeeded", run.error
            # 人工输入的值要真的带着走完整条链路，而不是被透传节点覆盖
            assert "先摘流量" in run.output_json

            nodes = [n.node_id for n in await workflow_service.list_node_runs(db, run.id)]
            assert nodes == ["start", "gate", "note", "end"]
        finally:
            await db.delete(row)
            await db.commit()


@pytest.mark.asyncio
async def test_passthrough_keeps_submitted_input_value() -> None:
    """已确认的人工节点透传时，不能用自己的占位结果覆盖用户提交的值。"""

    from types import SimpleNamespace
    from typing import Any, cast

    from app.adk_runtime.workflow_nodes import PassThroughNode, RunContext

    definition = WorkflowDefinition.model_validate(
        {
            "nodes": [
                _node("start", "start", variables=[{"name": "x"}]),
                _node("note", "input", prompt="补充意见 {{x}}", output_key="note"),
                _node("end", "end", output_key="note"),
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "note"},
                {"id": "e2", "source": "note", "target": "end"},
            ],
        }
    )
    run_ctx = RunContext(definition=definition, db=cast(Any, None))
    input_node = next(n for n in definition.nodes if n.id == "note")
    passthrough = PassThroughNode.build(
        node=input_node, run_ctx=run_ctx, name=input_node.id
    )
    ctx = SimpleNamespace(state={})

    keep = await passthrough.execute(ctx=ctx, state={"note": "先摘流量"})
    assert keep == "先摘流量"

    # 没有提交值时退回占位结果，保持原有语义
    placeholder = await passthrough.execute(ctx=ctx, state={})
    assert placeholder == {"gate": "note", "approved": True}
