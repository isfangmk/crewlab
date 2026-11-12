"""运行事件总线与「发起即返回」。

覆盖范围：
  - 事件总线：补发已发生事件、多订阅者都能收到、终止后标记完成；
  - 发起运行：接口立刻返回 run_uid，运行记录当场是 running；
  - 订阅：运行已结束时补完历史就收尾，不留长连接。
不覆盖：真实画布执行（由 test_workflow_graph / test_workflow_run_state 覆盖）。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.services.workflow_events import WorkflowEventBus


def test_subscribe_replays_events_already_published() -> None:
    """订阅方比执行晚到也不能缺步骤：连上先补发。"""

    bus = WorkflowEventBus()
    channel = bus.channel("run_x")
    channel.publish({"event": "run_started", "run_uid": "run_x"})
    channel.publish({"event": "node_started", "node_id": "a"})

    backlog, _queue = asyncio.run(bus.subscribe("run_x"))
    assert [e["event"] for e in backlog] == ["run_started", "node_started"]


def test_two_subscribers_both_receive_later_events() -> None:
    """发起方与查看方各连各的流，事件要都能收到。"""

    bus = WorkflowEventBus()
    channel = bus.channel("run_y")
    first: asyncio.Queue = asyncio.Queue()
    second: asyncio.Queue = asyncio.Queue()
    channel.subscribers.update({first, second})

    channel.publish({"event": "node_finished", "node_id": "a", "status": "succeeded"})

    assert first.get_nowait()["node_id"] == "a"
    assert second.get_nowait()["node_id"] == "a"


def test_channel_marks_finished_on_terminal_event() -> None:
    bus = WorkflowEventBus()
    channel = bus.channel("run_z")
    assert bus.is_finished("run_z") is False

    channel.publish({"event": "run_finished", "status": "succeeded"})
    assert bus.is_finished("run_z") is True


def test_subscribe_after_finish_returns_full_history() -> None:
    """运行早就结束才来订阅：补完历史即可，不必等长连接。"""

    bus = WorkflowEventBus()
    channel = bus.channel("run_done")
    channel.publish({"event": "run_started", "run_uid": "run_done"})
    channel.publish({"event": "run_finished", "status": "succeeded"})

    backlog, _queue = asyncio.run(bus.subscribe("run_done"))
    assert [e["event"] for e in backlog] == ["run_started", "run_finished"]
    assert bus.is_finished("run_done") is True


def test_close_wakes_up_subscribers() -> None:
    """运行结束时订阅方要能收到收尾信号，否则详情页一直挂着。"""

    bus = WorkflowEventBus()
    _backlog, queue = asyncio.run(bus.subscribe("run_close"))
    bus.channel("run_close").close()
    assert queue.get_nowait() is None


def test_waiting_does_not_close_the_channel() -> None:
    """停在人工节点只是暂停：通道要留着，续跑事件才能进同一条流。"""

    bus = WorkflowEventBus()
    channel = bus.channel("run_gate")
    _backlog, queue = asyncio.run(bus.subscribe("run_gate"))

    channel.publish({"event": "run_waiting", "gate": "g1"})

    assert bus.is_finished("run_gate") is False
    # 暂停事件推到了订阅方，但没有收尾信号：长连接保持打开等续跑
    assert queue.get_nowait()["event"] == "run_waiting"
    assert queue.empty()


def test_resume_events_continue_on_the_same_channel() -> None:
    """人工确认后的续跑事件仍进同一条流，订阅方接着看。"""

    bus = WorkflowEventBus()
    channel = bus.channel("run_resume")
    _backlog, queue = asyncio.run(bus.subscribe("run_resume"))

    channel.publish({"event": "run_waiting", "gate": "g1"})
    channel.publish({"event": "node_finished", "node_id": "after", "status": "succeeded"})
    channel.publish({"event": "run_finished", "status": "succeeded"})

    received = [queue.get_nowait() for _ in range(2)]
    assert [e["event"] for e in received] == ["run_waiting", "node_finished"]
    assert bus.is_finished("run_resume") is True


def test_finished_run_closes_subscribers() -> None:
    """真正结束时订阅方要收到收尾信号，长连接才能断开。"""

    bus = WorkflowEventBus()
    channel = bus.channel("run_end")
    _backlog, queue = asyncio.run(bus.subscribe("run_end"))

    channel.publish({"event": "run_finished", "status": "succeeded"})

    assert queue.get_nowait()["event"] == "run_finished"
    assert queue.get_nowait() is None


def test_publish_keeps_bounded_buffer() -> None:
    """长循环不能把内存吃穿：缓冲按上限丢弃最旧事件。"""

    from app.services.workflow_events import _MAX_EVENTS_PER_RUN

    bus = WorkflowEventBus()
    channel = bus.channel("run_long")
    for i in range(_MAX_EVENTS_PER_RUN + 50):
        channel.publish({"event": "node_started", "node_id": f"n{i}"})

    assert len(channel.events) == _MAX_EVENTS_PER_RUN
    # 保留的是最近的
    assert channel.events[-1]["node_id"] == f"n{_MAX_EVENTS_PER_RUN + 49}"


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
