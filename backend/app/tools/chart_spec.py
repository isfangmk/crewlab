"""图表规格构建器：把整理好的数据转成 ECharts option（纯结构，零配色）。

配色与文字样式统一由前端渲染层的主题注入（web/src/lib/chartTheme.ts），
本模块只做「数据 → 图形结构」的映射：图表的视觉风格只有一份定义。

标准库即可运行，两条路径共用：
1. 后端单测直接 import；
2. 沙盒脚本文本内嵌（code_interpreter 的 chart() 助手调用）。

data 数据契约（按图表类型取字段）：
    折线/面积    {"x": [...], "series": [{"name", "data": [...]}], "y_name"?: str}
    柱/条/堆叠/分组 {"categories" 或 "x": [...], "series": [{"name", "data": [...]}], "y_name"?: str}
    饼/环形      {"categories" 或 "x": [...], "series": [{"name", "data": [...]}]}
    散点        {"points": [{"x", "y", "name"?: str}], "name"?: str, "x_name"?: str, "y_name"?: str}
    雷达        {"indicators": [{"name", "max"}], "series": [{"name", "data": [...]}]}
    仪表盘      {"value": 数值, "name"?: str}
    漏斗        {"categories" 或 "x": [...], "series": [{"name", "data": [...]}]}
    热力图      {"x": [...], "y": [...], "values": [[...], ...], "name"?: str}
    矩形树图    {"data": [{"name", "value", "children"?: [...]}]}
    箱线图      {"categories" 或 "x": [...], "series": [{"name", "data": [...]}]}
    瀑布图      {"categories" 或 "x": [...], "series": [{"name", "data": [...]}]}（首项为起点，其后为增量）
"""

from __future__ import annotations

from typing import Any

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


def _grid(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    grid: dict[str, Any] = {"left": 48, "right": 20, "top": 28, "bottom": 40, "containLabel": True}
    if extra:
        grid.update(extra)
    return grid


def _category_axis(categories: list[str], rotate: int = 0) -> dict[str, Any]:
    axis: dict[str, Any] = {"type": "category", "data": categories}
    if rotate:
        axis["axisLabel"] = {"rotate": rotate}
    return axis


def _value_axis(name: str = "") -> dict[str, Any]:
    return {"type": "value", "name": name}


def _label(show: bool = False) -> dict[str, Any]:
    if not show:
        return {"show": False}
    return {"show": True, "position": "top"}


def _legend(count: int) -> dict[str, Any]:
    return {
        "show": count > 1,
        "top": 0,
        "right": 0,
        "icon": "roundRect",
        "itemWidth": 8,
        "itemHeight": 8,
    }


# ---------------------------------------------------------------- 各图表类型
# 只描述结构（类型、数据、几何、布局、文案模板、显隐）；
# 颜色、字号、描边、阴影一律由渲染层主题注入。


def _build_line(data: dict[str, Any], area: bool) -> dict[str, Any]:
    xs = data.get("x")
    if not isinstance(xs, list) or len(xs) == 0:
        raise ChartError("data.x 必须是非空数组")
    series = _series_of(data)
    out_series: list[dict[str, Any]] = []
    for item in series:
        line: dict[str, Any] = {
            "name": item["name"],
            "type": "line",
            "data": item["data"],
            "smooth": True,
            "showSymbol": len(item["data"]) <= 30,
            "symbolSize": 4,
            "lineStyle": {"width": 1.6},
        }
        if area:
            line["areaStyle"] = {"opacity": 0.18}
        out_series.append(line)
    return {
        "tooltip": {"trigger": "axis"},
        "legend": _legend(len(out_series)),
        "grid": _grid(),
        "xAxis": _category_axis([str(v) for v in xs]),
        "yAxis": _value_axis(str(data.get("y_name") or "")),
        "series": out_series,
    }


def _build_bar(data: dict[str, Any], mode: str) -> dict[str, Any]:
    categories = _categories_of(data)
    series = _series_of(data)
    stacked = mode == "stacked_bar"
    out_series: list[dict[str, Any]] = []
    for item in series:
        bar: dict[str, Any] = {
            "name": item["name"],
            "type": "bar",
            "data": item["data"],
            "itemStyle": {"borderRadius": [0, 4, 4, 0] if mode == "hbar" else [4, 4, 0, 0]},
            "label": _label(not stacked and len(series) == 1),
        }
        if stacked:
            bar["stack"] = "total"
        if mode == "grouped_bar":
            bar["barGap"] = "10%"
        out_series.append(bar)
    horizontal = mode == "hbar"
    value_axis = _value_axis(str(data.get("y_name") or ""))
    cat_axis = _category_axis(categories, rotate=0 if horizontal else 30)
    return {
        "tooltip": {"trigger": "axis"},
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
    pie: dict[str, Any] = {
        "name": series[0]["name"] or "",
        "type": "pie",
        "radius": ["48%", "72%"] if donut else ["0%", "70%"],
        "center": ["50%", "56%"],
        "avoidLabelOverlap": True,
        "label": {"formatter": "{b}\n{d}%"},
        "data": [
            {"name": name, "value": value}
            for name, value in zip(categories, values, strict=True)
        ],
    }
    option: dict[str, Any] = {
        "tooltip": {"trigger": "item"},
        "legend": {"show": False},
        "series": [pie],
    }
    if donut:
        # 环心总数用 title 组件承载：布局在这里给，文字配色由主题 title 样式统一注入
        total = sum(v for v in values if v > 0)
        option["title"] = {
            "text": f"{total:g}",
            "subtext": series[0]["name"] or "",
            "left": "center",
            "top": "40%",
            "textAlign": "center",
        }
    return option


def _build_scatter(data: dict[str, Any]) -> dict[str, Any]:
    points = data.get("points")
    if not isinstance(points, list) or len(points) == 0:
        raise ChartError("data.points 必须是非空数组")
    series: dict[str, Any] = {
        "name": str(data.get("name") or ""),
        "type": "scatter",
        "symbolSize": 7,
        "itemStyle": {"opacity": 0.75},
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
        "tooltip": {"trigger": "item"},
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
    for item in series:
        out_series.append(
            {
                "name": item["name"],
                "type": "radar",
                "data": [item["data"]],
                "areaStyle": {"opacity": 0.16},
                "lineStyle": {"width": 1.6},
            }
        )
    return {
        "tooltip": {"trigger": "item"},
        "legend": _legend(len(out_series)),
        "radar": {"indicator": indicators, "splitArea": {"show": False}},
        "series": out_series,
    }


def _build_gauge(data: dict[str, Any]) -> dict[str, Any]:
    if "value" not in data:
        raise ChartError("data 缺少 value")
    value = _number(data["value"])
    name = str(data.get("name") or "")
    return {
        "tooltip": {"trigger": "item"},
        "series": [
            {
                "name": name,
                "type": "gauge",
                "min": 0,
                "max": 100,
                "progress": {"show": True, "width": 10},
                "axisLine": {"lineStyle": {"width": 10}},
                "axisTick": {"show": False},
                "splitLine": {"length": 8, "lineStyle": {"width": 1}},
                "pointer": {"length": "60%", "width": 3},
                "detail": {
                    "valueAnimation": True,
                    "formatter": "{value}",
                    "offsetCenter": [0, "62%"],
                },
                "title": {
                    "show": name != "",
                    "offsetCenter": [0, "84%"],
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
        "tooltip": {"trigger": "item"},
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
        "tooltip": {"position": "top"},
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
        },
        "series": [
            {
                "name": str(data.get("name") or ""),
                "type": "heatmap",
                "data": points,
                "label": {"show": True},
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
        "tooltip": {"trigger": "item"},
        "series": [
            {
                "type": "treemap",
                "data": convert(raw),
                "roam": False,
                "nodeClick": False,
                "breadcrumb": {"show": False},
                "itemStyle": {"gapWidth": 1},
                "upperLabel": {"show": True, "height": 18},
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
        "tooltip": {"trigger": "item"},
        "grid": _grid({"left": 60, "bottom": 60}),
        "xAxis": _category_axis(categories, rotate=30),
        "yAxis": _value_axis(str(data.get("y_name") or "")),
        "series": [
            {
                "name": series[0]["name"] or "",
                "type": "boxplot",
                "data": boxes,
            }
        ],
    }


def _build_waterfall(data: dict[str, Any]) -> dict[str, Any]:
    categories = _categories_of(data)
    series = _series_of(data)
    values = series[0]["data"]
    if len(values) != len(categories):
        raise ChartError("data.series[0].data 与 categories 长度不一致")
    # 第一格是起点，后续是增量；转成 ECharts 的透明底柱写法
    base: list[float] = []
    delta: list[float] = []
    running = 0.0
    for index, value in enumerate(values):
        if index == 0:
            running = value
            base.append(0.0)
            delta.append(value)
            continue
        start = running
        running += value
        base.append(min(start, running))
        delta.append(abs(value))
    return {
        "tooltip": {"trigger": "axis"},
        "grid": _grid({"left": 60, "bottom": 60}),
        "xAxis": _category_axis(categories, rotate=30),
        "yAxis": _value_axis(str(data.get("y_name") or "")),
        "series": [
            {
                "name": "base",
                "type": "bar",
                "stack": "wf",
                # 透明底柱是瀑布图的垫高结构（不属于配色），始终不可见
                "itemStyle": {"color": "transparent"},
                "emphasis": {"itemStyle": {"color": "transparent"}},
                "data": base,
            },
            {
                "name": series[0]["name"] or "",
                "type": "bar",
                "stack": "wf",
                "itemStyle": {"borderRadius": [3, 3, 0, 0]},
                "data": delta,
            },
        ],
    }


_BUILDERS = {
    "line": lambda data: _build_line(data, area=False),
    "area": lambda data: _build_line(data, area=True),
    "bar": lambda data: _build_bar(data, "bar"),
    "hbar": lambda data: _build_bar(data, "hbar"),
    "stacked_bar": lambda data: _build_bar(data, "stacked_bar"),
    "grouped_bar": lambda data: _build_bar(data, "grouped_bar"),
    "pie": lambda data: _build_pie(data, donut=False),
    "donut": lambda data: _build_pie(data, donut=True),
    "scatter": _build_scatter,
    "radar": _build_radar,
    "gauge": _build_gauge,
    "funnel": _build_funnel,
    "heatmap": _build_heatmap,
    "treemap": _build_treemap,
    "boxplot": _build_boxplot,
    "waterfall": _build_waterfall,
}


def build_chart_option(data: dict[str, Any], chart_type: str = "line") -> dict[str, Any]:
    """按图表类型把数据契约转成 ECharts option（纯结构，配色由渲染层主题注入）。"""

    if chart_type not in _BUILDERS:
        raise ChartError(f"未知图表类型: {chart_type}；可选：{', '.join(CHART_TYPES)}")
    data = _require_dict(data, "data")
    return _BUILDERS[chart_type](data)
