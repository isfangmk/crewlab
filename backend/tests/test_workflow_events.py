"""运行事件总线与「发起即返回」。

覆盖范围：
  - 事件总线：补发已发生事件、多订阅者都能收到、终止后标记完成；
    底层是 Redis Stream（多 worker 跨进程），公开接口 publish/subscribe/
    unsubscribe/is_finished/drop 不变；每个用例用独立 uid + 结束清理。
  - 发起运行：接口立刻返回 run_uid，运行记录当场是 running；
  - 订阅：运行已结束时补完历史就收尾，不留长连接。
不覆盖：真实画布执行（由 test_workflow_graph / test_workflow_run_state 覆盖）。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.services.workflow.workflow_events import WorkflowEventBus


async def _drain(queue: asyncio.Queue, timeout: float = 1.0) -> list[dict | None]:
    """ drains 队列直到拿到 None（收尾）或超时；返回收到的事件。"""

    out: list[dict | None] = []
    while True:
        try:
            out.append(await asyncio.wait_for(queue.get(), timeout=timeout))
        except asyncio.TimeoutError:
            break
        if out[-1] is None:
            break
    return out


@pytest.mark.asyncio
async def test_subscribe_replays_events_already_published() -> None:
    """订阅方比执行晚到也不能缺步骤：连上先补发。"""

    bus = WorkflowEventBus()
    uid = f"run_replay_{uuid.uuid4().hex[:8]}"
    try:
        publish = bus.open(uid)
        await publish({"event": "run_started", "run_uid": uid})
        await publish({"event": "node_started", "node_id": "a"})

        backlog, queue = await bus.subscribe(uid)
        assert [e["event"] for e in backlog] == ["run_started", "node_started"]
        bus.unsubscribe(uid, queue)
    finally:
        await bus.drop(uid)


@pytest.mark.asyncio
async def test_two_subscribers_both_receive_later_events() -> None:
    """发起方与查看方各连各的流，事件要都能收到（独立游标，不互相抢）。"""

    bus = WorkflowEventBus()
    uid = f"run_multi_{uuid.uuid4().hex[:8]}"
    try:
        publish = bus.open(uid)
        _b1, q1 = await bus.subscribe(uid)
        _b2, q2 = await bus.subscribe(uid)

        await publish({"event": "node_finished", "node_id": "a", "status": "succeeded"})

        e1 = await asyncio.wait_for(q1.get(), timeout=2.0)
        e2 = await asyncio.wait_for(q2.get(), timeout=2.0)
        assert e1["node_id"] == "a"
        assert e2["node_id"] == "a"
        bus.unsubscribe(uid, q1)
        bus.unsubscribe(uid, q2)
    finally:
        await bus.drop(uid)


@pytest.mark.asyncio
async def test_channel_marks_finished_on_terminal_event() -> None:
    bus = WorkflowEventBus()
    uid = f"run_finish_{uuid.uuid4().hex[:8]}"
    try:
        assert bus.is_finished(uid) is False
        publish = bus.open(uid)
        await publish({"event": "run_finished", "status": "succeeded"})
        assert bus.is_finished(uid) is True
    finally:
        await bus.drop(uid)


@pytest.mark.asyncio
async def test_subscribe_after_finish_returns_full_history() -> None:
    """运行早就结束才来订阅：补完历史即可，不必等长连接。"""

    bus = WorkflowEventBus()
    uid = f"run_done_{uuid.uuid4().hex[:8]}"
    try:
        publish = bus.open(uid)
        await publish({"event": "run_started", "run_uid": uid})
        await publish({"event": "run_finished", "status": "succeeded"})

        backlog, queue = await bus.subscribe(uid)
        assert [e["event"] for e in backlog] == ["run_started", "run_finished"]
        assert bus.is_finished(uid) is True
        bus.unsubscribe(uid, queue)
    finally:
        await bus.drop(uid)


@pytest.mark.asyncio
async def test_close_wakes_up_subscribers() -> None:
    """运行结束时订阅方要能收到收尾信号，否则详情页一直挂着。"""

    bus = WorkflowEventBus()
    uid = f"run_close_{uuid.uuid4().hex[:8]}"
    try:
        _backlog, queue = await bus.subscribe(uid)
        publish = bus.open(uid)
        await publish({"event": "run_finished", "status": "succeeded"})

        received = await _drain(queue)
        assert received[-1] is None, "终态后读循环应推 None 收尾"
        bus.unsubscribe(uid, queue)
    finally:
        await bus.drop(uid)


@pytest.mark.asyncio
async def test_waiting_does_not_close_the_channel() -> None:
    """停在人工节点只是暂停：通道要留着，续跑事件才能进同一条流。"""

    bus = WorkflowEventBus()
    uid = f"run_gate_{uuid.uuid4().hex[:8]}"
    try:
        _backlog, queue = await bus.subscribe(uid)
        publish = bus.open(uid)

        await publish({"event": "run_waiting", "gate": "g1"})

        first = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert first["event"] == "run_waiting"
        assert bus.is_finished(uid) is False
        # 没有终态：通道保持打开，不推 None
        assert queue.empty()
        bus.unsubscribe(uid, queue)
    finally:
        await bus.drop(uid)


@pytest.mark.asyncio
async def test_resume_events_continue_on_the_same_channel() -> None:
    """人工确认后的续跑事件仍进同一条流，订阅方接着看。"""

    bus = WorkflowEventBus()
    uid = f"run_resume_{uuid.uuid4().hex[:8]}"
    try:
        _backlog, queue = await bus.subscribe(uid)
        publish = bus.open(uid)

        await publish({"event": "run_waiting", "gate": "g1"})
        await publish({"event": "node_finished", "node_id": "after", "status": "succeeded"})
        await publish({"event": "run_finished", "status": "succeeded"})

        received = await _drain(queue)
        assert [e["event"] for e in received if e is not None] == [
            "run_waiting",
            "node_finished",
            "run_finished",
        ]
        assert bus.is_finished(uid) is True
        bus.unsubscribe(uid, queue)
    finally:
        await bus.drop(uid)


@pytest.mark.asyncio
async def test_finished_run_closes_subscribers() -> None:
    """真正结束时订阅方要收到收尾信号，长连接才能断开。"""

    bus = WorkflowEventBus()
    uid = f"run_end_{uuid.uuid4().hex[:8]}"
    try:
        _backlog, queue = await bus.subscribe(uid)
        publish = bus.open(uid)

        await publish({"event": "run_finished", "status": "succeeded"})

        received = await _drain(queue)
        assert received[0]["event"] == "run_finished"
        assert received[-1] is None
        bus.unsubscribe(uid, queue)
    finally:
        await bus.drop(uid)


@pytest.mark.asyncio
async def test_publish_keeps_bounded_buffer() -> None:
    """长循环不能把内存吃穿：Redis Stream 按 MAXLEN 裁剪，只保留最近事件。"""

    from app.services.eventbus.redis_stream import _MAXLEN

    bus = WorkflowEventBus()
    uid = f"run_long_{uuid.uuid4().hex[:8]}"
    try:
        publish = bus.open(uid)
        for i in range(_MAXLEN + 50):
            await publish({"event": "node_started", "node_id": f"n{i}"})

        backlog, queue = await bus.subscribe(uid)
        assert len(backlog) == _MAXLEN
        # 保留的是最近的
        assert backlog[-1]["node_id"] == f"n{_MAXLEN + 49}"
        bus.unsubscribe(uid, queue)
    finally:
        await bus.drop(uid)


@pytest.mark.asyncio
async def test_start_endpoint_returns_before_execution_finishes(
    env: dict[str, str], api_base: str
) -> None:
    """发起运行只建记录就返回：响应里带 run_uid，且记录当场是 running。"""

    import httpx

    from tests.conftest import login_access_token

    async with httpx.AsyncClient(base_url=api_base, timeout=60.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        headers = {"Authorization": f"Bearer {token}"}

        created = await client.post(
            "/api/v1/workflows",
            headers=headers,
            json={
                "name": f"it-start-{uuid.uuid4().hex[:8]}",
                "definition": {
                    "nodes": [
                        {"id": "start", "kind": "start", "variables": [{"name": "topic"}]},
                        {"id": "end", "kind": "end", "output_key": "topic"},
                    ],
                    "edges": [{"id": "e1", "source": "start", "target": "end"}],
                },
            },
        )
        assert created.status_code == 200, created.text
        workflow_id = created.json()["id"]
        try:
            started = await client.post(
                f"/api/v1/workflows/{workflow_id}/runs/start",
                headers=headers,
                json={"values": {"topic": "磁盘告警"}, "source": "draft"},
            )
            assert started.status_code == 200, started.text
            body = started.json()
            assert body["run_uid"].startswith("run_")
            assert body["workflow_id"] == workflow_id

            run_uid = body["run_uid"]
            detail = await client.get(f"/api/v1/workflows/runs/{run_uid}", headers=headers)
            assert detail.status_code == 200, detail.text
            # 这步断言的是「记录先建好」：状态可能已跑完，但不能是查不到
            assert detail.json()["workflow_status"] == "draft"
        finally:
            await client.delete(f"/api/v1/workflows/{workflow_id}", headers=headers)


@pytest.mark.asyncio
async def test_events_endpoint_rejects_unknown_run(
    env: dict[str, str], api_base: str
) -> None:
    """订阅不存在的运行要明确报错，而不是挂一条空流。"""

    import httpx

    from tests.conftest import login_access_token

    async with httpx.AsyncClient(base_url=api_base, timeout=30.0) as client:
        if (await client.get("/healthz")).status_code != 200:
            pytest.skip("后端未就绪")
        token = await login_access_token(client, env)
        response = await client.get(
            "/api/v1/workflows/runs/run_not_exists/events",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404, response.text
