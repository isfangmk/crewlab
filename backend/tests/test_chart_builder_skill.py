"""内置绘图技能的脚本：图表类型覆盖与数据校验。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "chart-builder"
    / "scripts"
    / "render_chart.py"
)


def _run(chart: str, data: object, title: str = "标题") -> dict:
    """按 ADK 的调用方式跑脚本：长选项传参，stdout 返回 JSON。"""

    completed = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--chart",
            chart,
            "--title",
            title,
            "--data",
            json.dumps(data, ensure_ascii=False),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.mark.parametrize(
    ("chart", "data"),
    [
        ("line", {"x": ["10:00", "10:05"], "series": [{"name": "CPU", "data": [10, 42]}]}),
        ("area", {"x": ["10:00", "10:05"], "series": [{"name": "CPU", "data": [10, 42]}]}),
        ("bar", {"categories": ["a", "b"], "series": [{"name": "值", "data": [1, 2]}]}),
        ("hbar", {"categories": ["a", "b"], "series": [{"name": "值", "data": [1, 2]}]}),
        (
            "stacked_bar",
            {"categories": ["a", "b"], "series": [{"name": "x", "data": [1, 2]}]},
        ),
        (
            "grouped_bar",
            {"categories": ["a", "b"], "series": [{"name": "x", "data": [1, 2]}]},
        ),
        ("pie", {"categories": ["a", "b"], "series": [{"name": "占比", "data": [60, 40]}]}),
        ("donut", {"categories": ["a", "b"], "series": [{"name": "占比", "data": [60, 40]}]}),
        ("scatter", {"points": [{"x": 1, "y": 2, "name": "p"}]}),
        (
            "radar",
            {
                "indicators": [{"name": "CPU", "max": 100}],
                "series": [{"name": "设备", "data": [80, 60]}],
            },
        ),
        ("gauge", {"value": 72, "name": "CPU 使用率"}),
        ("funnel", {"categories": ["浏览", "下单"], "series": [{"name": "数", "data": [1000, 200]}]}),
        ("heatmap", {"x": ["周一"], "y": ["北京"], "values": [[1, 2], [3, 4]]}),
        ("treemap", {"data": [{"name": "目录", "value": 10, "children": []}]}),
        ("boxplot", {"categories": ["p50"], "series": [{"name": "延迟", "data": [10]}]}),
        ("waterfall", {"categories": ["起点", "增长"], "series": [{"name": "值", "data": [100, 20]}]}),
    ],
)
def test_chart_types_render(chart: str, data: object) -> None:
    """每种图表类型都要能产出可渲染的 ECharts 配置。"""

    spec = _run(chart, data)
    assert spec["chart"] == chart
    assert spec["title"] == "标题"
    option = spec["option"]
    assert option["series"], f"{chart} 缺少 series"
    assert spec["summary"], f"{chart} 缺少结论"


def test_unknown_chart_type_is_rejected() -> None:
    completed = subprocess.run(
        [sys.executable, str(_SCRIPT), "--chart", "nope", "--title", "t", "--data", "{}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == 1
    assert "未知图表类型" in completed.stdout


def test_value_with_unit_is_rejected() -> None:
    """带单位的数值不是数字，必须报错而不是画出一张错的图。"""

    completed = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--chart",
            "line",
            "--title",
            "t",
            "--data",
            json.dumps({"x": ["a"], "series": [{"name": "n", "data": ["80%"]}]}),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == 1
    assert "不能带单位" in completed.stdout


def test_line_summary_reports_peak() -> None:
    spec = _run("line", {"x": ["a", "b", "c"], "series": [{"name": "n", "data": [1, 9, 5]}]})
    assert "9" in spec["summary"]
