"""查询层：时间解析（ISO / now-Nm|N h|Nd 相对式）与六域过滤、分页、聚合。

工具面只做参数整形，全部业务查询落这里；返回统一 dict，server.py 序列化为 JSON。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from world import (
    ALERT_RULES,
    HOSTS,
    MIDDLEWARE,
    NETWORK_DEVICES,
    RELATIONS,
    SERVICES,
    SH_TZ,
)
from dataset import Dataset, iso
from series import match_series, rate as series_rate, value as series_value

_REL = re.compile(r"^now((?:\s*[+-]?\s*\d+\s*[mhd])+)$", re.I)
_REL_PART = re.compile(r"([+-])?\s*(\d+)\s*([mhd])", re.I)
_STEP = re.compile(r"^(\d+)\s*([smhd])$", re.I)

_UNIT_SECONDS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}


def parse_time(text: str, default: float) -> float:
    """ISO 8601 或 now-30m / now-1h30m / now-7d → epoch 秒；空串用 default。"""

    s = (text or "").strip()
    if not s:
        return default
    if s.lower() == "now":
        return datetime.now(SH_TZ).timestamp()
    m = _REL.match(s)
    if m:
        total = 0.0
        sign = "-"
        for seg_sign, num, unit in _REL_PART.findall(m.group(1)):
            if seg_sign:
                sign = seg_sign
            delta = int(num) * _UNIT_SECONDS[unit.lower()]
            total += -delta if sign == "-" else delta
        return datetime.now(SH_TZ).timestamp() + total
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=SH_TZ)
        return dt.timestamp()
    except ValueError:
        raise ValueError(f"无法解析时间: {text!r}（支持 ISO 8601 或 now-30m/now-2h/now-7d）")


def parse_step(text: str) -> float:
    """step 参数（如 30s / 5m / 1h）→ 秒；默认 60s。"""

    s = (text or "").strip() or "60s"
    m = _STEP.match(s)
    if not m:
        raise ValueError(f"无法解析 step: {text!r}（支持 30s / 5m / 1h 等）")
    return int(m.group(1)) * _UNIT_SECONDS[m.group(2).lower()]


def _to_iso_range(start: str, end: str, default_window: float) -> tuple[float, float]:
    now = datetime.now(SH_TZ).timestamp()
    end_ts = parse_time(end, now)
    start_ts = parse_time(start, end_ts - default_window)
    return start_ts, end_ts


def _page(items: list, limit: int, offset: int) -> tuple[list, int]:
    limit = max(1, min(limit or 20, 200))
    offset = max(0, offset or 0)
    return items[offset:offset + limit], len(items)


# ---- 指标 ----

def list_metrics(query: str = "") -> dict:
    from world import METRIC_SPECS

    q = (query or "").lower().strip()
    out = []
    for name, spec in METRIC_SPECS.items():
        if q and q not in name.lower() and q not in spec["help"].lower():
            continue
        out.append({"name": name, **spec})
    return {"metrics": out, "count": len(out)}


def get_metric_metadata(metric: str) -> dict:
    from world import METRIC_SPECS

    spec = METRIC_SPECS.get(metric)
    if spec is None:
        raise KeyError(f"未知指标: {metric}")
    series = match_series(metric, {})
    return {"name": metric, **spec, "series_count": len(series),
            "series_sample": series[:8]}


def query_metric(metric: str, labels: str = "", start: str = "", end: str = "",
                 step: str = "60s", rate: bool = False, max_series: int = 12) -> dict:
    """范围查询：labels 形如 service=payment-svc,code=500（子集匹配）。"""

    start_ts, end_ts = _to_iso_range(start, end, 3600.0)
    step_s = parse_step(step)
    want = _parse_labels(labels)
    matched = match_series(metric, want)[:max(1, min(max_series or 12, 30))]
    if not matched:
        return {"metric": metric, "labels": want, "series": [],
                "note": "没有匹配的序列，用 list_metrics 查看可用标签"}
    points_per_series = int((end_ts - start_ts) / step_s) + 1
    if points_per_series > 2000:
        raise ValueError("时间范围/step 产生过多采样点，请缩窗或加大 step")
    out = []
    for s in matched:
        pts = []
        for i in range(points_per_series):
            t = start_ts + i * step_s
            v = series_rate(metric, s, t) if rate else series_value(metric, s, t)
            pts.append([iso(t), v])
        out.append({"labels": s, "points": pts})
    return {"metric": metric, "type": "counter_rate" if rate else
            ("counter" if metric.startswith(("http_", "mysql_slow", "node_cpu")) else "gauge"),
            "start": iso(start_ts), "end": iso(end_ts), "step_seconds": step_s,
            "series": out}


def query_metric_instant(metric: str, labels: str = "", at: str = "") -> dict:
    t = parse_time(at, datetime.now(SH_TZ).timestamp())
    want = _parse_labels(labels)
    matched = match_series(metric, want)[:30]
    out = [{"labels": s, "value": series_value(metric, s, t)} for s in matched]
    return {"metric": metric, "at": iso(t), "results": out}


def get_service_overview(service: str, start: str = "", end: str = "") -> dict:
    """单服务 RED 概览：速率 / 错误率 / 延迟（由指标序列聚合）。"""

    if service not in SERVICES:
        raise KeyError(f"未知服务: {service}")
    start_ts, end_ts = _to_iso_range(start, end, 3600.0)
    steps = 12
    step_s = max((end_ts - start_ts) / steps, 60.0)
    qps_sum, err_sum, p50_sum = 0.0, 0.0, 0.0
    routes = list(SERVICES[service]["routes"])
    for i in range(steps):
        t = start_ts + i * step_s
        # _rate_at 给的是服务级速率，不按实例重复求和
        total = sum(series_rate("http_requests_total",
                                {"service": service, "code": c, "route": r}, t)
                    for c in ("200", "404", "500", "504") for r in routes)
        err = sum(series_rate("http_requests_total",
                              {"service": service, "code": c, "route": r}, t)
                  for c in ("500", "504") for r in routes)
        qps_sum += total
        err_sum += err
        from series import _latency_p50
        p50_sum += _latency_p50(t, service) * 1000
    qps = qps_sum / steps
    err_rate = (err_sum / qps_sum * 100) if qps_sum > 0 else 0.0
    return {"service": service, "display": SERVICES[service]["display"],
            "window": {"start": iso(start_ts), "end": iso(end_ts)},
            "qps": round(qps, 2), "error_rate_pct": round(err_rate, 3),
            "latency_p50_ms": round(p50_sum / steps, 1),
            "instances": [i for i, _h in SERVICES[service]["instances"]],
            "version": SERVICES[service]["version"]}


# ---- 链路 ----

def search_traces(service: str = "", status: str = "", route: str = "",
                  min_duration_ms: float = 0, start: str = "", end: str = "",
                  limit: int = 20, offset: int = 0) -> dict:
    start_ts, end_ts = _to_iso_range(start, end, 86400.0)
    items = []
    for tr in DATA_TRACES:
        t = datetime.fromisoformat(tr["start_time"]).timestamp()
        if not (start_ts <= t <= end_ts):
            continue
        if service and service not in tr["services"]:
            continue
        if status and tr["status"] != status:
            continue
        if route and tr["route"] != route:
            continue
        if tr["duration_ms"] < (min_duration_ms or 0):
            continue
        items.append({k: tr[k] for k in ("trace_id", "service", "name", "route",
                                         "start_time", "duration_ms", "status",
                                         "http_status_code", "span_count", "services")})
    items.sort(key=lambda x: -datetime.fromisoformat(x["start_time"]).timestamp())
    page, total = _page(items, limit, offset)
    return {"traces": page, "total": total, "limit": limit, "offset": offset}


def get_trace(trace_id: str) -> dict:
    tr = DATA_GET_TRACE(trace_id)
    if tr is None:
        raise KeyError(f"未找到链路: {trace_id}")
    return tr


def get_service_topology(start: str = "", end: str = "") -> dict:
    """按链路聚合调用拓扑：服务间边（调用数/错误数/平均耗时）。"""

    start_ts, end_ts = _to_iso_range(start, end, 86400.0)
    edges: dict[tuple, dict] = {}
    for tr in DATA_TRACES:
        t = datetime.fromisoformat(tr["start_time"]).timestamp()
        if not (start_ts <= t <= end_ts):
            continue
        for a, b in zip(tr["services"], tr["services"][1:]):
            key = (a, b)
            e = edges.setdefault(key, {"from": a, "to": b, "calls": 0, "errors": 0,
                                       "total_ms": 0.0})
            e["calls"] += 1
            if tr["status"] == "error":
                e["errors"] += 1
            e["total_ms"] += tr["duration_ms"]
    out = []
    for e in edges.values():
        out.append({"from": e["from"], "to": e["to"], "calls": e["calls"],
                    "errors": e["errors"],
                    "avg_duration_ms": round(e["total_ms"] / max(e["calls"], 1), 1)})
    out.sort(key=lambda x: -x["calls"])
    return {"window": {"start": iso(start_ts), "end": iso(end_ts)}, "edges": out}


def get_trace_stats(service: str = "", start: str = "", end: str = "") -> dict:
    """链路视角 RED：调用数 / 错误数 / 耗时分位（P50/P95/P99）。"""

    start_ts, end_ts = _to_iso_range(start, end, 86400.0)
    durs: list[float] = []
    errors = 0
    for tr in DATA_TRACES:
        t = datetime.fromisoformat(tr["start_time"]).timestamp()
        if not (start_ts <= t <= end_ts):
            continue
        if service and service not in tr["services"]:
            continue
        durs.append(tr["duration_ms"])
        if tr["status"] == "error":
            errors += 1
    durs.sort()
    def pct(p: float) -> float:
        if not durs:
            return 0.0
        i = min(int(len(durs) * p), len(durs) - 1)
        return round(durs[i], 1)
    return {"service": service or "全部", "count": len(durs), "errors": errors,
            "error_rate_pct": round(errors / len(durs) * 100, 2) if durs else 0.0,
            "p50_ms": pct(0.5), "p95_ms": pct(0.95), "p99_ms": pct(0.99),
            "max_ms": round(durs[-1], 1) if durs else 0.0}


# ---- 日志 ----

def search_logs(keyword: str = "", service: str = "", level: str = "",
                trace_id: str = "", start: str = "", end: str = "",
                limit: int = 50, offset: int = 0) -> dict:
    start_ts, end_ts = _to_iso_range(start, end, 3600.0)
    kw = (keyword or "").lower().strip()
    items = []
    for lg in DATA_LOGS:
        t = datetime.fromisoformat(lg["ts"]).timestamp()
        if not (start_ts <= t <= end_ts):
            continue
        if service and lg["service"] != service:
            continue
        if level and lg["level"] != level.upper():
            continue
        if trace_id and lg["trace_id"] != trace_id:
            continue
        if kw and kw not in lg["message"].lower() and kw not in lg["logger"].lower():
            continue
        items.append(lg)
    items.sort(key=lambda x: -datetime.fromisoformat(x["ts"]).timestamp())
    page, total = _page(items, limit, offset)
    return {"logs": page, "total": total, "limit": limit, "offset": offset}


def get_logs_by_trace(trace_id: str) -> dict:
    items = [lg for lg in DATA_LOGS if lg["trace_id"] == trace_id]
    return {"trace_id": trace_id, "logs": items, "count": len(items)}


def get_log_level_stats(service: str = "", start: str = "", end: str = "") -> dict:
    start_ts, end_ts = _to_iso_range(start, end, 3600.0)
    stats: dict[str, int] = {}
    for lg in DATA_LOGS:
        t = datetime.fromisoformat(lg["ts"]).timestamp()
        if not (start_ts <= t <= end_ts):
            continue
        if service and lg["service"] != service:
            continue
        stats[lg["level"]] = stats.get(lg["level"], 0) + 1
    return {"service": service or "全部",
            "window": {"start": iso(start_ts), "end": iso(end_ts)},
            "levels": stats, "total": sum(stats.values())}


# ---- CMDB ----

def _all_cis() -> list[dict]:
    cis = []
    for name, meta in SERVICES.items():
        cis.append({"id": name, "type": "service", "name": name,
                    "display": meta["display"], "env": "prod", "owner": meta["owner"],
                    "team": meta["team"], "version": meta["version"], "sla": meta["sla"],
                    "instances": [i for i, _h in meta["instances"]]})
    for name, meta in HOSTS.items():
        cis.append({"id": name, "type": "host", "name": name, "display": name,
                    "env": meta["env"], "owner": meta["owner"], **meta})
    for name, meta in MIDDLEWARE.items():
        cis.append({"id": name, "type": meta["kind"], "name": name,
                    "display": meta["display"], "env": "prod", "owner": meta["owner"],
                    **meta})
    for name, meta in NETWORK_DEVICES.items():
        cis.append({"id": name, "type": "network_device", "name": name,
                    "display": meta["display"], "env": "prod", "owner": meta["owner"],
                    **meta})
    return cis


def list_cis(type: str = "", env: str = "", owner: str = "") -> dict:
    items = _all_cis()
    if type:
        items = [c for c in items if c["type"] == type]
    if env:
        items = [c for c in items if c.get("env") == env]
    if owner:
        items = [c for c in items if c.get("owner") == owner]
    return {"cis": items, "count": len(items),
            "types": sorted({c["type"] for c in _all_cis()})}


def get_ci(name: str) -> dict:
    for c in _all_cis():
        if c["id"] == name:
            relations = [r for r in RELATIONS if r["from"] == name or r["to"] == name]
            return {"ci": c, "relations": relations}
    raise KeyError(f"未找到 CI: {name}")


def search_cis(keyword: str) -> dict:
    kw = (keyword or "").lower().strip()
    if not kw:
        raise ValueError("keyword 不能为空")
    items = []
    for c in _all_cis():
        blob = " ".join(str(v) for v in c.values()).lower()
        if kw in blob:
            items.append(c)
    return {"cis": items, "count": len(items)}


def get_service_dependencies(service: str) -> dict:
    if service not in SERVICES:
        raise KeyError(f"未知服务: {service}")
    upstream = [r["from"] for r in RELATIONS
                if r["to"] == service and r["type"] == "depends_on"]
    downstream = [{"to": r["to"], "note": r["note"]} for r in RELATIONS
                  if r["from"] == service and r["type"] == "depends_on"]
    runs_on = [r["to"] for r in RELATIONS
               if r["from"] == service and r["type"] == "runs_on"]
    return {"service": service, "upstream": upstream, "downstream": downstream,
            "runs_on": runs_on}


# ---- 告警 ----

def list_alerts(state: str = "", severity: str = "", service: str = "",
                start: str = "", end: str = "") -> dict:
    start_ts, end_ts = _to_iso_range(start, end, 86400.0)
    items = []
    for al in DATA_ALERTS:
        t = datetime.fromisoformat(al["startsAt"]).timestamp()
        if not (start_ts <= t <= end_ts):
            continue
        if state and al["status"]["state"] != state:
            continue
        if severity and al["labels"]["severity"] != severity:
            continue
        if service and al["labels"]["service"] != service:
            continue
        items.append(al)
    items.sort(key=lambda x: x["startsAt"], reverse=True)
    return {"alerts": items, "count": len(items)}


def get_alert(fingerprint: str) -> dict:
    for al in DATA_ALERTS:
        if al["fingerprint"] == fingerprint:
            return al
    raise KeyError(f"未找到告警: {fingerprint}")


def list_alert_rules(service: str = "", severity: str = "") -> dict:
    items = ALERT_RULES
    if service:
        items = [r for r in items if r["service"] == service]
    if severity:
        items = [r for r in items if r["severity"] == severity]
    return {"rules": items, "count": len(items)}


def acknowledge_alert(fingerprint: str, note: str = "") -> dict:
    al = DATA_ACK(fingerprint, note)
    if al is None:
        raise KeyError(f"未找到告警: {fingerprint}")
    return {"acknowledged": True, "alert": al}


def create_silence(matchers: str, duration_minutes: int = 60, comment: str = "") -> dict:
    """matchers 形如 alertname=RedisMemoryHigh,service=redis-cache-01。"""

    want = _parse_labels(matchers)
    if not want:
        raise ValueError("matchers 不能为空（alertname=...,service=...）")
    dur = max(1, min(int(duration_minutes or 60), 7 * 24 * 60))
    sil = {"id": f"sil-{len(DATA_SILENCES) + 1:04d}", "matchers": want,
           "starts_at": iso(datetime.now(SH_TZ).timestamp()),
           "ends_at": iso(datetime.now(SH_TZ).timestamp() + dur * 60),
           "duration_minutes": dur, "comment": comment or "",
           "created_by": "mcp-obs"}
    DATA_SILENCES.append(sil)
    matched = [al["fingerprint"] for al in DATA_ALERTS
               if all(al["labels"].get(k) == v for k, v in want.items())]
    return {"silence": sil, "matched_fingerprints": matched,
            "matched_count": len(matched)}


# ---- 事件 ----

def list_events(type: str = "", service: str = "", start: str = "", end: str = "",
                limit: int = 50) -> dict:
    start_ts, end_ts = _to_iso_range(start, end, 86400.0)
    items = []
    for ev in DATA_EVENTS:
        t = datetime.fromisoformat(ev["ts"]).timestamp()
        if not (start_ts <= t <= end_ts):
            continue
        if type and ev["type"] != type:
            continue
        if service and ev["service"] != service:
            continue
        items.append(ev)
    items.sort(key=lambda x: x["ts"], reverse=True)
    page, total = _page(items, limit, 0)
    return {"events": page, "total": total}


def get_event(event_id: str) -> dict:
    for ev in DATA_EVENTS:
        if ev["event_id"] == event_id:
            return ev
    raise KeyError(f"未找到事件: {event_id}")


def get_change_timeline(service: str = "", start: str = "", end: str = "") -> dict:
    """变更事件 + 告警合并时间线（事故因果回看用）。"""

    start_ts, end_ts = _to_iso_range(start, end, 86400.0)
    items = []
    for ev in DATA_EVENTS:
        t = datetime.fromisoformat(ev["ts"]).timestamp()
        if start_ts <= t <= end_ts and (not service or ev["service"] == service):
            items.append({"kind": "event", "ts": ev["ts"], "id": ev["event_id"],
                          "title": ev["title"], "type": ev["type"],
                          "status": ev["status"], "detail": ev["description"]})
    for al in DATA_ALERTS:
        t = datetime.fromisoformat(al["startsAt"]).timestamp()
        if start_ts <= t <= end_ts and (not service or al["labels"]["service"] == service):
            items.append({"kind": "alert", "ts": al["startsAt"],
                          "id": al["fingerprint"], "title": al["annotations"]["summary"],
                          "type": al["labels"]["alertname"],
                          "status": al["status"]["state"],
                          "detail": al["annotations"]["description"]})
    items.sort(key=lambda x: x["ts"])
    return {"timeline": items, "count": len(items),
            "window": {"start": iso(start_ts), "end": iso(end_ts)}}


# ---- 总览 ----

def get_system_overview() -> dict:
    firing = [a for a in DATA_ALERTS if a["status"]["state"] == "firing"]
    by_sev: dict[str, int] = {}
    for a in firing:
        sev = a["labels"]["severity"]
        by_sev[sev] = by_sev.get(sev, 0) + 1
    recent_events = sorted(DATA_EVENTS, key=lambda e: e["ts"], reverse=True)[:5]
    services_health = []
    for svc in SERVICES:
        ov = get_service_overview(svc)
        services_health.append({"service": svc, "qps": ov["qps"],
                                "error_rate_pct": ov["error_rate_pct"],
                                "latency_p50_ms": ov["latency_p50_ms"]})
    return {"generated_at": iso(datetime.now(SH_TZ).timestamp()),
            "firing_alerts": len(firing), "firing_by_severity": by_sev,
            "firing_alert_names": [a["labels"]["alertname"] for a in firing],
            "recent_events": [{"event_id": e["event_id"], "ts": e["ts"],
                               "title": e["title"], "type": e["type"]}
                              for e in recent_events],
            "services": services_health,
            "active_silences": len(DATA_SILENCES)}


# ---- 内部：解析与数据集绑定 ----

def _parse_labels(text: str) -> dict:
    out: dict[str, str] = {}
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"标签格式应为 k=v, 收到: {part!r}")
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


# 模块级数据集句柄：server.py 启动时绑定（测试可换 build_dataset 产物）
DATA_TRACES: list[dict] = []
DATA_LOGS: list[dict] = []
DATA_ALERTS: list[dict] = []
DATA_EVENTS: list[dict] = []
DATA_SILENCES: list[dict] = []
DATA_GET_TRACE = lambda trace_id: None  # noqa: E731
DATA_ACK = lambda fingerprint, note: None  # noqa: E731


def bind_dataset(ds: Dataset) -> None:
    """把查询层指到具体数据集（进程一份；测试用固定 t0 的构建体）。"""

    global DATA_TRACES, DATA_LOGS, DATA_ALERTS, DATA_EVENTS, DATA_SILENCES
    global DATA_GET_TRACE, DATA_ACK
    DATA_TRACES = ds.traces
    DATA_LOGS = ds.logs
    DATA_ALERTS = ds.alerts
    DATA_EVENTS = ds.events
    DATA_SILENCES = ds.silences
    DATA_GET_TRACE = ds.get_trace
    DATA_ACK = ds.acknowledge
