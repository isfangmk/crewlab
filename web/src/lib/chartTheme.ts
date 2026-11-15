// chartTheme — ECharts 主题：图表配色与文字样式的唯一来源。
//
// 图表结构（backend/app/tools/chart_spec.py）零配色，所有颜色、字号、
// 描边、阴影都在这里定义，经 echarts.registerTheme 注册后由 ChartCanvas
// 按主题初始化画布。改配色只改这一个文件。

// 主题名：ChartCanvas 按它初始化画布
export const CHART_THEME_NAME = 'crewlab';

// 图表配色：暗色底上都能看清，且相邻色相拉开；按 series 顺序自动分配
const PALETTE = [
  '#6366f1',
  '#22d3ee',
  '#34d399',
  '#fbbf24',
  '#fb7185',
  '#a78bfa',
  '#f472b6',
  '#38bdf8',
];

const TEXT = '#a1a1aa';
const TEXT_STRONG = '#e4e4e7';
const AXIS_LINE = '#3f3f46';
const SPLIT_LINE = '#27272a';
const TOOLTIP_BG = '#18181b';
// 分片描边：贴合暗底，让饼/漏斗/树图的分块边界透气
const SLICE_BORDER = '#18181b';
// 热力图色带：深靛 → 靛蓝 → 青，语义上由深到浅表示数值增大
const HEAT_RAMP = ['#312e81', '#4f46e5', '#22d3ee'];

export const CHART_THEME: Record<string, unknown> = {
  color: PALETTE,
  backgroundColor: 'transparent',
  textStyle: { color: TEXT_STRONG },
  // 环形图环心总数（chart_spec 用 title 组件承载，布局在结构层）
  title: {
    textStyle: { color: TEXT_STRONG, fontSize: 20 },
    subtextStyle: { color: TEXT, fontSize: 10 },
  },
  legend: { textStyle: { color: TEXT, fontSize: 10 } },
  tooltip: {
    backgroundColor: TOOLTIP_BG,
    borderColor: AXIS_LINE,
    textStyle: { color: TEXT_STRONG, fontSize: 11 },
  },
  categoryAxis: {
    axisLine: { lineStyle: { color: AXIS_LINE } },
    axisTick: { show: false },
    axisLabel: { color: TEXT, fontSize: 10, hideOverlap: true },
    splitLine: { show: false },
  },
  valueAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: TEXT, fontSize: 10 },
    splitLine: { lineStyle: { color: SPLIT_LINE } },
    nameTextStyle: { color: TEXT, fontSize: 10 },
  },
  visualMap: {
    textStyle: { color: TEXT, fontSize: 10 },
    inRange: { color: HEAT_RAMP },
  },
  bar: { label: { color: TEXT, fontSize: 10 } },
  pie: {
    itemStyle: { borderColor: SLICE_BORDER, borderWidth: 1 },
    label: { color: TEXT, fontSize: 10 },
  },
  funnel: {
    itemStyle: { borderColor: SLICE_BORDER, borderWidth: 1 },
    label: { color: TEXT_STRONG, fontSize: 10 },
  },
  treemap: {
    itemStyle: { borderColor: SLICE_BORDER, borderWidth: 1 },
    label: { color: TEXT_STRONG, fontSize: 10 },
    upperLabel: { color: TEXT, fontSize: 10 },
  },
  // 箱体描边与填充同色（调色板第一色），保持干净的单色块面
  boxplot: { itemStyle: { borderColor: PALETTE[0] } },
  heatmap: {
    // 热力格子上的数值：深色带上用白字对比度最好
    label: { color: '#fff', fontSize: 9 },
    emphasis: { itemStyle: { shadowBlur: 6, shadowColor: 'rgba(0,0,0,0.4)' } },
  },
  radar: {
    axisName: { color: TEXT, fontSize: 10 },
    splitLine: { lineStyle: { color: SPLIT_LINE } },
    axisLine: { lineStyle: { color: SPLIT_LINE } },
  },
  gauge: {
    progress: { itemStyle: { color: PALETTE[0] } },
    axisLine: { lineStyle: { color: [[1, SPLIT_LINE]] } },
    splitLine: { lineStyle: { color: AXIS_LINE } },
    axisLabel: { color: TEXT, fontSize: 9 },
    pointer: { itemStyle: { color: TEXT_STRONG } },
    detail: { color: TEXT_STRONG, fontSize: 22 },
    title: { color: TEXT, fontSize: 10 },
  },
};
