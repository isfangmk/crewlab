"""可观测 MCP Server（SSE :18099）：指标 / 链路 / 日志 / CMDB / 告警 / 事件 六域查询。

数据为内置仿真数据集（dataset.py 启动期物化、series.py 确定性抽样），
事故剧本把六域串在同一时间线上，跨域可互相印证；不接真实监控后端。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.mcpserver import MCPServer

import queries
from dataset import DATA

queries.bind_dataset(DATA)

server = MCPServer(
    name="obs-tools",
    title="可观测平台",
    description="指标/链路/日志/CMDB/告警/事件查询（内置仿真数据，按真实生产形态生成）",
)


def _dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=1)


# ---- 指标 ----

@server.tool()
def list_metrics(query: str = "") -> str:
    """【指标】列出可用指标名（Prometheus 命名），支持按名称/帮助文本模糊过滤。返回指标类型、单位、标签键。"""
    return _dump(queries.list_metrics(query))


@server.tool()
def get_metric_metadata(metric: str) -> str:
    """【指标】查看单个指标的元数据：类型（counter/gauge）、单位、帮助文本、可查询标签组合示例。"""
    return _dump(queries.get_metric_metadata(metric))


@server.tool()
def query_metric(metric: str, labels: str = "", start: str = "", end: str = "",
                 step: str = "60s", rate: bool = False) -> str:
    """【指标】范围查询时间序列。labels 形如 service=payment-svc,code=500（子集匹配）；
    start/end 支持 ISO 8601 或 now-30m/now-2h/now-7d 相对式，缺省最近 1 小时；
    step 如 30s/5m；rate=true 时 counter 返回每秒速率而非累计值。返回多个序列的采样点。
    """
    return _dump(queries.query_metric(metric, labels, start, end, step, rate))


@server.tool()
def query_metric_instant(metric: str, labels: str = "", at: str = "") -> str:
    """【指标】瞬时值查询：取某时刻（缺省当前）各匹配序列的单点值，适合快速看现状。"""
    return _dump(queries.query_metric_instant(metric, labels, at))


@server.tool()
def get_service_overview(service: str, start: str = "", end: str = "") -> str:
    """【指标】单服务 RED 概览：QPS、错误率(%)、延迟 P50(ms)、实例与版本。服务名如 payment-svc。"""
    return _dump(queries.get_service_overview(service, start, end))


# ---- 链路 ----

@server.tool()
def search_traces(service: str = "", status: str = "", route: str = "",
                  min_duration_ms: float = 0, start: str = "", end: str = "",
                  limit: int = 20, offset: int = 0) -> str:
    """【链路】按服务/状态(ok|error)/路由/最小耗时检索分布式链路摘要（不含 span 明细）。
    缺省最近 24 小时，按开始时间倒序分页；status=error 可快速找异常调用。
    """
    return _dump(queries.search_traces(service, status, route, min_duration_ms,
                                       start, end, limit, offset))


@server.tool()
def get_trace(trace_id: str) -> str:
    """【链路】按 trace_id 取完整调用树：全部 span（含 trace_id/span_id/parent、
    http/db 属性、状态码、异常事件与堆栈）。trace_id 来自 search_traces 或日志里的关联字段。
    """
    return _dump(queries.get_trace(trace_id))


@server.tool()
def get_service_topology(start: str = "", end: str = "") -> str:
    """【链路】按链路数据聚合服务调用拓扑：服务间边的调用数、错误数、平均耗时。缺省最近 24 小时。"""
    return _dump(queries.get_service_topology(start, end))


@server.tool()
def get_trace_stats(service: str = "", start: str = "", end: str = "") -> str:
    """【链路】链路视角 RED 统计：调用数、错误数、错误率、耗时 P50/P95/P99/max。缺省最近 24 小时。"""
    return _dump(queries.get_trace_stats(service, start, end))


# ---- 日志 ----

@server.tool()
def search_logs(keyword: str = "", service: str = "", level: str = "",
                trace_id: str = "", start: str = "", end: str = "",
                limit: int = 50, offset: int = 0) -> str:
    """【日志】全文检索日志（access/app/slow_sql/system 四类），支持关键字、服务、
    级别(DEBUG|INFO|WARN|ERROR)、trace_id 过滤。缺省最近 1 小时，倒序分页。
    """
    return _dump(queries.search_logs(keyword, service, level, trace_id,
                                     start, end, limit, offset))


@server.tool()
def get_logs_by_trace(trace_id: str) -> str:
    """【日志】按 trace_id 取关联的全部日志行（链路↔日志关联排查用）。"""
    return _dump(queries.get_logs_by_trace(trace_id))


@server.tool()
def get_log_level_stats(service: str = "", start: str = "", end: str = "") -> str:
    """【日志】统计时间窗内各日志级别条数（判断是否有异常增多）。缺省最近 1 小时。"""
    return _dump(queries.get_log_level_stats(service, start, end))


# ---- CMDB ----

@server.tool()
def list_cis(type: str = "", env: str = "", owner: str = "") -> str:
    """【CMDB】列出配置项：service/host/database/cache/mq/lb/network_device 等类型，
    可按类型、环境(prod|staging)、负责人过滤。返回中含全部类型枚举。
    """
    return _dump(queries.list_cis(type, env, owner))


@server.tool()
def get_ci(name: str) -> str:
    """【CMDB】取单个配置项详情（主机/服务/中间件/网络设备）及其拓扑关系边（runs_on/depends_on 等）。name 如 prod-app-01、payment-svc、core-sw-01。"""
    return _dump(queries.get_ci(name))


@server.tool()
def search_cis(keyword: str) -> str:
    """【CMDB】按关键字模糊搜索配置项（名称/型号/负责人/IP/描述均可命中）。"""
    return _dump(queries.search_cis(keyword))


@server.tool()
def get_service_dependencies(service: str) -> str:
    """【CMDB】服务依赖关系：上游调用方、下游依赖（含中间件）、部署主机。"""
    return _dump(queries.get_service_dependencies(service))


# ---- 告警 ----

@server.tool()
def list_alerts(state: str = "", severity: str = "", service: str = "",
                start: str = "", end: str = "") -> str:
    """【告警】列出告警实例（Alertmanager 形态），可按状态(firing|resolved)、
    严重级(critical|warning|info)、服务过滤。缺省最近 24 小时，含标签/注解/起止时间/触发规则。
    """
    return _dump(queries.list_alerts(state, severity, service, start, end))


@server.tool()
def get_alert(fingerprint: str) -> str:
    """【告警】按 fingerprint 取告警详情：标签、注解、起止时间、触发规则表达式、认领状态。"""
    return _dump(queries.get_alert(fingerprint))


@server.tool()
def list_alert_rules(service: str = "", severity: str = "") -> str:
    """【告警】列出告警规则定义（PromQL 表达式、持续时间 for、严重级、摘要），可按服务/严重级过滤。"""
    return _dump(queries.list_alert_rules(service, severity))


@server.tool()
def acknowledge_alert(fingerprint: str, note: str = "") -> str:
    """【告警·写操作】认领一条告警并记录处理备注（内存态，重启失效）。用于值班确认接管。"""
    return _dump(queries.acknowledge_alert(fingerprint, note))


@server.tool()
def create_silence(matchers: str, duration_minutes: int = 60, comment: str = "") -> str:
    """【告警·写操作】创建告警静默：matchers 形如 alertname=RedisMemoryHigh,service=redis-cache-01，
    duration_minutes 静默时长（1~10080），comment 说明原因。返回命中的告警列表。"""
    return _dump(queries.create_silence(matchers, duration_minutes, comment))


# ---- 事件 ----

@server.tool()
def list_events(type: str = "", service: str = "", start: str = "", end: str = "",
                limit: int = 50) -> str:
    """【事件】列出变更/运维事件，type 可选 deploy(发布)/config_change(配置变更)/scale(扩缩容)/
    rollback(回滚)/failover(故障切换)/maintenance(维护)。缺省最近 24 小时，倒序。
    """
    return _dump(queries.list_events(type, service, start, end, limit))


@server.tool()
def get_event(event_id: str) -> str:
    """【事件】取事件详情：类型、标题、描述、操作人、影响 CI、版本变化、变更单号。"""
    return _dump(queries.get_event(event_id))


@server.tool()
def get_change_timeline(service: str = "", start: str = "", end: str = "") -> str:
    """【事件】变更+告警合并时间线（按时间正序）：还原「发布→告警→回滚→恢复」等因果链。缺省最近 24 小时。"""
    return _dump(queries.get_change_timeline(service, start, end))


# ---- 总览 ----

@server.tool()
def get_system_overview() -> str:
    """【总览】全局态势快照：当前告警（按严重级分组）、各服务 RED、最近 5 条事件、生效静默数。适合作为排查第一步。"""
    return _dump(queries.get_system_overview())


if __name__ == "__main__":
    server.run(transport="sse", host="127.0.0.1", port=18099)
