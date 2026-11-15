"""chart_spec：数据 → ECharts 结构映射的类型覆盖、结构断言与数据校验。"""

from __future__ import annotations

from typing import Any

import pytest

from app.tools.chart_spec import CHART_TYPES, ChartError, build_chart_option

# 每种图表类型都要能产出可渲染的 ECharts 结构
_TYPE_CASES = [
    ("line", {"x": ["10:00", "10:05"], "series": [{"name": "CPU", "data": [10, 42]}]}),
    ("area", {"x": ["10:00", "10:05"], "series": [{"name": "CPU", "data": [10, 42]}]}),
    ("bar", {"categories": ["a", "b"], "series": [{"name": "值", "data": [1, 2]}]}),
    ("hbar", {"categories": ["a", "b"], "series": [{"name": "值", "data": [1, 2]}]}),
    ("stacked_bar", {"categories": ["a", "b"], "series": [{"name": "x", "data": [1, 2]}]}),
    ("grouped_bar", {"categories": ["a", "b"], "series": [{"name": "x", "data": [1, 2]}]}),
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
]

# 图表类型 → ECharts series 类型
_SERIES_TYPE = {
    "line": "line",
    "area": "line",
    "bar": "bar",
    "hbar": "bar",
    "stacked_bar": "bar",
    "grouped_bar": "bar",
    "waterfall": "bar",
    "pie": "pie",
    "donut": "pie",
    "scatter": "scatter",
    "radar": "radar",
    "gauge": "gauge",
    "funnel": "funnel",
    "heatmap": "heatmap",
    "treemap": "treemap",
    "boxplot": "boxplot",
}

# 颜色/文字样式类 key：一律不许出现在结构里（配色只属于渲染层主题），
# 瀑布图透明垫高柱的 "transparent" 是结构技巧，单独放行。
_STYLE_KEYS = {"color", "backgroundColor", "borderColor", "textStyle", "inRange"}


def _style_hits(node: Any, path: str = "$") -> list[str]:
    """递归找结构里的样式 key，返回违规路径列表。

    唯一放行：color 值为 "transparent" 的瀑布图垫高柱（结构技巧非配色）。
    """

    hits: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _STYLE_KEYS and not (key == "color" and value == "transparent"):
                hits.append(f"{path}.{key}={value!r}")
            hits.extend(_style_hits(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            hits.extend(_style_hits(item, f"{path}[{index}]"))
    return hits


@pytest.mark.parametrize(("chart_type", "data"), _TYPE_CASES)
def test_chart_types_build(chart_type: str, data: dict[str, Any]) -> None:
    option = build_chart_option(data, chart_type)
    assert option["series"], f"{chart_type} 缺少 series"
    assert option["series"][0]["type"] == _SERIES_TYPE[chart_type]


@pytest.mark.parametrize(("chart_type", "data"), _TYPE_CASES)
def test_structure_carries_no_colors(chart_type: str, data: dict[str, Any]) -> None:
    """结构层零配色：颜色/文字样式只允许出现在渲染层主题里。"""

    option = build_chart_option(data, chart_type)
    assert _style_hits(option) == []


def test_chart_types_cover_all() -> None:
    """类型表与构建器不分叉。"""

    assert {case[0] for case in _TYPE_CASES} == set(CHART_TYPES)


def test_stacked_bar_stacks_and_grouped_bar_gaps() -> None:
    stacked = build_chart_option(
        {"categories": ["a", "b"], "series": [{"name": "x", "data": [1, 2]}, {"name": "y", "data": [3, 4]}]},
        "stacked_bar",
    )
    assert all(item["stack"] == "total" for item in stacked["series"])
    grouped = build_chart_option(
        {"categories": ["a", "b"], "series": [{"name": "x", "data": [1, 2]}]},
        "grouped_bar",
    )
    assert grouped["series"][0]["barGap"] == "10%"
    assert "stack" not in grouped["series"][0]


def test_hbar_swaps_axes() -> None:
    option = build_chart_option(
        {"categories": ["a", "b"], "series": [{"name": "值", "data": [1, 2]}]},
        "hbar",
    )
    assert option["xAxis"]["type"] == "value"
    assert option["yAxis"]["type"] == "category"
    assert option["series"][0]["itemStyle"]["borderRadius"] == [0, 4, 4, 0]


def test_bar_rotates_category_labels() -> None:
    option = build_chart_option(
        {"categories": ["a", "b"], "series": [{"name": "值", "data": [1, 2]}]},
        "bar",
    )
    assert option["xAxis"]["axisLabel"]["rotate"] == 30


def test_line_symbol_and_area_opacity() -> None:
    line = build_chart_option(
        {"x": ["a"], "series": [{"name": "n", "data": [1]}]},
        "line",
    )
    assert line["series"][0]["smooth"] is True
    assert line["series"][0]["showSymbol"] is True
    assert "areaStyle" not in line["series"][0]
    area = build_chart_option(
        {"x": ["a"], "series": [{"name": "n", "data": [1]}]},
        "area",
    )
    assert area["series"][0]["areaStyle"] == {"opacity": 0.18}


def test_donut_center_total_uses_title() -> None:
    donut = build_chart_option(
        {"categories": ["a", "b"], "series": [{"name": "占比", "data": [60, 40]}]},
        "donut",
    )
    assert donut["series"][0]["radius"] == ["48%", "72%"]
    assert donut["title"]["text"] == "100"
    assert donut["title"]["subtext"] == "占比"
    pie = build_chart_option(
        {"categories": ["a", "b"], "series": [{"name": "占比", "data": [60, 40]}]},
        "pie",
    )
    assert "title" not in pie


def test_heatmap_flattens_grid_and_bounds_visual_map() -> None:
    option = build_chart_option(
        {"x": ["周一", "周二"], "y": ["北京"], "values": [[1, 2], [3, 4]]},
        "heatmap",
    )
    assert option["series"][0]["data"] == [[0, 0, 1.0], [1, 0, 2.0], [0, 1, 3.0], [1, 1, 4.0]]
    assert option["visualMap"]["min"] == 1.0
    assert option["visualMap"]["max"] == 4.0


def test_waterfall_transparent_base() -> None:
    option = build_chart_option(
        {"categories": ["起点", "增长"], "series": [{"name": "值", "data": [100, 20]}]},
        "waterfall",
    )
    base, delta = option["series"]
    assert base["itemStyle"]["color"] == "transparent"
    assert base["data"] == [0.0, 100.0]
    assert delta["data"] == [100.0, 20.0]
    assert delta["stack"] == "wf"


def test_boxplot_five_number_summary() -> None:
    option = build_chart_option(
        {"categories": ["p50"], "series": [{"name": "延迟", "data": [10]}]},
        "boxplot",
    )
    assert option["series"][0]["data"] == [[10.0, 10.0, 10.0, 10.0, 10.0]]


def test_unknown_chart_type_is_rejected() -> None:
    with pytest.raises(ChartError, match="未知图表类型"):
        build_chart_option({}, "nope")


def test_value_with_unit_is_rejected() -> None:
    """带单位的数值不是数字，必须报错而不是画出一张错的图。"""

    with pytest.raises(ChartError, match="不能带单位"):
        build_chart_option(
            {"x": ["a"], "series": [{"name": "n", "data": ["80%"]}]},
            "line",
        )


def test_bool_value_is_rejected() -> None:
    with pytest.raises(ChartError, match="布尔"):
        build_chart_option(
            {"x": ["a"], "series": [{"name": "n", "data": [True]}]},
            "line",
        )


@pytest.mark.parametrize(
    ("chart_type", "data", "match"),
    [
        ("line", {"series": [{"name": "n", "data": [1]}]}, "data.x"),
        ("bar", {"series": [{"name": "n", "data": [1]}]}, "categories"),
        ("pie", {"categories": ["a", "b"], "series": [{"name": "n", "data": [1]}]}, "长度不一致"),
        ("gauge", {"name": "x"}, "value"),
        ("scatter", {"points": [{"x": 1}]}, "x 与 y"),
        ("radar", {"series": [{"name": "n", "data": [1]}]}, "indicators"),
        ("treemap", {}, "data.data"),
        ("heatmap", {"x": ["a"], "y": ["b"], "values": [1]}, "每一行"),
        ("line", {"x": ["a"], "series": []}, "series"),
    ],
)
def test_bad_data_is_rejected(chart_type: str, data: dict[str, Any], match: str) -> None:
    with pytest.raises(ChartError, match=match):
        build_chart_option(data, chart_type)
