"""从 before/after 的 Locust 报告生成前后对比（stdout Markdown + comparison.json）。

用法：.venv/Scripts/python.exe -m perf.compare
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
KEY = "GET /api/v1/chat/sessions"
MSG_KEY = "GET /api/v1/chat/sessions/{session_id}/messages"
LEVELS = ["serial", "u10", "u30", "u50"]


def load(name: str) -> dict[str, Any]:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def main() -> None:
    rows: list[dict[str, Any]] = []
    print("| 并发 | 端点 | 指标 | 优化前 | 优化后 | 变化 |")
    print("|---|---|---|---|---|---|")
    for level in LEVELS:
        before = load(f"report_before_{level}.json")
        after = load(f"report_after_{level}.json")
        for ep in (KEY, MSG_KEY):
            b = before["endpoints"].get(ep, {})
            a = after["endpoints"].get(ep, {})
            short = "会话列表" if ep == KEY else "消息列表"
            for metric in ("p50", "p95", "p99"):
                bv, av = b.get(metric, 0), a.get(metric, 0)
                ratio = f"↓{bv / av:.1f}x" if av else "-"
                print(f"| {level} | {short} | {metric} | {bv}ms | {av}ms | {ratio} |")
                rows.append({
                    "level": level, "endpoint": ep, "metric": metric,
                    "before_ms": bv, "after_ms": av,
                })
            # 吞吐（每秒请求数）
            bc, ac = b.get("count", 0), a.get("count", 0)
            print(f"| {level} | {short} | 请求数 | {bc} | {ac} | ↑{ac / bc:.1f}x |" if bc else "")

    (ROOT / "comparison.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n已写 perf/comparison.json")


if __name__ == "__main__":
    main()
