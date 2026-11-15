"""指标时间序列：确定性函数生成，查询时按 step 抽样，不落点存储。

value(metric, labels, t) = 昼夜节律 + 平滑伪噪声 + 事故窗口叠加；
counter 维护累计值（对速率做分段积分 + 检查点缓存），同一输入永远同一输出。
"""

from __future__ import annotations

import bisect
import math

from world import (
    CUM_BASE,
    abs_offset,
    DURATION_LE_BUCKETS,
    HOSTS,
    INCIDENT,
    METRIC_SPECS,
    SERVICES,
    diurnal,
    incident_factor,
    smooth_noise,
)

# counter 检查点缓存：series_key -> (checkpoints_min 列表, cum 列表)
_CHECKPOINT_STEP = 300.0  # 5 分钟一个积分检查点
_CKPT: dict[tuple, tuple[list[float], list[float]]] = {}


def _series_key(metric: str, labels: dict) -> tuple:
    return (metric, tuple(sorted(labels.items())))


def _svc_of(labels: dict) -> str:
    return labels.get("service", "")


def _traffic_rate(t: float, service: str) -> float:
    """服务瞬时 QPS：基线 × 昼夜 × 噪声。"""

    meta = SERVICES.get(service)
    if meta is None:
        return 0.0
    rate = meta["base_qps"] * diurnal(t) * (1.0 + smooth_noise(f"qps:{service}", t, 0.08))
    return max(rate, 0.05)


def _error_ratio(t: float, service: str) -> float:
    """5xx 比例：常态约 0.2%，事故窗内 payment-svc 冲到 12%，order-svc 级联到 4%。"""

    base = 0.002 + max(0.0, smooth_noise(f"err:{service}", t, 0.001))
    f = incident_factor(t, abs_offset(INCIDENT["degrade_start"] + 300),
                        abs_offset(INCIDENT["alert_fire"]), abs_offset(INCIDENT["recover"]))
    if f <= 0:
        return base
    if service == "payment-svc":
        return base + f * 0.12
    if service == "order-svc":
        return base + f * 0.04
    return base


def _latency_p50(t: float, service: str) -> float:
    """请求 P50 延迟（秒）：基线 × 昼夜微涨 × 事故窗放大（payment 8 倍 / order 3 倍）。"""

    meta = SERVICES.get(service)
    if meta is None:
        return 0.01
    p50 = meta["latency_median_ms"] / 1000.0
    p50 *= 1.0 + 0.12 * (diurnal(t) - 1.0) + smooth_noise(f"lat:{service}", t, 0.10)
    f = incident_factor(t, abs_offset(INCIDENT["degrade_start"] + 300),
                        abs_offset(INCIDENT["alert_fire"]), abs_offset(INCIDENT["recover"]))
    if f > 0:
        if service == "payment-svc":
            p50 *= 1.0 + f * 7.0
        elif service == "order-svc":
            p50 *= 1.0 + f * 2.0
    return max(p50, 0.002)


def _histogram_frac_under(service: str, le: float, t: float) -> float:
    """对数正态近似：P(latency <= le)。sigma 固定 0.9，中位数即 P50。"""

    mu = math.log(max(_latency_p50(t, service), 1e-4))
    z = (math.log(max(le, 1e-6)) - mu) / 0.9
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _rate_at(metric: str, labels: dict, t: float) -> float:
    """counter 的瞬时增速（每秒增量）；带 instance 标签时按实例均摊。"""

    service = _svc_of(labels)
    inst_share = 1.0
    if "instance" in labels and service in SERVICES:
        n = max(len(SERVICES[service]["instances"]), 1)
        inst_share = 1.0 / n
    if metric == "http_requests_total":
        route = labels.get("route", "")
        code = labels.get("code", "200")
        route_share = SERVICES.get(service, {}).get("routes", {}).get(route, 0.0)
        rate = _traffic_rate(t, service) * route_share * inst_share
        if code.startswith("5"):
            rate *= _error_ratio(t, service)
        elif code == "200":
            rate *= 1.0 - _error_ratio(t, service) - 0.01
        else:
            rate *= 0.01
        return max(rate, 0.0)
    if metric == "http_request_duration_seconds_count":
        route = labels.get("route", "")
        route_share = SERVICES.get(service, {}).get("routes", {}).get(route, 0.0)
        return max(_traffic_rate(t, service) * route_share, 0.0)
    if metric == "http_request_duration_seconds_sum":
        route = labels.get("route", "")
        route_share = SERVICES.get(service, {}).get("routes", {}).get(route, 0.0)
        return max(_traffic_rate(t, service) * route_share * _latency_p50(t, service) * 1.35, 0.0)
    if metric == "http_request_duration_seconds_bucket":
        le = float(labels.get("le", "10"))
        route = labels.get("route", "")
        route_share = SERVICES.get(service, {}).get("routes", {}).get(route, 0.0)
        frac = _histogram_frac_under(service, le, t)
        return max(_traffic_rate(t, service) * route_share * frac, 0.0)
    if metric == "mysql_slow_queries_total":
        # 慢查询增速：常态 0.003/s，事故窗内 mysql-order-01 涨到 0.35/s
        inst = labels.get("instance", "")
        rate = 0.003 if inst == "mysql-order-01" else 0.001
        f = incident_factor(t, abs_offset(INCIDENT["degrade_start"] + 300),
                            abs_offset(INCIDENT["alert_fire"]), abs_offset(INCIDENT["recover"]))
        if inst == "mysql-order-01" and f > 0:
            rate += f * 0.35
        return rate
    if metric == "node_cpu_seconds_total":
        mode = labels.get("mode", "idle")
        share = {"user": 0.22, "system": 0.06, "iowait": 0.01, "idle": 0.71}.get(mode, 0.0)
        inst = labels.get("instance", "")
        cores = HOSTS.get(inst, {}).get("cpu_cores", 16)
        return cores * share * (0.7 + 0.3 * diurnal(t))
    return 0.0


def _gauge_value(metric: str, labels: dict, t: float) -> float:
    """gauge 瞬时值。"""

    service = _svc_of(labels)
    if metric == "up":
        return 1.0
    if metric == "process_resident_memory_bytes":
        meta = SERVICES.get(service, {})
        base = 420e6 + meta.get("base_qps", 10) * 6e6
        return base * (1.0 + 0.08 * (diurnal(t) - 1.0) + smooth_noise(f"rss:{service}", t, 0.05))
    if metric == "go_goroutines":
        meta = SERVICES.get(service, {})
        base = 40 + meta.get("base_qps", 10) * 4
        f = incident_factor(t, abs_offset(INCIDENT["degrade_start"] + 300),
                            abs_offset(INCIDENT["alert_fire"]), abs_offset(INCIDENT["recover"]))
        if service in ("payment-svc", "order-svc"):
            base *= 1.0 + f * 3.0  # 事故期协程堆积
        return base * (1.0 + smooth_noise(f"gr:{service}", t, 0.12))
    if metric == "mysql_threads_connected":
        inst = labels.get("instance", "")
        base = 120.0 if inst == "mysql-order-01" else 30.0
        f = incident_factor(t, abs_offset(INCIDENT["degrade_start"] + 300),
                            abs_offset(INCIDENT["alert_fire"]), abs_offset(INCIDENT["recover"]))
        if inst == "mysql-order-01":
            base += f * 250  # 连接池打满
        return base * (1.0 + smooth_noise(f"conn:{inst}", t, 0.08))
    if metric == "redis_memory_used_bytes":
        inst = labels.get("instance", "")
        if inst == "redis-cache-01":
            # 主节点内存持续走高：约 14.7G/16.0G（85% 线上方），RedisMemoryHigh 同款形状
            return 14.7e9 + smooth_noise("redis01", t, 0.05e9)
        return 10.4e9 * (1.0 + smooth_noise(f"redis:{inst}", t, 0.02))
    if metric == "redis_connected_clients":
        inst = labels.get("instance", "")
        base = 260.0 if inst == "redis-cache-01" else 80.0
        return base * (1.0 + 0.15 * (diurnal(t) - 1.0) + smooth_noise(f"rc:{inst}", t, 0.1))
    if metric == "node_memory_MemAvailable_bytes":
        inst = labels.get("instance", "")
        total = HOSTS.get(inst, {}).get("memory_gb", 64) * 1024 ** 3
        return total * (0.42 + 0.06 * (1 - diurnal(t)) + smooth_noise(f"mem:{inst}", t, 0.03))
    if metric == "node_filesystem_avail_bytes":
        inst = labels.get("instance", "")
        disk = HOSTS.get(inst, {}).get("disk_gb", 200) * 1024 ** 3
        if inst == "prod-app-06":
            # 根分区持续走低：剩 ~12%（HostDiskUsageHigh 告警同款形状）
            return disk * (0.12 + smooth_noise("disk06", t, 0.004))
        return disk * (0.58 + smooth_noise(f"disk:{inst}", t, 0.01))
    if metric == "kafka_consumer_lag":
        group = labels.get("consumer_group", "")
        base = 120.0 if group == "notify-orders" else 30.0
        f = incident_factor(t, abs_offset(INCIDENT["degrade_start"] + 300),
                            abs_offset(INCIDENT["alert_fire"]), abs_offset(INCIDENT["recover"] + 600))
        base += f * 22000  # 事故期订单事件积压
        return base * (1.0 + smooth_noise(f"lag:{group}", t, 0.2))
    return 0.0


def _counter_cum(metric: str, labels: dict, t: float) -> float:
    """counter 累计值：5 分钟检查点积分缓存 + 段内线性外推。"""

    key = _series_key(metric, labels)
    ck_min, ck_cum = _CKPT.get(key, ([], []))
    if not ck_min:
        # 首次构建：从 t-8d 起每 5 分钟一个检查点，足够覆盖任何查询窗
        t_anchor = t - 8 * 86400.0
        base = CUM_BASE.get(metric, 100_000.0)
        cur = base
        m = t_anchor
        ck_min.append(m)
        ck_cum.append(cur)
        while m < t + _CHECKPOINT_STEP:
            nxt = m + _CHECKPOINT_STEP
            # 梯形积分
            cur += (_rate_at(metric, labels, m) + _rate_at(metric, labels, nxt)) / 2.0 \
                * _CHECKPOINT_STEP
            m = nxt
            ck_min.append(m)
            ck_cum.append(cur)
        _CKPT[key] = (ck_min, ck_cum)
    i = bisect.bisect_right(ck_min, t) - 1
    i = max(0, min(i, len(ck_min) - 2))
    frac = (t - ck_min[i]) / (ck_min[i + 1] - ck_min[i])
    return ck_cum[i] + (ck_cum[i + 1] - ck_cum[i]) * frac


def value(metric: str, labels: dict, t: float) -> float:
    """单点取值入口：counter 给累计值，gauge 给瞬时值。"""

    spec = METRIC_SPECS.get(metric)
    if spec is None:
        raise KeyError(f"未知指标: {metric}")
    if spec["type"] == "counter":
        return round(_counter_cum(metric, labels, t), 4)
    return round(_gauge_value(metric, labels, t), 4)


def rate(metric: str, labels: dict, t: float) -> float:
    """counter 的瞬时速率（query_metric rate=true 时用）。"""

    return round(_rate_at(metric, labels, t), 6)


def le_buckets() -> tuple[str, ...]:
    return DURATION_LE_BUCKETS


_POST_ROUTES = ("create", "charge", "refund", "reserve", "send", "login", "export")


def known_series(metric: str) -> list[dict]:
    """该指标可查询的标签组合枚举（列表/过滤用）。"""

    out: list[dict] = []
    if metric == "http_requests_total":
        for svc, meta in SERVICES.items():
            for route in meta["routes"]:
                method = "POST" if any(k in route for k in _POST_ROUTES) else "GET"
                for inst, _host in meta["instances"]:
                    for code in ("200", "404", "500", "504"):
                        out.append({"service": svc, "env": "prod", "instance": inst,
                                    "method": method, "code": code, "route": route})
    elif metric in ("http_request_duration_seconds_count", "http_request_duration_seconds_sum"):
        for svc, meta in SERVICES.items():
            for route in meta["routes"]:
                out.append({"service": svc, "env": "prod", "route": route})
    elif metric == "http_request_duration_seconds_bucket":
        for svc, meta in SERVICES.items():
            for route in meta["routes"]:
                for le in DURATION_LE_BUCKETS:
                    out.append({"service": svc, "env": "prod", "route": route, "le": le})
    elif metric in ("process_resident_memory_bytes", "go_goroutines"):
        for svc, meta in SERVICES.items():
            for inst, _host in meta["instances"]:
                out.append({"service": svc, "env": "prod", "instance": inst})
    elif metric in ("mysql_threads_connected", "mysql_slow_queries_total"):
        out = [{"instance": "mysql-order-01"}, {"instance": "mysql-order-02"}]
    elif metric in ("redis_memory_used_bytes", "redis_connected_clients"):
        out = [{"instance": "redis-cache-01"}, {"instance": "redis-cache-02"}]
    elif metric == "node_cpu_seconds_total":
        for host in HOSTS:
            for mode in ("user", "system", "iowait", "idle"):
                out.append({"instance": host, "mode": mode})
    elif metric == "node_memory_MemAvailable_bytes":
        out = [{"instance": h} for h in HOSTS]
    elif metric == "node_filesystem_avail_bytes":
        out = [{"instance": h, "mountpoint": "/"} for h in HOSTS]
    elif metric == "kafka_consumer_lag":
        out = [{"consumer_group": "notify-orders", "topic": "order-events"},
               {"consumer_group": "report-events", "topic": "order-events"}]
    elif metric == "up":
        for svc, meta in SERVICES.items():
            for inst, _host in meta["instances"]:
                out.append({"job": svc, "instance": inst, "env": "prod"})
    return out


def match_series(metric: str, want: dict) -> list[dict]:
    """按标签子集过滤可查询序列。"""

    return [s for s in known_series(metric)
            if all(s.get(k) == v for k, v in want.items())]
