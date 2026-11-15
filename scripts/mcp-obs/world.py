"""可观测 MCP 的虚构生产环境底座：CI 资产、服务拓扑、告警规则与事故时间线。

六域（指标/链路/日志/CMDB/告警/事件）共用这一份世界观，事故窗口统一锚定
启动时刻 T0 的相对偏移，保证跨域数据能互相印证。
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

# 输出统一用 +08:00 展示（产品面向中文环境）
SH_TZ = timezone(timedelta(hours=8))

HOUR = 3600.0
MINUTE = 60.0

# ---- 事故剧本时间锚（相对 T0 的秒偏移，负数=过去） ----
INCIDENT = {
    "deploy_order": -30 * HOUR,          # order-svc v2.13.0 正常发布
    "deploy_payment": -8 * HOUR,         # payment-svc v3.2.1 发布
    "degrade_start": -7 * HOUR - 55 * MINUTE,  # 错误率开始上升
    "alert_fire": -7 * HOUR - 52 * MINUTE,     # PaymentErrorRateHigh 触发
    "rollback": -7 * HOUR - 50 * MINUTE,       # 回滚 v3.2.0
    "recover": -7 * HOUR - 40 * MINUTE,        # 指标恢复、告警解除
    "redis_warn": -3 * HOUR,             # redis-cache 内存告警触发（至今未恢复）
    "switch_crc": -1 * HOUR,             # core-sw-01 端口 CRC 告警触发（至今未恢复）
}

# ---- 服务目录（prod 两实例，user/order 另有 staging 单实例） ----
SERVICES: dict[str, dict] = {
    "gateway": {
        "display": "接入网关", "team": "平台组", "owner": "张伟",
        "repo": "git@git.internal:platform/gateway.git", "version": "v6.0.1",
        "sla": "99.99%", "language": "Go",
        "base_qps": 42.0, "latency_median_ms": 12.0,
        "routes": {
            "/api/orders": 0.32, "/api/payments": 0.18, "/api/users": 0.24,
            "/api/search": 0.16, "/api/inventory": 0.10,
        },
        "instances": [("prod-app-01:8080", "prod-app-01"), ("prod-app-02:8080", "prod-app-02")],
    },
    "user-svc": {
        "display": "用户服务", "team": "用户组", "owner": "陈静",
        "repo": "git@git.internal:user/user-svc.git", "version": "v4.1.0",
        "sla": "99.95%", "language": "Java",
        "base_qps": 24.0, "latency_median_ms": 25.0,
        "routes": {"/users/login": 0.35, "/users/{id}": 0.45, "/users/profile": 0.20},
        "instances": [("prod-app-01:8081", "prod-app-01"), ("prod-app-02:8081", "prod-app-02")],
    },
    "order-svc": {
        "display": "订单服务", "team": "交易组", "owner": "李娜",
        "repo": "git@git.internal:trade/order-svc.git", "version": "v2.13.0",
        "sla": "99.95%", "language": "Java",
        "base_qps": 28.0, "latency_median_ms": 45.0,
        "routes": {"/orders/create": 0.22, "/orders/{id}": 0.53, "/orders/list": 0.25},
        "instances": [("prod-app-03:8080", "prod-app-03"), ("prod-app-04:8080", "prod-app-04")],
    },
    "payment-svc": {
        "display": "支付服务", "team": "支付组", "owner": "王强",
        "repo": "git@git.internal:pay/payment-svc.git", "version": "v3.2.0",
        "sla": "99.99%", "language": "Java",
        "base_qps": 18.0, "latency_median_ms": 120.0,
        "routes": {"/payments/charge": 0.46, "/payments/refund": 0.14, "/payments/{id}": 0.40},
        "instances": [("prod-app-05:8080", "prod-app-05"), ("prod-app-06:8080", "prod-app-06")],
    },
    "inventory-svc": {
        "display": "库存服务", "team": "交易组", "owner": "李娜",
        "repo": "git@git.internal:trade/inventory-svc.git", "version": "v2.0.3",
        "sla": "99.90%", "language": "Go",
        "base_qps": 12.0, "latency_median_ms": 35.0,
        "routes": {"/inventory/stock": 0.62, "/inventory/reserve": 0.38},
        "instances": [("prod-app-03:8081", "prod-app-03"), ("prod-app-04:8081", "prod-app-04")],
    },
    "notify-svc": {
        "display": "通知服务", "team": "平台组", "owner": "张伟",
        "repo": "git@git.internal:platform/notify-svc.git", "version": "v1.8.2",
        "sla": "99.50%", "language": "Go",
        "base_qps": 8.0, "latency_median_ms": 15.0,
        "routes": {"/notify/send": 0.70, "/notify/templates": 0.30},
        "instances": [("prod-app-04:8082", "prod-app-04")],
    },
    "search-svc": {
        "display": "搜索服务", "team": "数据组", "owner": "赵磊",
        "repo": "git@git.internal:data/search-svc.git", "version": "v0.9.4",
        "sla": "99.50%", "language": "Go",
        "base_qps": 15.0, "latency_median_ms": 80.0,
        "routes": {"/search/query": 0.78, "/search/suggest": 0.22},
        "instances": [("prod-app-05:8081", "prod-app-05")],
    },
    "report-svc": {
        "display": "报表服务", "team": "数据组", "owner": "赵磊",
        "repo": "git@git.internal:data/report-svc.git", "version": "v1.2.0",
        "sla": "99.00%", "language": "Python",
        "base_qps": 2.0, "latency_median_ms": 400.0,
        "routes": {"/report/daily": 0.55, "/report/export": 0.45},
        "instances": [("prod-app-06:8081", "prod-app-06")],
    },
}

# ---- 主机 ----
HOSTS: dict[str, dict] = {
    "prod-app-01": {"ip": "10.20.1.11", "os": "Ubuntu 22.04.3 LTS", "cpu_cores": 16,
                    "memory_gb": 64, "disk_gb": 200, "idc": "北京-亦庄-A", "owner": "张伟",
                    "role": "应用服务器", "env": "prod"},
    "prod-app-02": {"ip": "10.20.1.12", "os": "Ubuntu 22.04.3 LTS", "cpu_cores": 16,
                    "memory_gb": 64, "disk_gb": 200, "idc": "北京-亦庄-A", "owner": "张伟",
                    "role": "应用服务器", "env": "prod"},
    "prod-app-03": {"ip": "10.20.1.13", "os": "Ubuntu 22.04.3 LTS", "cpu_cores": 16,
                    "memory_gb": 64, "disk_gb": 200, "idc": "北京-亦庄-B", "owner": "李娜",
                    "role": "应用服务器", "env": "prod"},
    "prod-app-04": {"ip": "10.20.1.14", "os": "Ubuntu 22.04.3 LTS", "cpu_cores": 16,
                    "memory_gb": 64, "disk_gb": 200, "idc": "北京-亦庄-B", "owner": "李娜",
                    "role": "应用服务器", "env": "prod"},
    "prod-app-05": {"ip": "10.20.1.15", "os": "Ubuntu 22.04.3 LTS", "cpu_cores": 32,
                    "memory_gb": 128, "disk_gb": 400, "idc": "北京-亦庄-A", "owner": "王强",
                    "role": "应用服务器", "env": "prod"},
    "prod-app-06": {"ip": "10.20.1.16", "os": "Ubuntu 22.04.3 LTS", "cpu_cores": 32,
                    "memory_gb": 128, "disk_gb": 400, "idc": "北京-亦庄-A", "owner": "王强",
                    "role": "应用服务器", "env": "prod"},
    "prod-db-01": {"ip": "10.20.2.11", "os": "CentOS 7.9", "cpu_cores": 32,
                   "memory_gb": 256, "disk_gb": 2000, "idc": "北京-亦庄-A", "owner": "陈静",
                   "role": "数据库服务器", "env": "prod"},
    "prod-db-02": {"ip": "10.20.2.12", "os": "CentOS 7.9", "cpu_cores": 32,
                   "memory_gb": 256, "disk_gb": 2000, "idc": "北京-亦庄-B", "owner": "陈静",
                   "role": "数据库服务器", "env": "prod"},
    "staging-app-01": {"ip": "10.30.1.11", "os": "Ubuntu 22.04.3 LTS", "cpu_cores": 8,
                       "memory_gb": 32, "disk_gb": 100, "idc": "测试区", "owner": "张伟",
                       "role": "应用服务器", "env": "staging"},
    "staging-app-02": {"ip": "10.30.1.12", "os": "Ubuntu 22.04.3 LTS", "cpu_cores": 8,
                       "memory_gb": 32, "disk_gb": 100, "idc": "测试区", "owner": "张伟",
                       "role": "应用服务器", "env": "staging"},
}

# ---- 中间件 CI ----
MIDDLEWARE: dict[str, dict] = {
    "mysql-order-01": {"display": "订单库主库", "kind": "database", "product": "MySQL 8.0.32",
                       "host": "prod-db-01", "port": 3306, "role": "primary", "owner": "陈静",
                       "detail": "订单/支付核心库，innodb_buffer_pool=64G，max_connections=400"},
    "mysql-order-02": {"display": "订单库从库", "kind": "database", "product": "MySQL 8.0.32",
                       "host": "prod-db-02", "port": 3306, "role": "replica", "owner": "陈静",
                       "detail": "读写分离从库，供 search-svc / report-svc 查询"},
    "redis-cache-01": {"display": "缓存主节点", "kind": "cache", "product": "Redis 7.2.4",
                       "host": "prod-db-01", "port": 6379, "role": "primary", "owner": "陈静",
                       "detail": "maxmemory=16GB，allkeys-lru；会话与热点库存缓存"},
    "redis-cache-02": {"display": "缓存从节点", "kind": "cache", "product": "Redis 7.2.4",
                       "host": "prod-db-02", "port": 6379, "role": "replica", "owner": "陈静",
                       "detail": "主从复制，故障时手工提升"},
    "kafka-01": {"display": "消息队列节点 1", "kind": "mq", "product": "Kafka 3.6.1",
                 "host": "prod-app-05", "port": 9092, "role": "broker", "owner": "张伟",
                 "detail": "订单事件主题 order-events，保留 72h（混部部署）"},
    "kafka-02": {"display": "消息队列节点 2", "kind": "mq", "product": "Kafka 3.6.1",
                 "host": "prod-app-06", "port": 9092, "role": "broker", "owner": "张伟",
                 "detail": "order-events 副本节点（混部部署）"},
    "nginx-lb-01": {"display": "七层负载 1", "kind": "lb", "product": "Nginx 1.24.0",
                    "host": "prod-app-01", "port": 80, "role": "active", "owner": "张伟",
                    "detail": "upstream 分发到各服务实例"},
    "nginx-lb-02": {"display": "七层负载 2", "kind": "lb", "product": "Nginx 1.24.0",
                    "host": "prod-app-02", "port": 80, "role": "standby", "owner": "张伟",
                    "detail": "keepalived 备节点"},
}

# ---- 网络设备 ----
NETWORK_DEVICES: dict[str, dict] = {
    "core-sw-01": {"display": "核心交换机 1", "product": "华为 CE6865-48S8CQ-EI",
                   "mgmt_ip": "10.20.0.11", "idc": "北京-亦庄-A", "owner": "周涛",
                   "role": "核心层", "os_version": "V200R022C00",
                   "detail": "连接 prod-app/生产网段；Gi1/0/17 上联防火墙"},
    "core-sw-02": {"display": "核心交换机 2", "product": "华为 CE6865-48S8CQ-EI",
                   "mgmt_ip": "10.20.0.12", "idc": "北京-亦庄-B", "owner": "周涛",
                   "role": "核心层", "os_version": "V200R022C00",
                   "detail": "与 core-sw-01 堆叠互联，双活网关"},
    "edge-fw-01": {"display": "边界防火墙", "product": "华为 USG6650E",
                   "mgmt_ip": "10.20.0.2", "idc": "北京-亦庄-A", "owner": "周涛",
                   "role": "边界安全", "os_version": "V500R005C10",
                   "detail": "南北向出入策略，与 lb-f5-01 串联"},
    "lb-f5-01": {"display": "全局负载均衡", "product": "F5 BIG-IP i4800",
                 "mgmt_ip": "10.20.0.3", "idc": "北京-亦庄-A", "owner": "周涛",
                 "role": "全局负载", "os_version": "17.1.0",
                 "detail": "对外 VIP 分发到 nginx-lb；证书由安全组统一签发"},
}

# ---- 关系边（CMDB 拓扑） ----
RELATIONS: list[dict] = []


def _build_relations() -> None:
    for svc, meta in SERVICES.items():
        for _inst, host in meta["instances"]:
            RELATIONS.append({"from": svc, "type": "runs_on", "to": host,
                              "note": f"{meta['display']}实例部署"})
    svc_deps = {
        "gateway": ["user-svc", "order-svc"],
        "order-svc": ["payment-svc", "inventory-svc", "notify-svc"],
        "payment-svc": ["mysql-order-01", "redis-cache-01"],
        "user-svc": ["mysql-order-01", "redis-cache-01"],
        "inventory-svc": ["mysql-order-01", "redis-cache-01"],
        "notify-svc": ["kafka-01", "mysql-order-01"],
        "search-svc": ["mysql-order-02"],
        "report-svc": ["mysql-order-02"],
    }
    for src, targets in svc_deps.items():
        for tgt in targets:
            RELATIONS.append({"from": src, "type": "depends_on", "to": tgt,
                              "note": "运行时调用/读写"})
    RELATIONS.append({"from": "mysql-order-01", "type": "replicates_to", "to": "mysql-order-02",
                      "note": "异步复制"})
    RELATIONS.append({"from": "redis-cache-01", "type": "replicates_to", "to": "redis-cache-02",
                      "note": "主从复制"})
    RELATIONS.append({"from": "kafka-01", "type": "replicates_to", "to": "kafka-02",
                      "note": "分区副本"})
    RELATIONS.append({"from": "order-svc", "type": "produces_to", "to": "kafka-01",
                      "note": "order-events 主题"})
    RELATIONS.append({"from": "nginx-lb-01", "type": "connects_to", "to": "lb-f5-01",
                      "note": "F5 下挂七层负载"})
    RELATIONS.append({"from": "lb-f5-01", "type": "connects_to", "to": "edge-fw-01",
                      "note": "南北向串联"})
    RELATIONS.append({"from": "edge-fw-01", "type": "connects_to", "to": "core-sw-01",
                      "note": "Gi1/0/17 上联"})
    RELATIONS.append({"from": "core-sw-01", "type": "connects_to", "to": "core-sw-02",
                      "note": "堆叠互联"})
    for host in HOSTS:
        if host.startswith("prod-app"):
            RELATIONS.append({"from": host, "type": "connects_to", "to": "core-sw-01",
                              "note": "接入生产交换机"})


_build_relations()

# ---- 告警规则（PromQL 形态，展示用） ----
ALERT_RULES: list[dict] = [
    {"name": "PaymentErrorRateHigh", "severity": "critical", "service": "payment-svc",
     "expr": 'sum(rate(http_requests_total{service="payment-svc",code=~"5.."}[5m])) '
             '/ sum(rate(http_requests_total{service="payment-svc"}[5m])) > 0.05',
     "for": "5m", "summary": "支付服务 5xx 比例超过 5%"},
    {"name": "OrderTimeoutSpike", "severity": "warning", "service": "order-svc",
     "expr": 'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket'
             '{service="order-svc"}[5m])) by (le)) > 1',
     "for": "3m", "summary": "订单服务 P99 延迟超过 1s"},
    {"name": "MySQLSlowQueriesHigh", "severity": "warning", "service": "mysql-order-01",
     "expr": 'rate(mysql_slow_queries_total{instance="mysql-order-01"}[5m]) > 0.1',
     "for": "5m", "summary": "订单库慢查询速率异常"},
    {"name": "MySQLConnectionsHigh", "severity": "warning", "service": "mysql-order-01",
     "expr": "mysql_threads_connected / 400 > 0.8", "for": "5m",
     "summary": "订单库连接数逼近上限"},
    {"name": "RedisMemoryHigh", "severity": "warning", "service": "redis-cache-01",
     "expr": "redis_memory_used_bytes{instance=\"redis-cache-01\"} / 17179869184 > 0.85",
     "for": "10m", "summary": "缓存主节点内存使用超过 85%"},
    {"name": "SwitchPortCRCErrors", "severity": "warning", "service": "core-sw-01",
     "expr": 'rate(interface_crc_errors_total{device="core-sw-01",interface="Gi1/0/17"}[10m]) > 0',
     "for": "10m", "summary": "核心交换机端口 CRC 错误增长"},
    {"name": "HostDiskUsageHigh", "severity": "warning", "service": "prod-app-06",
     "expr": 'node_filesystem_avail_bytes{instance="prod-app-06",mountpoint="/"} '
             '/ node_filesystem_size_bytes < 0.15',
     "for": "15m", "summary": "主机根分区可用空间低于 15%"},
    {"name": "KafkaConsumerLagHigh", "severity": "warning", "service": "notify-svc",
     "expr": 'kafka_consumer_lag{consumer_group="notify-orders"} > 10000',
     "for": "5m", "summary": "通知服务消费组积压超过 1 万条"},
    {"name": "ServiceInstanceDown", "severity": "critical", "service": "user-svc",
     "expr": 'up{env="prod"} == 0', "for": "2m", "summary": "生产实例失联"},
    {"name": "Nginx5xxRateHigh", "severity": "warning", "service": "nginx-lb-01",
     "expr": 'rate(nginx_http_requests_total{status=~"5.."}[5m]) > 1',
     "for": "5m", "summary": "七层负载 5xx 比例升高"},
    {"name": "CertExpiringSoon", "severity": "info", "service": "lb-f5-01",
     "expr": "(probe_ssl_earliest_cert_expiry - time()) / 86400 < 15", "for": "1h",
     "summary": "对外证书 15 天内到期"},
]


# ---- 指标目录（Prometheus 命名惯例） ----
METRIC_SPECS: dict[str, dict] = {
    "up": {"type": "gauge", "unit": "", "help": "采集目标存活状态（1 存活 / 0 失联）",
           "labels": ["job", "instance", "env"]},
    "http_requests_total": {"type": "counter", "unit": "次",
                            "help": "HTTP 请求累计数（按服务/实例/方法/状态码/路由）",
                            "labels": ["service", "env", "instance", "method", "code", "route"]},
    "http_request_duration_seconds_count": {"type": "counter", "unit": "次",
                                            "help": "HTTP 请求延迟观测累计次数（直方图 count）",
                                            "labels": ["service", "env", "route"]},
    "http_request_duration_seconds_sum": {"type": "counter", "unit": "秒",
                                          "help": "HTTP 请求耗时累计（直方图 sum，除以 count 得均值）",
                                          "labels": ["service", "env", "route"]},
    "http_request_duration_seconds_bucket": {"type": "counter", "unit": "次",
                                             "help": "HTTP 请求延迟直方图分桶累计（le 标签为桶上界）",
                                             "labels": ["service", "env", "route", "le"]},
    "process_resident_memory_bytes": {"type": "gauge", "unit": "字节",
                                      "help": "服务进程常驻内存（RSS）",
                                      "labels": ["service", "env", "instance"]},
    "go_goroutines": {"type": "gauge", "unit": "个", "help": "Go 服务协程数",
                      "labels": ["service", "env", "instance"]},
    "mysql_threads_connected": {"type": "gauge", "unit": "个", "help": "MySQL 当前连接线程数",
                                "labels": ["instance"]},
    "mysql_slow_queries_total": {"type": "counter", "unit": "条", "help": "MySQL 慢查询累计数",
                                 "labels": ["instance"]},
    "redis_memory_used_bytes": {"type": "gauge", "unit": "字节", "help": "Redis 已用内存",
                                "labels": ["instance"]},
    "redis_connected_clients": {"type": "gauge", "unit": "个", "help": "Redis 客户端连接数",
                                "labels": ["instance"]},
    "node_cpu_seconds_total": {"type": "counter", "unit": "秒",
                               "help": "主机 CPU 各模式累计秒数（user/system/iowait/idle 等）",
                               "labels": ["instance", "mode"]},
    "node_memory_MemAvailable_bytes": {"type": "gauge", "unit": "字节",
                                       "help": "主机可用内存估算值",
                                       "labels": ["instance"]},
    "node_filesystem_avail_bytes": {"type": "gauge", "unit": "字节",
                                    "help": "文件系统剩余可用空间",
                                    "labels": ["instance", "mountpoint"]},
    "kafka_consumer_lag": {"type": "gauge", "unit": "条",
                           "help": "Kafka 消费组未消费消息条数（积压）",
                           "labels": ["consumer_group", "topic"]},
}

# 直方图桶上界（秒）
DURATION_LE_BUCKETS = ("0.01", "0.025", "0.05", "0.1", "0.25", "0.5", "1", "2.5", "5", "10")

# counter 累计起点的基数（模拟进程已运行较久）
CUM_BASE = {
    "http_requests_total": 12_400_000.0,
    "http_request_duration_seconds_count": 12_400_000.0,
    "http_request_duration_seconds_sum": 940_000.0,
    "http_request_duration_seconds_bucket": 12_400_000.0,
    "mysql_slow_queries_total": 48_200.0,
    "node_cpu_seconds_total": 9_800_000.0,
}


def diurnal(t: float) -> float:
    """昼夜流量节律：14:00 峰值、凌晨低谷，幅度 ±35%。"""

    dt = datetime.fromtimestamp(t, SH_TZ)
    hour = dt.hour + dt.minute / 60.0
    return 1.0 + 0.35 * math.sin(2 * math.pi * (hour - 14.0) / 24.0)


def smooth_noise(key: str, t: float, amp: float) -> float:
    """分钟级平滑伪噪声：相邻分钟哈希线性插值，保证同一输入永远同输出。"""

    import random

    b = t / 60.0
    i = int(b)
    f = b - i
    r1 = random.Random(f"{key}:{i}").random() * 2 - 1
    r2 = random.Random(f"{key}:{i + 1}").random() * 2 - 1
    return amp * (r1 * (1 - f) + r2 * f)


def incident_factor(t: float, start: float, peak: float, end: float) -> float:
    """事故叠加因子：start→peak 线性爬升，保持到 end 前 2 分钟，再线性回落。

    三个边界均为绝对 epoch 秒（用 abs_offset 从相对偏移换算）。
    """

    if t < start or t > end:
        return 0.0
    if t < peak:
        return (t - start) / max(peak - start, 1.0)
    decay_start = end - 120.0
    if t <= decay_start:
        return 1.0
    return max(0.0, (end - t) / 120.0)


# 时间锚：进程启动时刻。事故偏移（INCIDENT）是相对它的秒数，
# 与绝对 t 比较前必须经 abs_offset 换算。
ANCHOR: float = datetime.now(SH_TZ).timestamp()


def abs_offset(offset: float) -> float:
    """T0 相对偏移 → 绝对 epoch 秒。"""

    return ANCHOR + offset


def rel_time(t0: datetime, offset: float) -> float:
    """T0 相对偏移换算为 epoch 秒。"""

    return t0.timestamp() + offset
