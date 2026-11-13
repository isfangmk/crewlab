"""图表规格构建器：把整理好的数据转成 ECharts option JSON。

这个文件是 chart-builder 技能的数据契约实现，标准库即可运行，两条路径共用：

1. 对话里通过内置工具 render_chart 调用（后端 import 本模块）；
2. 沙箱里通过 run_skill_script 执行本脚本（args 传 chart / title / data）。

输入是一个 JSON 对象：
    {"chart": "line", "title": "CPU 趋势", "data": "<data 的 JSON 字符串>"}

输出是一个 JSON 对象：
    {"chart": ..., "title": ..., "option": {...ECharts 配置...}, "summary": "一句话结论"}
"""

from __future__ import annotations

import json
import sys
from typing import Any

# 图表配色：暗色底上都能看清，且相邻色相拉开
PALETTE = [
    "#6366f1",
    "#22d3ee",
    "#34d399",
    "#fbbf24",
    "#fb7185",
    "#a78bfa",
    "#f472b6",
    "#38bdf8",
]

TEXT = "#a1a1aa"
TEXT_STRONG = "#e4e4e7"
AXIS_LINE = "#3f3f46"
SPLIT_LINE = "#27272a"
TOOLTIP_BG = "#18181b"

CHART_TYPES = (
    "line",
    "area",
    "bar",
    "hbar",
    "stacked_bar",
    "grouped_bar",
    "pie",
    "donut",
    "scatter",
    "radar",
    "gauge",
    "funnel",
    "heatmap",
    "treemap",
    "boxplot",
    "waterfall",
)

# 类别型图表：x 轴是分类
CATEGORY_CHARTS = {
    "bar",
    "hbar",
    "stacked_bar",
    "grouped_bar",
    "pie",
    "donut",
    "funnel",
    "boxplot",
    "waterfall",
}


class ChartError(ValueError):
    """数据不合法：消息直接回给模型，让它改数据重试。"""


def _require_dict(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ChartError(f"{field} 必须是对象")
    return value


def _series_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    series = data.get("series")
    if series is None:
        raise ChartError("data 缺少 series")
    if not isinstance(series, list) or len(series) == 0:
        raise ChartError("data.series 必须是非空数组")
    out: list[dict[str, Any]] = []
    for item in series:
        item = _require_dict(item, "data.series[]")
        name = item.get("name")
        values = item.get("data")
        if not isinstance(values, list):
            raise ChartError("data.series[].data 必须是数组")
        out.append(
            {
                "name": str(name) if name is not None else "",
                "data": [_number(v) for v in values],
            }
        )
    return out


def _number(value: Any) -> float:
    """数值归一：布尔与数字字符串都接受，带单位的字符串拒绝。"""

    if isinstance(value, bool):
        raise ChartError(f"数值不能是布尔值: {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        try:
            return float(text)
        except ValueError as exc:
            raise ChartError(f"数值不能带单位或非数字: {value!r}") from exc
    raise ChartError(f"不是合法数值: {value!r}")


def _categories_of(data: dict[str, Any]) -> list[str]:
    for key in ("categories", "x"):
        raw = data.get(key)
        if raw is None:
            continue
        if not isinstance(raw, list) or len(raw) == 0:
            raise ChartError(f"data.{key} 必须是非空数组")
        return [str(item) for item in raw]
    raise ChartError("data 缺少 categories")


def _tooltip(trigger: str = "axis") -> dict[str, Any]:
    return {
        "trigger": trigger,
        "backgroundColor": TOOLTIP_BG,
        "borderColor": AXIS_LINE,
        "textStyle": {"color": TEXT_STRONG, "fontSize": 11},
    }


def _grid(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    grid: dict[str, Any] = {"left": 48, "right": 20, "top": 28, "bottom": 40, "containLabel": True}
    if extra:
        grid.update(extra)
    return grid


def _category_axis(categories: list[str], rotate: int = 0) -> dict[str, Any]:
    return {
        "type": "category",
        "data": categories,
        "axisLine": {"lineStyle": {"color": AXIS_LINE}},
        "axisTick": {"show": False},
        "axisLabel": {"color": TEXT, "fontSize": 10, "rotate": rotate, "hideOverlap": True},
    }


def _value_axis(name: str = "") -> dict[str, Any]:
    return {
        "type": "value",
        "name": name,
        "nameTextStyle": {"color": TEXT, "fontSize": 10},
        "axisLine": {"show": False},
        "axisTick": {"show": False},
        "axisLabel": {"color": TEXT, "fontSize": 10},
        "splitLine": {"lineStyle": {"color": SPLIT_LINE}},
    }


def _label(show: bool = False) -> dict[str, Any]:
    if not show:
        return {"show": False}
    return {
        "show": True,
        "color": TEXT,
        "fontSize": 10,
        "position": "top",
    }


# ---------------------------------------------------------------- 各图表类型


def _build_line(data: dict[str, Any], area: bool) -> dict[str, Any]:
    xs = data.get("x")
    if not isinstance(xs, list) or len(xs) == 0:
        raise ChartError("data.x 必须是非空数组")
    series = _series_of(data)
    out_series: list[dict[str, Any]] = []
    for index, item in enumerate(series):
        line: dict[str, Any] = {
            "name": item["name"],
            "type": "line",
            "data": item["data"],
            "smooth": True,
            "showSymbol": len(item["data"]) <= 30,
            "symbolSize": 4,
            "itemStyle": {"color": PALETTE[index % len(PALETTE)]},
            "lineStyle": {"width": 1.6},
        }
        if area:
            line["areaStyle"] = {"opacity": 0.18}
        out_series.append(line)
    return {
        "tooltip": _tooltip(),
        "legend": _legend(len(out_series)),
        "grid": _grid(),
        "xAxis": _category_axis([str(v) for v in xs]),
        "yAxis": _value_axis(str(data.get("y_name") or "")),
        "series": out_series,
    }


def _build_bar(data: dict[str, Any], mode: str) -> dict[str, Any]:
    categories = _categories_of(data)
    series = _series_of(data)
    stacked = mode == "stacked"
    out_series: list[dict[str, Any]] = []
    for index, item in enumerate(series):
        bar: dict[str, Any] = {
            "name": item["name"],
            "type": "bar",
            "data": item["data"],
            "stack": "total" if stacked else None,
            "itemStyle": {
                "color": PALETTE[index % len(PALETTE)],
                "borderRadius": mode == "hbar" and [0, 4, 4, 0] or [4, 4, 0, 0],
            },
            "label": _label(mode != "stacked" and len(series) == 1),
        }
        if mode == "grouped":
            bar["barGap"] = "10%"
        out_series.append(bar)
    horizontal = mode == "hbar"
    value_axis = _value_axis(str(data.get("y_name") or ""))
    cat_axis = _category_axis(categories, rotate=0 if horizontal else 30)
    return {
        "tooltip": _tooltip(),
        "legend": _legend(len(out_series)),
        "grid": _grid({"left": 70, "bottom": 60} if horizontal else None),
        "xAxis": value_axis if horizontal else cat_axis,
        "yAxis": cat_axis if horizontal else value_axis,
        "series": out_series,
    }


def _build_pie(data: dict[str, Any], donut: bool) -> dict[str, Any]:
    categories = _categories_of(data)
    series = _series_of(data)
    values = series[0]["data"]
    if len(values) != len(categories):
        raise ChartError("data.series[0].data 与 categories 长度不一致")
    total = sum(v for v in values if v > 0)
    pie: dict[str, Any] = {
        "name": series[0]["name"] or "",
        "type": "pie",
        "radius": ["48%", "72%"] if donut else ["0%", "70%"],
        "center": ["50%", "56%"],
        "avoidLabelOverlap": True,
        "itemStyle": {"borderColor": "#18181b", "borderWidth": 1},
        "label": {"color": TEXT, "fontSize": 10, "formatter": "{b}\n{d}%"},
        "data": [
            {"name": name, "value": value}
            for name, value in zip(categories, values, strict=True)
        ],
    }
    option: dict[str, Any] = {
        "tooltip": _tooltip("item"),
        "legend": {"show": False},
        "series": [pie],
    }
    if donut:
        option["graphic"] = [
            {
                "type": "text",
                "left": "center",
                "top": "48%",
                "style": {
                    "text": f"{total:g}",
                    "fill": TEXT_STRONG,
                    "fontSize": 20,
                    "textAlign": "center",
                },
            },
            {
                "type": "text",
                "left": "center",
                "top": "60%",
                "style": {
                    "text": series[0]["name"] or "",
                    "fill": TEXT,
                    "fontSize": 10,
                    "textAlign": "center",
                },
            },
        ]
    return option


def _build_scatter(data: dict[str, Any]) -> dict[str, Any]:
    points = data.get("points")
    if not isinstance(points, list) or len(points) == 0:
        raise ChartError("data.points 必须是非空数组")
    series: dict[str, Any] = {
        "name": str(data.get("name") or ""),
        "type": "scatter",
        "symbolSize": 7,
        "itemStyle": {"color": PALETTE[0], "opacity": 0.75},
        "data": [],
    }
    for point in points:
        point = _require_dict(point, "data.points[]")
        if "x" not in point or "y" not in point:
            raise ChartError("data.points[] 需要 x 与 y")
        item: list[Any] = [_number(point["x"]), _number(point["y"])]
        if point.get("name") is not None:
            item.append(str(point["name"]))
        series["data"].append(item)
    return {
        "tooltip": _tooltip("item"),
        "grid": _grid(),
        "xAxis": _value_axis(str(data.get("x_name") or "")),
        "yAxis": _value_axis(str(data.get("y_name") or "")),
        "series": [series],
    }


def _build_radar(data: dict[str, Any]) -> dict[str, Any]:
    raw_indicators = data.get("indicators")
    if not isinstance(raw_indicators, list) or len(raw_indicators) == 0:
        raise ChartError("data.indicators 必须是非空数组")
    indicators: list[dict[str, Any]] = []
    for item in raw_indicators:
        item = _require_dict(item, "data.indicators[]")
        indicators.append(
            {
                "name": str(item.get("name") or ""),
                "max": _number(item.get("max", 100)),
            }
        )
    series = _series_of(data)
    out_series: list[dict[str, Any]] = []
    for index, item in enumerate(series):
        out_series.append(
            {
                "name": item["name"],
                "type": "radar",
                "data": [item["data"]],
                "itemStyle": {"color": PALETTE[index % len(PALETTE)]},
                "areaStyle": {"opacity": 0.16},
                "lineStyle": {"width": 1.6},
            }
        )
    return {
        "tooltip": _tooltip("item"),
        "legend": _legend(len(out_series)),
        "radar": {
            "indicator": indicators,
            "axisName": {"color": TEXT, "fontSize": 10},
            "splitLine": {"lineStyle": {"color": SPLIT_LINE}},
            "splitArea": {"show": False},
            "axisLine": {"lineStyle": {"color": SPLIT_LINE}},
        },
        "series": out_series,
    }


def _build_gauge(data: dict[str, Any]) -> dict[str, Any]:
    if "value" not in data:
        raise ChartError("data 缺少 value")
    value = _number(data["value"])
    name = str(data.get("name") or "")
    return {
        "tooltip": _tooltip("item"),
        "series": [
            {
                "name": name,
                "type": "gauge",
                "min": 0,
                "max": 100,
                "progress": {"show": True, "width": 10, "itemStyle": {"color": PALETTE[0]}},
                "axisLine": {"lineStyle": {"width": 10, "color": [[1, SPLIT_LINE]]}},
                "axisTick": {"show": False},
                "splitLine": {"length": 8, "lineStyle": {"color": AXIS_LINE, "width": 1}},
                "axisLabel": {"color": TEXT, "fontSize": 9},
                "pointer": {"length": "60%", "width": 3, "itemStyle": {"color": TEXT_STRONG}},
                "detail": {
                    "valueAnimation": True,
                    "fontSize": 22,
                    "color": TEXT_STRONG,
                    "formatter": "{value}",
                    "offsetCenter": [0, "62%"],
                },
                "title": {
                    "show": name != "",
                    "offsetCenter": [0, "84%"],
                    "color": TEXT,
                    "fontSize": 10,
                },
                "data": [{"value": value, "name": name}],
            }
        ],
    }


def _build_funnel(data: dict[str, Any]) -> dict[str, Any]:
    categories = _categories_of(data)
    series = _series_of(data)
    values = series[0]["data"]
    if len(values) != len(categories):
        raise ChartError("data.series[0].data 与 categories 长度不一致")
    return {
        "tooltip": _tooltip("item"),
        "legend": _legend(0),
        "series": [
            {
                "name": series[0]["name"] or "",
                "type": "funnel",
                "left": "12%",
                "top": 16,
                "bottom": 16,
                "width": "76%",
                "minSize": "18%",
                "sort": "descending",
                "gap": 3,
                "label": {"color": TEXT_STRONG, "fontSize": 10},
                "itemStyle": {"borderColor": "#18181b", "borderWidth": 1},
                "data": [
                    {"name": name, "value": value}
                    for name, value in zip(categories, values, strict=True)
                ],
            }
        ],
    }


def _build_heatmap(data: dict[str, Any]) -> dict[str, Any]:
    xs = data.get("x")
    ys = data.get("y")
    values = data.get("values")
    if not isinstance(xs, list) or not isinstance(ys, list):
        raise ChartError("data.x 与 data.y 必须是数组")
    if not isinstance(values, list):
        raise ChartError("data.values 必须是二维数组")
    points: list[list[Any]] = []
    for y_index, row in enumerate(values):
        if not isinstance(row, list):
            raise ChartError("data.values 的每一行必须是数组")
        for x_index, cell in enumerate(row):
            points.append([x_index, y_index, _number(cell)])
    return {
        "tooltip": {
            "position": "top",
            "backgroundColor": TOOLTIP_BG,
            "borderColor": AXIS_LINE,
            "textStyle": {"color": TEXT_STRONG, "fontSize": 11},
        },
        "grid": _grid({"left": 70, "bottom": 60}),
        "xAxis": _category_axis([str(v) for v in xs], rotate=30),
        "yAxis": _category_axis([str(v) for v in ys]),
        "visualMap": {
            "min": min((p[2] for p in points), default=0),
            "max": max((p[2] for p in points), default=1),
            "calculable": True,
            "orient": "horizontal",
            "left": "center",
            "bottom": 4,
            "textStyle": {"color": TEXT, "fontSize": 10},
            "inRange": {"color": ["#312e81", "#4f46e5", "#22d3ee"]},
        },
        "series": [
            {
                "name": str(data.get("name") or ""),
                "type": "heatmap",
                "data": points,
                "label": {"show": True, "color": "#fff", "fontSize": 9},
                "emphasis": {"itemStyle": {"shadowBlur": 6, "shadowColor": "rgba(0,0,0,0.4)"}},
            }
        ],
    }


def _build_treemap(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("data")
    if not isinstance(raw, list) or len(raw) == 0:
        raise ChartError("data.data 必须是非空数组")

    def convert(nodes: list[Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for node in nodes:
            node = _require_dict(node, "data.data[]")
            item: dict[str, Any] = {"name": str(node.get("name") or "")}
            if node.get("value") is not None:
                item["value"] = _number(node["value"])
            children = node.get("children")
            if isinstance(children, list) and len(children) > 0:
                item["children"] = convert(children)
            out.append(item)
        return out

    return {
        "tooltip": _tooltip("item"),
        "series": [
            {
                "type": "treemap",
                "data": convert(raw),
                "roam": False,
                "nodeClick": False,
                "breadcrumb": {"show": False},
                "itemStyle": {"borderColor": "#18181b", "borderWidth": 1, "gapWidth": 1},
                "label": {"color": TEXT_STRONG, "fontSize": 10},
                "upperLabel": {"show": True, "height": 18, "color": TEXT, "fontSize": 10},
            }
        ],
    }


def _quantile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * ratio
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    weight = index - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def _build_boxplot(data: dict[str, Any]) -> dict[str, Any]:
    categories = _categories_of(data)
    series = _series_of(data)
    boxes: list[list[float]] = []
    for item in series:
        raw_values = item["data"]
        if len(raw_values) != len(categories):
            raise ChartError("data.series[].data 与 categories 长度不一致")
        for group in range(len(categories)):
            values = [item["data"][group]]
            # 每类一个统计量时直接当成分位数组使用
            boxes.append(_box_stats(values))
    return {
        "tooltip": _tooltip("item"),
        "grid": _grid({"left": 60, "bottom": 60}),
        "xAxis": _category_axis(categories, rotate=30),
        "yAxis": _value_axis(str(data.get("y_name") or "")),
        "series": [
            {
                "name": series[0]["name"] or "",
                "type": "boxplot",
                "data": boxes,
                "itemStyle": {"color": PALETTE[0], "borderColor": PALETTE[0]},
            }
        ],
    }


def _box_stats(values: list[float]) -> list[float]:
    """[min, Q1, median, Q3, max]；单值时退化为同值五元组。"""

    if not values:
        raise ChartError("boxplot 的数值不能为空")
    return [
        min(values),
        _quantile(values, 0.25),
        _quantile(values, 0.5),
        _quantile(values, 0.75),
        max(values),
    ]


def _build_waterfall(data: dict[str, Any]) -> dict[str, Any]:
    categories = _categories_of(data)
    series = _series_of(data)
    values = series[0]["data"]
    if len(values) != len(categories):
        raise ChartError("data.series[0].data 与 categories 长度不一致")
    # 第一格是起点，后续是增量；转成 ECharts 的透明底柱写法
    base: list[float] = []
    delta: list[float] = []
    transparent: list[float] = []
    running = 0.0
    for index, value in enumerate(values):
        if index == 0:
            running = value
            base.append(0.0)
            delta.append(value)
            transparent.append(value)
            continue
        start = running
        running += value
        base.append(min(start, running))
        delta.append(abs(value))
        transparent.append(max(start, running))
    return {
        "tooltip": _tooltip("item"),
        "grid": _grid({"left": 60, "bottom": 60}),
        "xAxis": _category_axis(categories, rotate=30),
        "yAxis": _value_axis(str(data.get("y_name") or "")),
        "series": [
            {
                "name": "base",
                "type": "bar",
                "stack": "wf",
                "itemStyle": {"color": "transparent"},
                "emphasis": {"itemStyle": {"color": "transparent"}},
                "data": base,
            },
            {
                "name": series[0]["name"] or "",
                "type": "bar",
                "stack": "wf",
                "itemStyle": {"color": PALETTE[0], "borderRadius": [3, 3, 0, 0]},
                "data": delta,
            },
        ],
    }


def _legend(count: int) -> dict[str, Any]:
    return {
        "show": count > 1,
        "top": 0,
        "right": 0,
        "icon": "roundRect",
        "itemWidth": 8,
        "itemHeight": 8,
        "textStyle": {"color": TEXT, "fontSize": 10},
    }


# ---------------------------------------------------------------- 结论与入口


def _summarize(chart: str, data: dict[str, Any]) -> str:
    """一句话结论：让模型可以据此写正文，不依赖看图。"""

    try:
        if chart in ("line", "area"):
            series = _series_of(data)
            values = [v for item in series for v in item["data"] if v is not None]
            if not values:
                return "无数据"
            peak = max(values)
            low = min(values)
            avg = sum(values) / len(values)
            return f"共 {len(values)} 个点，峰值 {peak:g}，最低 {low:g}，均值 {avg:.2f}"
        if chart in ("bar", "hbar", "stacked_bar", "grouped_bar"):
            series = _series_of(data)
            values = [v for item in series for v in item["data"]]
            if not values:
                return "无数据"
            return f"共 {len(series[0]['data'])} 个类别，合计 {sum(values):g}，最大 {max(values):g}"
        if chart in ("pie", "donut"):
            series = _series_of(data)
            values = series[0]["data"]
            categories = _categories_of(data)
            if not values:
                return "无数据"
            total = sum(v for v in values if v > 0)
            if total <= 0:
                return "无数据"
            top = max(zip(categories, values, strict=True), key=lambda pair: pair[1])
            return f"共 {len(values)} 个分项，最大项 {top[0]} 占 {top[1] / total * 100:.1f}%"
        if chart == "gauge":
            return f"{str(data.get('name') or '指标')} {data.get('value')}%"
        if chart == "funnel":
            series = _series_of(data)
            values = series[0]["data"]
            if not values:
                return "无数据"
            first = values[0]
            last = values[-1]
            rate = (last / first * 100) if first else 0.0
            return f"从 {first:g} 降到 {last:g}，转化率 {rate:.1f}%"
        if chart == "scatter":
            points = data.get("points") or []
            return f"共 {len(points)} 个点"
        if chart == "radar":
            series = _series_of(data)
            if not series or not series[0]["data"]:
                return "无数据"
            values = series[0]["data"]
            return f"{len(values)} 个维度，最高 {max(values):g}，最低 {min(values):g}"
        if chart == "heatmap":
            values = data.get("values") or []
            flat = [float(cell) for row in values if isinstance(row, list) for cell in row]
            if not flat:
                return "无数据"
            return f"{len(values)} 行 × {len(values[0]) if values else 0} 列，最大值 {max(flat):g}"
        if chart == "treemap":
            return "按层级展示占比"
        if chart == "boxplot":
            series = _series_of(data)
            values = [v for item in series for v in item["data"]]
            if not values:
                return "无数据"
            return f"中位数 {_quantile(values, 0.5):g}，最大 {max(values):g}，最小 {min(values):g}"
        if chart == "waterfall":
            series = _series_of(data)
            values = series[0]["data"]
            if not values:
                return "无数据"
            return f"从 {values[0]:g} 变化到 {sum(values):g}"
    except ChartError:
        return ""
    return ""


def build_option(chart: str, data: dict[str, Any]) -> dict[str, Any]:
    """按图表类型生成 ECharts 配置。"""

    if chart == "line":
        return _build_line(data, area=False)
    if chart == "area":
        return _build_line(data, area=True)
    if chart in ("bar", "hbar", "stacked_bar", "grouped_bar"):
        return _build_bar(data, chart)
    if chart == "pie":
        return _build_pie(data, donut=False)
    if chart == "donut":
        return _build_pie(data, donut=True)
    if chart == "scatter":
        return _build_scatter(data)
    if chart == "radar":
        return _build_radar(data)
    if chart == "gauge":
        return _build_gauge(data)
    if chart == "funnel":
        return _build_funnel(data)
    if chart == "heatmap":
        return _build_heatmap(data)
    if chart == "treemap":
        return _build_treemap(data)
    if chart == "boxplot":
        return _build_boxplot(data)
    if chart == "waterfall":
        return _build_waterfall(data)
    raise ChartError(f"未知图表类型: {chart}；可选：{', '.join(CHART_TYPES)}")


def render(chart: str, title: str, raw_data: str) -> dict[str, Any]:
    """入口：chart 类型 + 标题 + data 的 JSON 字符串，产出图表规格。"""

    if chart not in CHART_TYPES:
        raise ChartError(f"未知图表类型: {chart}；可选：{', '.join(CHART_TYPES)}")
    if not isinstance(raw_data, str) or raw_data.strip() == "":
        raise ChartError("data 不能为空")
    try:
        data = json.loads(raw_data)
    except json.JSONDecodeError as exc:
        raise ChartError(f"data 不是合法 JSON: {exc}") from exc
    data = _require_dict(data, "data")
    option = build_option(chart, data)
    return {
        "chart": chart,
        "title": title,
        "option": option,
        "summary": _summarize(chart, data),
    }


def _parse_args(argv: list[str]) -> tuple[str, str, str]:
    """解析命令行参数：--chart / --title / --data。

    ADK 的 run_skill_script 把 args 里的键值对展开成长选项（--key value），
    所以脚本必须按这个约定读参数；同时保留 stdin 的 JSON 形式，
    便于直接命令行调试。
    """

    values: dict[str, str] = {}
    index = 0
    while index < len(argv):
        token = argv[index]
        if token.startswith("--"):
            key = token[2:]
            value = ""
            if index + 1 < len(argv) and not argv[index + 1].startswith("--"):
                value = argv[index + 1]
                index += 1
            values[key] = value
        index += 1

    chart = values.get("chart", "")
    title = values.get("title", "")
    data = values.get("data", "")
    if data == "" and not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw != "":
            payload = json.loads(raw)
            chart = str(payload.get("chart") or chart)
            title = str(payload.get("title") or title)
            data = payload.get("data")
            if not isinstance(data, str):
                data = json.dumps(data, ensure_ascii=False)
    return chart, title, data


def main(argv: list[str]) -> int:
    """命令行入口：stdout 打印图表规格 JSON。"""

    try:
        chart, title, data = _parse_args(argv)
        spec = render(chart, title, data)
    except ChartError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"data 不是合法 JSON: {exc}"}, ensure_ascii=False))
        return 1
    print(json.dumps(spec, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
