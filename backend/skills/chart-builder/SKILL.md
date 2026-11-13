---
name: chart-builder
description: >-
  把查询到的指标 / 统计结果画成图表。用户问趋势、对比、占比、分布类问题并已拿到数据时使用。
metadata:
  author: ai-workbench
  group: chart
  version: "1.0"
  risk: safe
---

# 图表绘制

用户问趋势 / 对比 / 占比 / 分布，且手上已经有数据时，用本技能把数据画成图。

## 使用方式

1. 先用当前可用的查询类工具拿到真实数据。
2. 用 `run_skill_script` 执行 `scripts/render_chart.py`：

   ```
   skill_name: chart-builder
   file_path: scripts/render_chart.py
   args: {"chart": "line", "title": "标题", "data": "{\"x\": [...], \"series\": [...]}"}
   ```

   `args` 是键值对：ADK 会把每个键展开成长选项（`--chart line --title "..." --data "..."`）传给脚本。
   `data` 传 JSON **字符串**，不要传嵌套对象，避免转义出错。

3. 脚本在标准输出打印一段 JSON，字段是 `chart` / `title` / `option`（ECharts 配置）与 `summary`（一句话结论）。
4. **把脚本打印的那段 JSON 原样放进正文里的 `echarts` 围栏**：

   ````
   ```echarts
   {"chart": "line", "title": "...", "option": {...}, "summary": "..."}
   ```
   ````

   前端会把围栏渲染成图表。围栏之外写结论与关键数字。

## 图表类型

`chart` 取以下之一（脚本会拒绝未知类型）：

| chart | 适用场景 | data 形状 |
| --- | --- | --- |
| `line` | 随时间变化的趋势 | `{"x": ["10:00", ...], "series": [{"name": "名称", "data": [1, 2]}]}` |
| `area` | 强调量级变化的趋势 | 同 `line` |
| `bar` | 类别间竖向对比 | `{"categories": ["a", "b"], "series": [{"name": "名称", "data": [1, 2]}]}` |
| `hbar` | 类别名长、数量多的横向对比 | 同 `bar` |
| `stacked_bar` | 多系列按类别堆叠 | 同 `bar` |
| `grouped_bar` | 多系列并排对比 | 同 `bar` |
| `pie` | 占比构成 | `{"categories": ["a", "b"], "series": [{"name": "名称", "data": [60, 40]}]}` |
| `donut` | 占比构成（带中心总量） | 同 `pie` |
| `scatter` | 两个数值变量的相关性 | `{"points": [{"x": 1, "y": 2, "name": "标签"}]}` |
| `radar` | 多维能力 / 指标画像 | `{"indicators": [{"name": "维度", "max": 100}], "series": [{"name": "名称", "data": [80, 60]}]}` |
| `gauge` | 单个百分比指标 | `{"value": 72, "name": "指标名"}` |
| `funnel` | 转化漏斗 | `{"categories": ["阶段一", "阶段二"], "series": [{"name": "名称", "data": [1000, 200]}]}` |
| `heatmap` | 二维矩阵 | `{"x": ["列"], "y": ["行"], "values": [[1, 2], [3, 4]]}` |
| `treemap` | 层级占比 | `{"data": [{"name": "目录", "value": 10, "children": []}]}` |
| `boxplot` | 数据分布（分位数） | `{"categories": ["分组"], "series": [{"name": "名称", "data": [10, 90]}]}` |
| `waterfall` | 增减累加的贡献拆解 | `{"categories": ["起点", "变化"], "series": [{"name": "名称", "data": [100, 20]}]}` |

可选的辅助字段：`y_name`（数值轴单位）、`x_name`、`name`（系列名）。

## 数据要求

- 数值必须是数字，不能写 `"80%"` 这种带单位的字符串；单位写在 `title` 或 `series[].name` 里。
- 类别名最多 30 个；更多请先聚合。
- 时间轴用等间隔的字符串（`"10:00"`、`"10:05"`），不要用日期对象。
- 没有数据就不要画图：明确告诉用户查不到什么。

## 注意事项

- 一次只画一张图。用户明确要对比多个维度时，分多次调用并在正文里分别说明。
- 图只是补充，正文仍要给出结论与数字，不能只丢一张图。
- 画图前先确认数据是自己查到的，不是用户口述的估算值。
- 围栏里的内容必须是可以直接解析的 JSON：不要加注释、不要用尾逗号、不要截断。
