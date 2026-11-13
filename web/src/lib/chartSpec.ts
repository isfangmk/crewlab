// chartSpec — 识别对话正文里的图表围栏。
//
// 模型在回答正文里用 ```echarts 围栏给出 ECharts option，
// 前端把围栏内容渲染成图。这样图的内容来自模型正文，
// 位置、顺序与文字排在一起，比从工具结果里翻 JSON 更准。

/** 图表规格：ECharts option + 标题 + 一句话结论。 */
export type ChartSpec = {
  chart: string;
  title: string;
  option: Record<string, unknown>;
  summary: string;
};

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/**
 * 判断一个对象能不能当 ECharts option 用。
 *
 * ECharts 的 option 形状很松，只要至少有 series 或坐标系字段就能画；
 * 完全空的对象画出来是一片空白，所以要求至少命中一项。
 */
export function isChartOption(value: unknown): value is Record<string, unknown> {
  if (!isPlainObject(value)) return false;
  return (
    Array.isArray(value.series) ||
    isPlainObject(value.series) ||
    value.xAxis !== undefined ||
    value.yAxis !== undefined ||
    value.radar !== undefined ||
    value.visualMap !== undefined ||
    value.graph !== undefined
  );
}

/**
 * 解析围栏内容：既接受裸 option，也接受带 chart/title/summary 的完整规格。
 * 解析失败返回 null，调用方按普通代码块处理。
 */
export function parseChartSpec(text: string): ChartSpec | null {
  const trimmed = text.trim();
  if (trimmed === '' || !trimmed.startsWith('{')) return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return null;
  }
  if (!isPlainObject(parsed)) return null;

  // 完整规格：{chart, title, option, summary}
  if (isPlainObject(parsed.option) && isChartOption(parsed.option)) {
    return {
      chart: typeof parsed.chart === 'string' ? parsed.chart : 'chart',
      title: typeof parsed.title === 'string' ? parsed.title : '',
      option: parsed.option,
      summary: typeof parsed.summary === 'string' ? parsed.summary : '',
    };
  }

  // 裸 option：直接当 ECharts 配置用
  if (isChartOption(parsed)) {
    return { chart: 'chart', title: '', option: parsed, summary: '' };
  }

  return null;
}

/** 围栏的语言标记：只有 echarts / chart 两种写法会被当成图表。 */
export const CHART_FENCE_LANGS = ['echarts', 'chart'] as const;

export function isChartFence(className: string | undefined): boolean {
  if (className === undefined) return false;
  const match = /language-([\w-]+)/.exec(className);
  if (match === null) return false;
  return (CHART_FENCE_LANGS as readonly string[]).includes(match[1].toLowerCase());
}
