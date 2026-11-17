// 汇总正文的图表锚点：把 [[图:标题关键词]] 解析成正文分段，图表插到对应段落而非堆末尾。
// 纯函数，不依赖组件；artifact 用泛型，调用方传入实际产出物对象即可。

export type BodySegment<T> =
  | { type: 'text'; text: string }
  | { type: 'chart'; artifact: T };

const ANCHOR_RE = /\[\[图[:：]\s*([^\]]+?)\s*\]\]/g;

/** 宽松匹配：关键词字符按顺序出现在标题里即可（忽略标点/空格/词序间隔）。
 * 例：'拓扑边错误率' 能命中 '链路拓扑各边错误率与调用量'。 */
function keywordMatches(keyword: string, title: string): boolean {
  const norm = (s: string) => s.replace(/[\s，,。.（）()·、:：\-_]/g, '').toLowerCase();
  const k = norm(keyword);
  const t = norm(title);
  if (!k) return false;
  if (t.includes(k)) return true;
  let i = 0;
  for (const ch of t) {
    if (ch === k[i]) i += 1;
    if (i === k.length) return true;
  }
  return false;
}

/**
 * 按正文里的 [[图:关键词]] 把文本切成 text/chart 交错段。
 *
 * - 每个锚点按关键词匹配一个标题命中的、尚未使用的产出物（按出现顺序）；
 * - 匹配不到就把锚点原文保留在文本里，不吞内容；
 * - 未被任何锚点使用的产出物按原顺序追加到末尾，保证旧行为兜底。
 */
export function segmentBodyWithCharts<T extends { title?: string }>(
  body: string,
  artifacts: T[],
): BodySegment<T>[] {
  const used = new Set<number>();
  const segments: BodySegment<T>[] = [];
  let cursor = 0;
  for (const match of body.matchAll(ANCHOR_RE)) {
    const index = match.index ?? 0;
    if (index > cursor) {
      segments.push({ type: 'text', text: body.slice(cursor, index) });
    }
    const keyword = (match[1] ?? '').trim();
    const found = keyword
      ? artifacts.findIndex(
          (a, i) => !used.has(i) && !!a.title && keywordMatches(keyword, a.title),
        )
      : -1;
    if (found >= 0) {
      used.add(found);
      segments.push({ type: 'chart', artifact: artifacts[found] });
    } else {
      // 未匹配到产出物：锚点保留为普通文本，避免丢失用户可见内容
      segments.push({ type: 'text', text: match[0] });
    }
    cursor = index + match[0].length;
  }
  if (cursor < body.length) {
    segments.push({ type: 'text', text: body.slice(cursor) });
  }
  artifacts.forEach((artifact, i) => {
    if (!used.has(i)) segments.push({ type: 'chart', artifact });
  });
  return segments;
}
