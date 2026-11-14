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

/** 字面量（数字 / true / false / null）的完整形态；半截的不算值。 */
const LITERAL_PATTERN = /^(true|false|null|-?\d+(\.\d+)?([eE][+-]?\d+)?)$/;

/**
 * 从截断的 JSON 文本里恢复「最长完整前缀」：切掉半截的字符串/数字/字面量，
 * 补上未闭合的括号，返回可直接 JSON.parse 的文本。
 *
 * 逐字符扫描并跟踪 key/值位置：对象里的字符串在冒号前是 key 不是值，
 * 只有值位置上的完整值才能成为截断点。流式渲染用它把正在输出的
 * 半截图表 JSON 修成当前可画的形状，数据随 token 逐步补全；
 * 没有任何完整值时返回 ''。
 */
export function repairJsonPrefix(raw: string): string {
  const text = raw.trim();
  if (!text.startsWith('{') && !text.startsWith('[')) return '';

  let inString = false;
  let escaped = false;
  // 容器栈：'{' | '['；对象帧附带「是否在等 key」
  const stack: Array<'{' | '['> = [];
  const expectKeyStack: boolean[] = [];
  // 最后一个值位置上的完整值的结束位置，及其容器栈快照
  let cut = -1;
  let cutStack: Array<'{' | '['> = [];
  // 数字/true/false/null 进行中的起始下标
  let literalStart = -1;

  // 当前位置是不是「值」：顶层值、数组元素、对象冒号之后才算
  const inValuePosition = (): boolean => {
    if (stack.length === 0) return true;
    if (stack[stack.length - 1] === '[') return true;
    return !expectKeyStack[expectKeyStack.length - 1];
  };

  const markValue = (end: number) => {
    if (!inValuePosition()) return;
    cut = end;
    cutStack = stack.slice();
  };

  const closeLiteral = (end: number) => {
    if (literalStart < 0) return;
    if (LITERAL_PATTERN.test(text.slice(literalStart, end))) {
      markValue(end);
    }
    literalStart = -1;
  };

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (ch === '\\') {
        escaped = true;
      } else if (ch === '"') {
        inString = false;
        markValue(i + 1);
      }
      continue;
    }
    if (ch === '"') {
      closeLiteral(i);
      inString = true;
    } else if (ch === '{') {
      closeLiteral(i);
      stack.push('{');
      expectKeyStack.push(true);
    } else if (ch === '[') {
      closeLiteral(i);
      stack.push('[');
      expectKeyStack.push(false);
    } else if (ch === '}' || ch === ']') {
      closeLiteral(i);
      stack.pop();
      expectKeyStack.pop();
      markValue(i + 1);
    } else if (ch === ':') {
      closeLiteral(i);
      if (stack[stack.length - 1] === '{') {
        expectKeyStack[expectKeyStack.length - 1] = false;
      }
    } else if (ch === ',') {
      closeLiteral(i);
      if (stack[stack.length - 1] === '{') {
        expectKeyStack[expectKeyStack.length - 1] = true;
      }
    } else if (ch === ' ' || ch === '\n' || ch === '\r' || ch === '\t') {
      closeLiteral(i);
    } else if (literalStart < 0) {
      literalStart = i;
    }
  }
  // 文本正好停在一个完整字面量末尾（没有分隔符收尾）：也算值
  closeLiteral(text.length);

  if (cut <= 0) return '';
  let out = text.slice(0, cut);
  for (let i = cutStack.length - 1; i >= 0; i--) {
    out += cutStack[i] === '{' ? '}' : ']';
  }
  return out;
}

/**
 * 流式解析：把截断的 JSON 修复成当前最大完整前缀后再解析，
 * 图表可以边流出边画（数据随 token 补全）。
 * 修复后仍不成形时返回 null，调用方决定给骨架还是退回代码块。
 */
export function parseChartSpecStreaming(text: string): ChartSpec | null {
  const repaired = repairJsonPrefix(text);
  return parseChartSpec(repaired === '' ? text.trim() : repaired);
}
