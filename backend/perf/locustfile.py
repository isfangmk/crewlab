"""Locust 压测任务集：对后端全部可读端点 + 安全写端点发真实请求并计时。

运行（在项目根 backend/ 下）：
  # 无 Web UI，10 用户 / 30 秒，结果写 perf/report_before.json
  .venv/Scripts/python.exe -m locust -f perf/locustfile.py --headless \
      -u 10 -r 5 -t 30s --host http://127.0.0.1:18080 \
      --csv perf/locust_run --only-summary

  # 只跑读端点（默认），写端点用 WRITE_WEIGHT 环境变量控制
  $env:PERF_REPORT="perf/report_before.json"

统计：自定义 request event hook 按 (method, path) 聚合响应时间，
结束时把 p50/p95/p99/max 写入 PERF_REPORT 指定的 json。
"""

from __future__ import annotations

import json
import os
import re
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from locust import HttpUser, between, events, task
from locust.env import Environment

from perf.discovery import Endpoint, discover
from perf.env import api_base, load_manifest, login_token

PERF_ROOT = Path(__file__).resolve().parent
REPORT_PATH = Path(os.environ.get("PERF_REPORT", str(PERF_ROOT / "report_latest.json")))
WRITE_WEIGHT = int(os.environ.get("PERF_WRITE_WEIGHT", "1"))  # 写用户相对读用户的任务权重

# ---- 全局状态：端点清单 + 参数池 ----
_MANIFEST = load_manifest()
_SESSION_POOL = _MANIFEST.get("session_ids", [])
_BIG_SESSION_POOL = _MANIFEST.get("big_session_ids", []) or _SESSION_POOL
_MEMORY_POOL = _MANIFEST.get("memory_ids", [])
_enpoints = [e for e in discover() if e.kind in ("read", "write")]
_read_endpoints = [e for e in _enpoints if e.kind == "read"]
_write_endpoints = [e for e in _enpoints if e.kind == "write"]

# token 只在初始化登录一次，绝不打印
_TOKEN = ""

# ---- 响应时间采集 ----
_stats: dict[str, list[float]] = defaultdict(list)
_failures: dict[str, int] = defaultdict(int)


@events.init.add_listener
def on_init(environment: Environment, **_: Any) -> None:
    global _TOKEN
    with requests.Session() as s:
        _TOKEN = login_token(s)  # noqa: F841 - 全局复用


@events.request.add_listener
def on_request(request_type: str, name: str, response_time: float,
               response_length: int, exception: Exception | None, **_: Any) -> None:
    key = f"{request_type} {name}"
    if exception is not None:
        _failures[key] += 1
    else:
        _stats[key].append(response_time)


@events.quitting.add_listener
def on_quitting(environment: Environment, **_: Any) -> None:
    report: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "host": api_base(),
        "read_endpoints": len(_read_endpoints),
        "write_endpoints": len(_write_endpoints),
        "endpoints": {},
        "failures": dict(_failures),
    }
    for key, times in _stats.items():
        times_sorted = sorted(times)
        n = len(times_sorted)

        def pct(p: float) -> float:
            return round(times_sorted[min(n - 1, int(n * p))], 2) if n else 0.0

        report["endpoints"][key] = {
            "count": n,
            "min": round(times_sorted[0], 2) if n else 0.0,
            "p50": pct(0.50),
            "p95": pct(0.95),
            "p99": pct(0.99),
            "max": round(times_sorted[-1], 2) if n else 0.0,
            "avg": round(statistics.fmean(times), 2) if n else 0.0,
        }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[perf] 报告已写: {REPORT_PATH}（{len(_stats)} 个端点）")


# ---- 请求构造 ----
@dataclass
class Prepared:
    method: str
    path: str
    body: dict[str, Any]
    params: dict[str, Any]


def _pick(pool: list[str], idx: int) -> str:
    return pool[idx % len(pool)] if pool else "__perf_missing__"


def _fill_path(path: str, idx: int) -> str:
    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        if name == "session_id":
            return _pick(_SESSION_POOL, idx)
        if name == "memory_id":
            return _pick(_memory_pool(), idx)
        # 其余资源 id 用不存在的占位：请求会走完整路由→鉴权→校验→404，安全
        return "__perf_id__"

    return re.sub(r"\{(\w+)\}", repl, path)


def _memory_pool() -> list[str]:
    return _MEMORY_POOL


def _fill_body(body: dict[str, Any], path: str) -> dict[str, Any]:
    """把 body 里的占位 id 换成真实 perf id，避免造无效请求。"""

    out = json.loads(json.dumps(body))  # deep copy
    for key in list(out.keys()):
        if key in ("session_id",) and out[key] == "__perf_id__":
            m = re.search(r"/sessions/\{session_id\}", path)
            if m and _SESSION_POOL:
                out[key] = _SESSION_POOL[0]
    return out


def _prepare(ep: Endpoint, idx: int) -> Prepared:
    path = _fill_path(ep.path, idx)
    body = _fill_body(ep.body, ep.path)
    params = json.loads(json.dumps(ep.query))  # deep copy
    # 查询参数里若也是 perf 资源 id，用真实池值替换，避免 404
    for key in list(params.keys()):
        if key == "run_uid" and _MANIFEST.get("eval_run_uids"):
            params[key] = _pick(_MANIFEST["eval_run_uids"], idx)
    return Prepared(method=ep.method, path=path, body=body, params=params)


class PerfUser(HttpUser):
    abstract = True
    wait_time = between(0, 0.05)
    host = api_base()

    def on_start(self) -> None:
        self.client.headers.update({"Authorization": f"Bearer {_TOKEN}"})

    def _call(self, ep: Endpoint) -> None:
        prep = _prepare(ep, self.environment.runner.user_count if self.environment.runner else 0)
        # 用 Locust client 发请求，name 用带占位符的原始路径聚合统计
        name = ep.path
        if ep.method == "GET":
            self.client.get(prep.path, name=name, params=prep.params)
        else:
            self.client.request(ep.method, prep.path, json=prep.body,
                                params=prep.params, name=name)


class ReadUser(PerfUser):
    weight = 10

    @task
    def read_all(self) -> None:
        if not _read_endpoints:
            return
        # 每轮随机打一个读端点，覆盖全部
        import random

        ep = random.choice(_read_endpoints)
        self._call(ep)


class WriteUser(PerfUser):
    weight = WRITE_WEIGHT

    @task
    def write_all(self) -> None:
        if not _write_endpoints:
            return
        import random

        ep = random.choice(_write_endpoints)
        self._call(ep)
