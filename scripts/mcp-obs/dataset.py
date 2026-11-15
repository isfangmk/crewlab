"""启动期物化链路/日志/告警/事件数据（指标在 series.py 按需抽样）。

事故窗口统一锚定 T0 相对偏移，错误日志与错误链路共享 trace_id，
告警起止、变更事件与指标尖峰落在同一时间线，保证跨域可互相印证。
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime

from world import (
    ALERT_RULES,
    abs_offset,
    HOSTS,
    INCIDENT,
    MIDDLEWARE,
    SERVICES,
    SH_TZ,
    rel_time,
)

# 数据覆盖窗口
TRACE_WINDOW = 72 * 3600.0   # 链路/日志近 72h
EVENT_WINDOW = 7 * 86400.0   # 事件/告警历史近 7 天


def iso(t: float) -> str:
    """epoch 秒 → ISO 8601（+08:00，毫秒精度）。"""

    return datetime.fromtimestamp(t, SH_TZ).isoformat(timespec="milliseconds")


def _hex(seed: str, n: int) -> str:
    """确定性十六进制串（trace_id / span_id / fingerprint 用）。"""

    return hashlib.sha1(seed.encode()).hexdigest()[:n]


class Dataset:
    """一次构建的全量仿真数据集；acknowledge/silence 会就地改内存状态。"""

    def __init__(self, t0: datetime):
        self.t0 = t0
        self.t0_ts = t0.timestamp()
        rng = random.Random("obs-mcp-2026")
        self.traces = _gen_traces(self.t0_ts, rng)
        self.logs = _gen_logs(self.t0_ts, self.traces, rng)
        self.alerts = _gen_alerts(self.t0_ts)
        self.events = _gen_events(self.t0_ts, rng)
        self.silences: list[dict] = []
        self._trace_index = {tr["trace_id"]: tr for tr in self.traces}


    def get_trace(self, trace_id: str) -> dict | None:
        return self._trace_index.get(trace_id)

    def acknowledge(self, fingerprint: str, note: str) -> dict | None:
        """告警置为已认领（内存态，进程重启即清）。"""

        for alert in self.alerts:
            if alert["fingerprint"] == fingerprint:
                alert["acknowledged"] = True
                alert["acknowledged_note"] = note
                return alert
        return None


# ---- 链路 ----

# 根路由 → 调用路径（服务/中间件链，span 树按此展开）
_CALL_PATHS: dict[str, list[str]] = {
    "/api/orders": ["gateway", "order-svc", "payment-svc", "mysql-order-01"],
    "/api/payments": ["gateway", "order-svc", "payment-svc", "mysql-order-01"],
    "/api/users": ["gateway", "user-svc", "mysql-order-01", "redis-cache-01"],
    "/api/search": ["gateway", "search-svc", "mysql-order-02"],
    "/api/inventory": ["gateway", "order-svc", "inventory-svc", "redis-cache-01"],
    "/orders/create": ["gateway", "order-svc", "payment-svc", "mysql-order-01"],
    "/orders/{id}": ["gateway", "order-svc", "mysql-order-01"],
    "/orders/list": ["gateway", "order-svc", "mysql-order-02"],
    "/payments/charge": ["gateway", "payment-svc", "mysql-order-01", "redis-cache-01"],
    "/payments/refund": ["gateway", "payment-svc", "mysql-order-01"],
    "/payments/{id}": ["gateway", "payment-svc", "mysql-order-01"],
    "/users/login": ["gateway", "user-svc", "mysql-order-01", "redis-cache-01"],
    "/users/{id}": ["gateway", "user-svc", "mysql-order-01"],
    "/users/profile": ["gateway", "user-svc", "redis-cache-01"],
    "/search/query": ["gateway", "search-svc", "mysql-order-02"],
    "/search/suggest": ["gateway", "search-svc", "redis-cache-01"],
    "/inventory/stock": ["gateway", "order-svc", "inventory-svc", "redis-cache-01"],
    "/inventory/reserve": ["gateway", "order-svc", "inventory-svc", "mysql-order-01"],
    "/notify/send": ["gateway", "notify-svc", "kafka-01"],
    "/notify/templates": ["gateway", "notify-svc", "redis-cache-01"],
    "/report/daily": ["gateway", "report-svc", "mysql-order-02"],
    "/report/export": ["gateway", "report-svc", "mysql-order-02"],
}

_DB_ATTRS = {
    "mysql-order-01": {"db.system": "mysql", "db.name": "order_db",
                       "net.peer.name": "mysql-order-01", "net.peer.port": 3306},
    "mysql-order-02": {"db.system": "mysql", "db.name": "order_db",
                       "net.peer.name": "mysql-order-02", "net.peer.port": 3306},
    "redis-cache-01": {"db.system": "redis", "db.name": "0",
                       "net.peer.name": "redis-cache-01", "net.peer.port": 6379},
    "redis-cache-02": {"db.system": "redis", "db.name": "0",
                       "net.peer.name": "redis-cache-02", "net.peer.port": 6379},
    "kafka-01": {"messaging.system": "kafka", "messaging.destination": "order-events",
                 "net.peer.name": "kafka-01", "net.peer.port": 9092},
}


def _in_incident(t: float) -> bool:
    """事故窗判定：INCIDENT 是相对偏移，统一经 abs_offset 换算。"""

    return abs_offset(INCIDENT["degrade_start"]) <= t <= abs_offset(INCIDENT["recover"])


def _gen_traces(t0: float, rng: random.Random) -> list[dict]:
    """近 72h 链路：常态为主，事故窗内 payment/order 路径错误率显著抬升。"""

    traces: list[dict] = []
    n = 3200
    routes = list(_CALL_PATHS.keys())
    for i in range(n):
        # 越接近当前时间样本越密（采样留存随时间衰减）
        frac = rng.random() ** 0.7
        t = t0 - TRACE_WINDOW * (1.0 - frac)
        route = rng.choice(routes)
        path = _CALL_PATHS[route]
        trace_id = _hex(f"trace:{i}", 32)
        incident = _in_incident(t) and route in (
            "/api/payments", "/api/orders", "/orders/create", "/payments/charge",
            "/payments/refund",
        )
        # 事故窗内约 35% 的支付/订单链路出错
        is_error = rng.random() < (0.35 if incident else 0.012)
        spans, duration_ms = _build_spans(trace_id, route, path, t, is_error, incident,
                                          rng)
        root = spans[0]
        status_code = 504 if is_error else 200
        traces.append({
            "trace_id": trace_id,
            "service": root["service"],
            "name": root["name"],
            "route": route,
            "start_time": iso(t),
            "end_time": iso(t + duration_ms / 1000.0),
            "duration_ms": duration_ms,
            "status": "error" if is_error else "ok",
            "http_status_code": status_code,
            "span_count": len(spans),
            "services": sorted({s["service"] for s in spans}),
            "spans": spans,
        })
    # 事故窗密集采样：15 分钟窗补 240 条支付/订单链路，35% 出错（撑起 12% 错误率叙事）
    incident_routes = ["/api/payments", "/api/orders", "/orders/create",
                       "/payments/charge", "/payments/refund"]
    inc_start = abs_offset(INCIDENT["degrade_start"])
    inc_end = abs_offset(INCIDENT["recover"])
    for i in range(240):
        t = inc_start + rng.random() * (inc_end - inc_start)
        route = rng.choice(incident_routes)
        path = _CALL_PATHS[route]
        trace_id = _hex(f"trace:inc:{i}", 32)
        is_error = rng.random() < 0.35
        spans, duration_ms = _build_spans(trace_id, route, path, t, is_error, True, rng)
        root = spans[0]
        traces.append({
            "trace_id": trace_id,
            "service": root["service"],
            "name": root["name"],
            "route": route,
            "start_time": iso(t),
            "end_time": iso(t + duration_ms / 1000.0),
            "duration_ms": duration_ms,
            "status": "error" if is_error else "ok",
            "http_status_code": 504 if is_error else 200,
            "span_count": len(spans),
            "services": sorted({s["service"] for s in spans}),
            "spans": spans,
        })
    traces.sort(key=lambda tr: tr["start_time"])
    return traces


def _build_spans(trace_id: str, route: str, path: list[str], t: float, is_error: bool,
                 incident: bool, rng: random.Random) -> tuple[list[dict], int]:
    """按调用路径展开 SERVER/CLIENT/DB span 树，错误链路在 payment-svc 挂异常事件。"""

    spans: list[dict] = []
    parent = ""
    cur = t
    total = 0.0
    for depth, node in enumerate(path):
        span_id = _hex(f"{trace_id}:{depth}", 16)
        is_db = node in _DB_ATTRS
        kind = "CLIENT" if is_db else "SERVER"
        name = node if is_db else f"{'POST' if 'create' in route or 'charge' in route or 'refund' in route or 'login' in route or 'send' in route or 'reserve' in route or 'export' in route else 'GET'} {route}"
        # span 耗时：越深越短，事故窗内 payment-svc / db 段被放大
        if is_db:
            dur = 8.0 + rng.random() * 30.0
        elif depth == 0:
            dur = 120.0 + rng.random() * 260.0
        else:
            dur = 40.0 + rng.random() * 160.0
        if incident and node in ("payment-svc", "mysql-order-01"):
            dur *= 8.0 if node == "payment-svc" else 5.0
        # 错误标注节点：事故链路标 payment-svc，其余链路标第一个业务服务
        err_node = "payment-svc" if "payment-svc" in path else path[1]
        err_here = is_error and node == err_node
        if err_here and err_node == "payment-svc":
            dur = 3000.0 + rng.random() * 200  # 连接池等待超时 3s
        span_start = cur + (total * 0.05)
        attrs: dict = {}
        if not is_db:
            attrs = {"http.method": "POST" if name.startswith("POST") else "GET",
                     "http.route": route, "http.status_code": 504 if err_here else 200,
                     "http.target": route}
            if node == "gateway":
                attrs["http.user_agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                attrs["net.peer.ip"] = "10.8.0." + str(rng.randint(2, 250))
            else:
                attrs["rpc.service"] = node
        else:
            attrs = dict(_DB_ATTRS[node])
            if err_here:
                attrs["db.statement"] = "SELECT /*+ MAX_EXECUTION_TIME(3000) */ ... FROM payment_order"
            else:
                attrs["db.statement"] = "SELECT id, status, amount FROM payment_order WHERE user_id = ? LIMIT 20"
        events = []
        if err_here and err_node == "payment-svc":
            status = {"code": "ERROR", "message": "连接池获取超时 (3000ms)"}
            events.append({
                "name": "exception", "time": iso(span_start + 3000),
                "attributes": {"exception.type": "GetConnectionTimeoutException",
                               "exception.message": "HikariPool-1 - 连接获取超时，等待 3000ms 后放弃",
                               "exception.stacktrace": "com.zaxxer.hikari.pool.HikariPool."
                                                      "GetConnectionTimeoutException: Could not "
                                                      "get connection within 3000ms\n\tat "
                                                      "com.zaxxer.hikari.pool.HikariPool."
                                                      "getConnection(HikariPool.java:738)"},
            })
        elif err_here:
            status = {"code": "ERROR", "message": "上游调用失败 (HTTP 504)"}
            events.append({
                "name": "exception", "time": iso(span_start + 500),
                "attributes": {"exception.type": "UpstreamTimeoutError",
                               "exception.message": "调用下游服务超时 (504)，上游已熔断返回",
                               "exception.stacktrace": ""},
            })
        else:
            status = {"code": "OK", "message": ""}
        spans.append({
            "span_id": span_id, "parent_span_id": parent, "name": name,
            "service": node, "kind": kind,
            "start_time": iso(span_start), "end_time": iso(span_start + dur / 1000.0),
            "duration_ms": round(dur, 2), "attributes": attrs,
            "status": status, "events": events,
        })
        parent = span_id
        total += dur * 0.35
    root_dur = spans[0]["duration_ms"]
    return spans, round(root_dur, 2)


# ---- 日志 ----

_APP_LOGGERS = {
    "gateway": "platform.gateway.AccessFilter", "user-svc": "com.example.user.LoginService",
    "order-svc": "com.example.order.OrderService", "payment-svc": "com.example.pay.ChargeService",
    "inventory-svc": "platform.inventory.StockService", "notify-svc": "platform.notify.SendService",
    "search-svc": "platform.search.QueryService", "report-svc": "report.export.DailyReportJob",
}

_NORMAL_MSGS = [
    "请求处理完成 elapsed={ms}ms",
    "缓存命中 key=order:{oid}",
    "会话校验通过 uid={uid}",
    "库存扣减成功 sku={sku} qty=1",
    "消息投递成功 topic=order-events",
    "报表任务分片 {n}/8 处理完成",
    "连接池活跃={a} 空闲={i}",
    "配置热加载完成变更项=3",
]

_ERROR_MSGS_INCIDENT = [
    ("ERROR", "com.example.pay.ChargeService", "支付下单失败: 获取数据库连接超时 (3000ms) order_id={oid}"),
    ("ERROR", "com.example.pay.ChargeService", "HikariPool-1 - 连接获取超时，池状态 active=50 idle=0 waiting=187"),
    ("WARN", "com.example.pay.ChargeService", "支付渠道重试第 {n}/3 次 order_id={oid}"),
    ("ERROR", "com.example.order.OrderService", "创建订单失败: 调用支付服务超时 order_id={oid}"),
    ("WARN", "com.example.order.OrderService", "订单落库延迟升高 elapsed={ms}ms"),
]


def _gen_logs(t0: float, traces: list[dict], rng: random.Random) -> list[dict]:
    """近 72h 日志：access 借链路生成、app/慢SQL/系统按分钟散布，错误聚集事故窗。"""

    logs: list[dict] = []
    svc_names = list(SERVICES.keys())
    # 1) 访问日志：取每条 trace 的 root，形成 nginx 风格行
    for tr in traces:
        t = datetime.fromisoformat(tr["start_time"]).timestamp()
        code = tr["http_status_code"]
        logs.append({
            "ts": tr["start_time"], "level": "INFO" if code < 500 else "ERROR",
            "service": "nginx-lb-01", "instance": "prod-app-01:80",
            "category": "access", "logger": "nginx.access",
            "message": f'{tr["name"]} {code} {tr["duration_ms"]:.0f}ms '
                       f'trace_id={tr["trace_id"]}',
            "trace_id": tr["trace_id"], "span_id": tr["spans"][0]["span_id"],
            "fields": {"status": code, "upstream": tr["service"],
                       "request_time": round(tr["duration_ms"] / 1000.0, 3)},
        })
    # 2) 应用日志：常态行 + 事故窗错误行（带 trace_id 与错误链路对齐）
    for minute in range(int(TRACE_WINDOW // 60)):
        t = t0 - minute * 60.0
        for svc in svc_names:
            base = SERVICES[svc]["base_qps"]
            # 分钟粒度抽样：QPS 越高行数越多（1~3 行/分钟），总量约 5 万行
            p = min(base / 40.0, 0.9)
            k = 1 if rng.random() < p else 0
            if rng.random() < p * 0.7:
                k += 1
            if rng.random() < p * 0.35:
                k += 1
            for _ in range(k):
                msg = rng.choice(_NORMAL_MSGS).format(
                    ms=rng.randint(12, 320), oid=rng.randint(100000, 999999),
                    uid=rng.randint(1000, 9999), sku=f"SKU-{rng.randint(100, 999)}",
                    a=rng.randint(20, 80), i=rng.randint(1, 30), n=rng.randint(1, 8))
                logs.append({
                    "ts": iso(t), "level": "DEBUG" if rng.random() < 0.15 else "INFO",
                    "service": svc,
                    "instance": rng.choice(SERVICES[svc]["instances"])[0],
                    "category": "app", "logger": _APP_LOGGERS[svc], "message": msg,
                    "trace_id": "", "span_id": "", "fields": {},
                })
        # 事故窗错误/告警日志：挂在当时错误 trace 上
        if _in_incident(t):
            err_traces = [tr for tr in traces if tr["status"] == "error"
                          and abs(datetime.fromisoformat(tr["start_time"]).timestamp() - t) < 180]
            for tr in err_traces[:3]:
                level, logger, tmpl = rng.choice(_ERROR_MSGS_INCIDENT)
                svc = "payment-svc" if "pay" in logger else "order-svc"
                logs.append({
                    "ts": iso(t + rng.random() * 3), "level": level, "service": svc,
                    "instance": rng.choice(SERVICES[svc]["instances"])[0],
                    "category": "app", "logger": logger,
                    "message": tmpl.format(oid=rng.randint(100000, 999999),
                                           ms=rng.randint(800, 2900),
                                           n=rng.randint(1, 3)),
                    "trace_id": tr["trace_id"], "span_id": tr["spans"][1]["span_id"]
                    if len(tr["spans"]) > 1 else "", "fields": {"thread": "http-nio-8080-exec-"
                                                                            f"{rng.randint(1, 20)}"},
                })
    # 3) 慢 SQL：常态偶发，事故窗内密集
    for minute in range(int(TRACE_WINDOW // 60)):
        t = t0 - minute * 60.0
        dense = _in_incident(t)
        p = 0.35 if dense else 0.02
        if rng.random() < p:
            logs.append({
                "ts": iso(t), "level": "WARN" if dense else "INFO",
                "service": "mysql-order-01", "instance": "prod-db-01:3306",
                "category": "slow_sql", "logger": "mysql.slow_query",
                "message": f'SELECT o.id, o.status, o.amount FROM payment_order o '
                           f'WHERE o.user_id = ? ORDER BY o.created_at DESC LIMIT 20 '
                           f'[query_time={rng.uniform(1.2, 4.8):.2f}s rows_examined='
                           f'{rng.randint(18000, 420000)}]',
                "trace_id": "", "span_id": "",
                "fields": {"schema": "order_db", "host": "mysql-order-01"},
            })
    # 4) 系统日志：主机/网络设备少量行
    host_names = list(HOSTS.keys())
    for minute in range(0, int(TRACE_WINDOW // 60), 10):
        t = t0 - minute * 60.0
        host = rng.choice(host_names)
        logs.append({
            "ts": iso(t), "level": "INFO", "service": host, "instance": host,
            "category": "system", "logger": "systemd", "message":
                rng.choice(["session-2041.scope: Succeeded",
                            "Started Session 2042 of user deploy.",
                            "logrotate.service: Succeeded",
                            "systemd-journald: Journal started"]),
            "trace_id": "", "span_id": "", "fields": {},
        })
    # core-sw-01 事故（CRC）伴随的端口错误日志
    for k in range(12):
        t = rel_time(datetime.fromtimestamp(t0, SH_TZ), INCIDENT["switch_crc"]) + k * 240
        logs.append({
            "ts": iso(t), "level": "WARN", "service": "core-sw-01",
            "instance": "10.20.0.11", "category": "system", "logger": "ifmgr",
            "message": f"Interface Gi1/0/17 input CRC errors rising, "
                       f"ifInErrors={1200 + k * 37}",
            "trace_id": "", "span_id": "",
            "fields": {"interface": "Gi1/0/17", "device": "core-sw-01"},
        })
    logs.sort(key=lambda l: l["ts"])
    return logs


# ---- 告警 ----

def _gen_alerts(t0: float) -> list[dict]:
    """告警实例：事故窗 6 条 + 近 7 天历史 5 条，Alertmanager 对象形态。"""

    def alert(rule_name: str, start_off: float, end_off: float | None, service: str,
              instance: str, summary: str, desc: str) -> dict:
        rule = next(r for r in ALERT_RULES if r["name"] == rule_name)
        starts = rel_time(datetime.fromtimestamp(t0, SH_TZ), start_off)
        ends = None if end_off is None else rel_time(
            datetime.fromtimestamp(t0, SH_TZ), end_off)
        return {
            "fingerprint": _hex(f"alert:{rule_name}:{int(starts)}", 16),
            "status": {"state": "firing" if ends is None else "resolved"},
            "labels": {"alertname": rule_name, "severity": rule["severity"],
                       "service": service, "env": "prod", "instance": instance,
                       "job": service},
            "annotations": {"summary": summary, "description": desc,
                            "runbook": f"https://runbook.internal/{rule_name.lower()}"},
            "startsAt": iso(starts), "endsAt": None if ends is None else iso(ends),
            "generatorURL": "http://prometheus.internal/graph?g0.expr=" + rule_name,
            "rule": {"expr": rule["expr"], "for": rule["for"],
                     "severity": rule["severity"]},
            "acknowledged": False, "acknowledged_note": "",
        }

    inc = INCIDENT
    return [
        alert("PaymentErrorRateHigh", inc["alert_fire"], inc["recover"], "payment-svc",
              "payment-svc:prod",
              "支付服务 5xx 比例 12.4%（阈值 5%）",
              "payment-svc 错误率自 v3.2.1 发布后快速攀升，持续 12 分钟，回滚 v3.2.0 后恢复"),
        alert("MySQLSlowQueriesHigh", inc["alert_fire"] - 60, inc["recover"] + 120,
              "mysql-order-01", "mysql-order-01:3306",
              "订单库慢查询 0.35 条/s（阈值 0.1）",
              "payment_order 表全表扫描增多，与支付服务连接池打满同窗出现"),
        alert("MySQLConnectionsHigh", inc["alert_fire"], inc["recover"], "mysql-order-01",
              "mysql-order-01:3306", "订单库连接数 372/400（阈值 80%）",
              "连接数逼近 max_connections，等待队列排队导致获取连接超时"),
        alert("OrderTimeoutSpike", inc["alert_fire"] + 90, inc["recover"] - 60,
              "order-svc", "order-svc:prod",
              "订单服务 P99 延迟 2.9s（阈值 1s）",
              "支付服务超时级联导致订单创建链路变慢"),
        alert("KafkaConsumerLagHigh", inc["alert_fire"] + 120, inc["recover"] + 360,
              "notify-svc", "notify-orders", "通知消费组积压 22k 条（阈值 10k）",
              "订单事件产生速率超过通知服务消费能力，恢复后逐步追平"),
        alert("RedisMemoryHigh", inc["redis_warn"], None, "redis-cache-01",
              "redis-cache-01:6379", "缓存主节点内存 85.1%（阈值 85%）",
              "会话与热点库存键持续增长，建议排查大键并调整 maxmemory-policy"),
        alert("SwitchPortCRCErrors", inc["switch_crc"], None, "core-sw-01",
              "core-sw-01:Gi1/0/17", "核心交换机 Gi1/0/17 CRC 错误持续增长",
              "端口误码上升，怀疑光模块/光纤劣化，建议环回测试并更换光模块"),
        # 历史告警（已恢复）
        alert("HostDiskUsageHigh", -2 * 86400.0 - 5 * 3600.0, -2 * 86400.0,
              "prod-app-06", "prod-app-06:/", "prod-app-06 根分区剩余 13%",
              "日志与临时文件增长过快，清理后恢复"),
        alert("Nginx5xxRateHigh", -4 * 86400.0 - 2 * 3600.0, -4 * 86400.0,
              "nginx-lb-01", "nginx-lb-01:80", "七层负载 5xx 速率 1.4/s",
              "上游 search-svc 发布抖动导致 502，发布完成后恢复"),
        alert("CertExpiringSoon", -6 * 86400.0, -5 * 86400.0 - 6 * 3600.0,
              "lb-f5-01", "lb-f5-01:443", "对外证书剩余 14 天",
              "安全组完成证书续签并下发，告警自动解除"),
        alert("ServiceInstanceDown", -3 * 86400.0 - 4 * 3600.0, -3 * 86400.0 - 3 * 3600.0,
              "user-svc", "prod-app-02:8081", "user-svc prod-app-02 实例失联 2 分钟",
              "实例 OOM 重启，扩缩容后恢复"),
        alert("MySQLSlowQueriesHigh", -1 * 86400.0 - 7 * 3600.0, -1 * 86400.0 - 5 * 3600.0,
              "mysql-order-01", "mysql-order-01:3306", "订单库慢查询 0.15 条/s",
              "报表服务大查询拖慢从库，限流后恢复"),
    ]


# ---- 变更事件 ----

def _gen_events(t0: float, rng: random.Random) -> list[dict]:
    """近 7 天变更事件：发布/配置/扩缩容/回滚/故障切换，含事故因果链。"""

    t0_dt = datetime.fromtimestamp(t0, SH_TZ)
    events: list[dict] = []

    def ev(eid: str, offset: float, etype: str, title: str, desc: str, service: str,
           resource: str, operator: str, status: str, affected: list[str],
           ver_from: str = "", ver_to: str = "", ticket: str = "") -> dict:
        return {"event_id": eid, "ts": iso(rel_time(t0_dt, offset)), "type": etype,
                "title": title, "description": desc, "service": service,
                "resource": resource, "operator": operator, "status": status,
                "affected_cis": affected, "version_from": ver_from,
                "version_to": ver_to, "change_ticket": ticket}

    inc = INCIDENT
    events += [
        ev("evt-0001", inc["deploy_order"], "deploy", "order-svc 发布 v2.13.0",
           "订单服务常规迭代：订单列表分页优化、若干缺陷修复", "order-svc",
           "order-svc:v2.13.0", "李娜", "success",
           ["order-svc", "prod-app-03", "prod-app-04"], "v2.12.4", "v2.13.0",
           "CHG-20261113-006"),
        ev("evt-0002", inc["deploy_payment"], "deploy", "payment-svc 发布 v3.2.1",
           "支付服务发布：新增渠道容错逻辑，调整数据库连接池参数", "payment-svc",
           "payment-svc:v3.2.1", "王强", "success",
           ["payment-svc", "prod-app-05", "prod-app-06"], "v3.2.0", "v3.2.1",
           "CHG-20261114-017"),
        ev("evt-0003", inc["rollback"], "rollback", "payment-svc 回滚至 v3.2.0",
           "发布后支付错误率快速攀升（PaymentErrorRateHigh 触发），执行版本回滚",
           "payment-svc", "payment-svc:v3.2.0", "王强", "rolled_back",
           ["payment-svc", "prod-app-05", "prod-app-06"], "v3.2.1", "v3.2.0",
           "CHG-20261114-018"),
        ev("evt-0004", inc["redis_warn"] + 300, "config_change",
           "redis-cache-01 maxmemory-policy 调整",
           "内存逼近阈值后将 allkeys-lru 调整为 allkeys-lfu 并观察", "redis-cache-01",
           "redis-cache-01:6379", "陈静", "success", ["redis-cache-01"], "", "",
           "CHG-20261115-002"),
        ev("evt-0005", inc["switch_crc"] + 600, "maintenance",
           "core-sw-01 Gi1/0/17 端口误码排查",
           "网管发现 CRC 错误增长，安排光纤环回测试（进行中）", "core-sw-01",
           "core-sw-01:Gi1/0/17", "周涛", "in_progress", ["core-sw-01"], "", "",
           "CHG-20261115-003"),
    ]
    # 常态变更噪音：近 7 天每日 1-2 个（staging 发布 / 扩缩容 / 配置）
    noise_pool = [
        ("deploy", "user-svc 发布 v4.1.0", "用户服务迭代：登录风控接入", "user-svc",
         "user-svc:v4.1.0", "陈静", "v4.0.9", "v4.1.0"),
        ("scale", "search-svc 扩容至 1 实例", "大促预热扩容", "search-svc",
         "search-svc:prod", "赵磊", "", ""),
        ("config_change", "nginx-lb upstream 权重调整", "按流量比例调整后端权重",
         "nginx-lb-01", "nginx-lb-01:80", "张伟", "", ""),
        ("deploy", "notify-svc 发布 v1.8.2", "通知模板渲染修复", "notify-svc",
         "notify-svc:v1.8.2", "张伟", "v1.8.1", "v1.8.2"),
        ("deploy", "report-svc 发布 v1.2.0", "报表导出支持大数据量分片", "report-svc",
         "report-svc:v1.2.0", "赵磊", "v1.1.7", "v1.2.0"),
        ("failover", "mysql-order 主从切换演练", "季度容灾演练，切换耗时 47s",
         "mysql-order-01", "mysql-order-01:3306", "陈静", "", ""),
        ("config_change", "kafka order-events 保留时长调整", "72h → 96h",
         "kafka-01", "kafka-01:9092", "张伟", "", ""),
    ]
    for i in range(9):
        offset = -(1.2 + i * 0.8 + rng.random() * 0.5) * 86400.0
        etype, title, desc, svc, res, op, vf, vt = noise_pool[i % len(noise_pool)]
        status = "failed" if rng.random() < 0.1 else "success"
        events.append(ev(f"evt-{100 + i}", offset, etype, title, desc, svc, res, op,
                         status, [svc], vf, vt,
                         f"CHG-202611{max(8, 15 - i)}-{100 + i}"))
    events.sort(key=lambda e: e["ts"])
    return events


def build_dataset(t0: datetime | None = None) -> Dataset:
    """构建数据集；不传 t0 时锚定当前时刻。

    同步把 world.ANCHOR 指到同一时间锚：series 指标的事故窗与
    dataset 的链路/日志/告警必须落在同一时间线。
    """

    import world as _world

    import series as _series

    ds_t0 = t0 or datetime.now(SH_TZ).replace(tzinfo=SH_TZ)
    _world.ANCHOR = ds_t0.timestamp()
    _series._CKPT.clear()  # 时间锚变了，counter 积分检查点必须重算
    return Dataset(ds_t0)


# 服务进程级单例：server.py 与测试共用同一份内存数据
DATA = build_dataset()
