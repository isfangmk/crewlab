"""可观测 MCP（scripts/mcp-obs）数据层测试：工具面齐全 + 事故剧本跨域一致性。

纯内存仿真数据，不触库、不改用户数据；sys.path 指向 scripts/mcp-obs 直接导入。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

_OBS = Path(__file__).resolve().parents[2] / "scripts" / "mcp-obs"
sys.path.insert(0, str(_OBS))

import dataset as obs_dataset  # noqa: E402
import queries as obs_queries  # noqa: E402
import series as obs_series  # noqa: E402
from world import INCIDENT, abs_offset  # noqa: E402


@pytest.fixture(scope="module")
def obs() -> obs_dataset.Dataset:
    """固定时间锚的数据集：整组测试共享，断言可依赖事故窗相对位置。"""

    ds = obs_dataset.build_dataset()
    obs_queries.bind_dataset(ds)
    return ds


def _ts(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()


class TestToolSurface:
    def test_25_tools_registered(self):
        import server as obs_server

        names = set(obs_server.server._tool_manager._tools)
        assert len(names) == 25
        for n in ("query_metric", "search_traces", "search_logs", "list_cis",
                  "list_alerts", "list_events", "get_system_overview",
                  "acknowledge_alert", "create_silence"):
            assert n in names

    def test_readonly_heuristic_labels(self):
        """查询工具过只读启发式 → 安全；写工具不过 → 需审批（与后端判定同口径）。"""

        from app.services.mcp_service import _needs_approval

        import server as obs_server

        names = set(obs_server.server._tool_manager._tools)
        approval_needed = {n for n in names if _needs_approval("non_readonly", n, "")}
        assert approval_needed == {"acknowledge_alert", "create_silence"}


class TestIncidentCrossDomain:
    """事故剧本：发布 → 错误率升 → 告警 → 回滚 → 恢复，六域同窗互证。"""

    def test_metric_error_spike_in_incident_window(self, obs):
        t_start = abs_offset(INCIDENT["degrade_start"])
        t_end = abs_offset(INCIDENT["recover"])
        inc = obs_series.rate("http_requests_total",
                              {"service": "payment-svc", "code": "504",
                               "route": "/payments/charge"}, t_start + 400)
        base = obs_series.rate("http_requests_total",
                               {"service": "payment-svc", "code": "504",
                                "route": "/payments/charge"},
                               abs_offset(-20 * 3600))
        assert inc > base * 20  # 事故窗 504 速率比常态高一个数量级以上

    def test_error_traces_share_trace_id_with_error_logs(self, obs):
        t_start = abs_offset(INCIDENT["degrade_start"])
        t_end = abs_offset(INCIDENT["recover"])
        err_traces = [tr for tr in obs.traces
                      if tr["status"] == "error" and t_start <= _ts(tr["start_time"]) <= t_end
                      and "payment-svc" in tr["services"]]
        assert len(err_traces) >= 30
        # 错误链路的 payment span 必须是 ERROR 且带异常事件
        sample = err_traces[0]
        err_spans = [s for s in sample["spans"] if s["status"]["code"] == "ERROR"]
        assert err_spans and err_spans[0]["service"] == "payment-svc"
        assert err_spans[0]["events"]
        assert "Timeout" in err_spans[0]["events"][0]["attributes"]["exception.type"]
        # 错误日志共享 trace_id（链路↔日志可互相追）
        log_ids = {lg["trace_id"] for lg in obs.logs if lg["trace_id"]}
        matched = [tr for tr in err_traces if tr["trace_id"] in log_ids]
        assert len(matched) >= 10

    def test_alert_fires_and_resolves_in_incident_window(self, obs):
        pay_alerts = [a for a in obs.alerts
                      if a["labels"]["alertname"] == "PaymentErrorRateHigh"]
        assert len(pay_alerts) == 1
        al = pay_alerts[0]
        assert al["status"]["state"] == "resolved"
        # 起点紧随发布、终点落在恢复点附近
        deploy = abs_offset(INCIDENT["deploy_payment"])
        recover = abs_offset(INCIDENT["recover"])
        assert deploy <= _ts(al["startsAt"]) <= deploy + 600
        assert abs(_ts(al["endsAt"]) - recover) < 300
        assert al["labels"]["severity"] == "critical"

    def test_events_form_causal_chain(self, obs):
        tl = obs_queries.get_change_timeline("payment-svc", "now-12h", "now-5h")
        kinds = [(x["kind"], x["type"]) for x in tl["timeline"]]
        assert ("event", "deploy") in kinds
        assert ("alert", "PaymentErrorRateHigh") in kinds
        assert ("event", "rollback") in kinds
        # 时间正序：deploy 早于告警，告警早于回滚
        idx = {x["type"]: _ts(x["ts"]) for x in tl["timeline"]}
        assert idx["deploy"] < idx["PaymentErrorRateHigh"] < idx["rollback"]

    def test_firing_alerts_match_ongoing_anomalies(self, obs):
        firing = [a for a in obs.alerts if a["status"]["state"] == "firing"]
        names = {a["labels"]["alertname"] for a in firing}
        assert {"RedisMemoryHigh", "SwitchPortCRCErrors"} <= names
        # redis 内存确实停在 85% 线上方，与告警互证
        v = obs_series.value("redis_memory_used_bytes", {"instance": "redis-cache-01"},
                             abs_offset(0))
        assert v > 0.85 * 17179869184


class TestQueryLayer:
    def test_relative_time_compound_offset(self):
        now = datetime.now().timestamp()
        t = obs_queries.parse_time("now-1h30m", 0.0)
        assert abs((now - t) - 5400) < 5

    def test_logs_pagination_and_filter(self, obs):
        page1 = obs_queries.search_logs(level="ERROR", limit=5, offset=0)
        assert len(page1["logs"]) <= 5
        assert page1["total"] >= len(page1["logs"])
        assert all(lg["level"] == "ERROR" for lg in page1["logs"])

    def test_metric_query_series_shape(self, obs):
        md = obs_queries.query_metric("http_requests_total",
                                      "service=payment-svc,code=504",
                                      "now-2h", "now-1h", "10m")
        assert md["series"]
        pts = md["series"][0]["points"]
        assert len(pts) == 7
        # counter 单调不减
        vals = [p[1] for p in pts]
        assert all(b >= a for a, b in zip(vals, vals[1:]))

    def test_acknowledge_alert_mutates(self, obs):
        target = next(a for a in obs.alerts if not a["acknowledged"])
        out = obs_queries.acknowledge_alert(target["fingerprint"], "测试认领")
        assert out["alert"]["acknowledged"] is True
        assert out["alert"]["acknowledged_note"] == "测试认领"

    def test_create_silence_matches(self, obs):
        out = obs_queries.create_silence("alertname=RedisMemoryHigh", 30, "测试")
        assert out["matched_count"] >= 1
        assert out["silence"]["duration_minutes"] == 30

    def test_cmdb_relations_complete(self, obs):
        dep = obs_queries.get_service_dependencies("payment-svc")
        assert "mysql-order-01" in [d["to"] for d in dep["downstream"]]
        assert dep["runs_on"]
        ci = obs_queries.get_ci("core-sw-01")
        assert ci["ci"]["type"] == "network_device"
        assert ci["relations"]
