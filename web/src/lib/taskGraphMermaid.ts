// taskGraphMermaid — 任务图节点拓扑 → Mermaid 图文本。
//
// 唯一职责：把 nodes + depends_on 转成 flowchart 文本，节点按状态语义色着色，
// 边表达依赖（多前驱即汇合点）。渲染交给共享 MermaidGraph 组件，本模块不碰 DOM。

import type { TaskGraphNode } from '@/api/taskGraph';
import { NODE_STATUS_META } from '@/lib/taskGraphStatus';

// 与 MermaidGraph TOKENS 一致（fill 用半透明让深色底透气，stroke 用实色）。
const TONE_STYLE: Record<string, string> = {
  ok: 'fill:#10b98133,stroke:#10b981,color:#e4e4e7',
  info: 'fill:#30a6d033,stroke:#30a6d0,color:#e4e4e7',
  warn: 'fill:#f59e0b33,stroke:#f59e0b,color:#e4e4e7',
  danger: 'fill:#ef444433,stroke:#ef4444,color:#e4e4e7',
  neutral: 'fill:#3f3f4666,stroke:#71717a,color:#e4e4e7',
};

/** mermaid 节点 id 只保留字母数字下划线，非法字符替换并去重。 */
function safeId(raw: string, used: Set<string>): string {
  let base = raw.replace(/[^a-zA-Z0-9_]/g, '_');
  if (base === '' || /^[0-9]/.test(base)) base = `n_${base}`;
  let id = base;
  let i = 2;
  while (used.has(id)) id = `${base}_${i++}`;
  used.add(id);
  return id;
}

/** 节点显示名做 mermaid 标签转义：引号改空格，反斜杠去除，截断避免超宽。 */
function label(text: string, fallback: string): string {
  const clean = (text || fallback).replace(/["\\]/g, ' ').trim().slice(0, 24);
  return clean || fallback;
}

/** 生成 Mermaid flowchart 文本；TD 自上而下表达并行起点与汇合。 */
export function buildMermaidGraph(nodes: TaskGraphNode[]): string {
  if (nodes.length === 0) return '';
  const used = new Set<string>();
  const idByLocal = new Map<string, string>();
  for (const n of nodes) idByLocal.set(n.local_id, safeId(n.local_id, used));

  const lines: string[] = ['flowchart TD'];
  for (const n of nodes) {
    const id = idByLocal.get(n.local_id)!;
    lines.push(`  ${id}["${label(n.name, n.local_id)}"]`);
  }
  for (const n of nodes) {
    const target = idByLocal.get(n.local_id)!;
    for (const dep of n.depends_on) {
      const source = idByLocal.get(dep);
      if (source) lines.push(`  ${source} --> ${target}`);
    }
  }
  // 按当前状态给节点着色：一眼看出并行起跑、运行中、失败与已完成。
  for (const n of nodes) {
    const tone = (NODE_STATUS_META[n.status] ?? NODE_STATUS_META.pending).tone;
    const style = TONE_STYLE[tone] ?? TONE_STYLE.neutral;
    lines.push(`  style ${idByLocal.get(n.local_id)} ${style}`);
  }
  return lines.join('\n');
}
