// taskGraphMermaid — 任务图节点拓扑 → Mermaid 图文本。
//
// 唯一职责：把 nodes + depends_on 转成 flowchart 文本，节点按状态语义色着色，
// 边表达依赖（多前驱即汇合点）。渲染交给共享 MermaidGraph 组件，本模块不碰 DOM。

import type { TaskGraphNode } from '@/api/taskGraph';
import { NODE_STATUS_META } from '@/lib/taskGraphStatus';

// 与 MermaidGraph 主题无关：用较深的实色填充 + 白字，明暗主题下都清晰可读。
// color 必须显式指定，否则浅色主题下继承浅色文字会看不清；stroke-width 加粗强化轮廓。
const TONE_STYLE: Record<string, string> = {
  ok: 'fill:#0f7a5a,stroke:#0b5c43,color:#ffffff,stroke-width:1.5px',
  info: 'fill:#15698a,stroke:#0f506a,color:#ffffff,stroke-width:1.5px',
  warn: 'fill:#9a6708,stroke:#754d06,color:#ffffff,stroke-width:1.5px',
  danger: 'fill:#b3261e,stroke:#8a1d17,color:#ffffff,stroke-width:1.5px',
  neutral: 'fill:#4b5563,stroke:#374151,color:#ffffff,stroke-width:1.5px',
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

/** 生成 Mermaid flowchart 文本；TD 自上而下表达并行起点与汇合。
 *
 * callbackName 非空时为每个节点追加 click 指令，点击回调收到对应 node_uid。
 */
export function buildMermaidGraph(nodes: TaskGraphNode[], callbackName = ''): string {
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
  if (callbackName !== '') {
    for (const n of nodes) {
      // node_uid 可含连字符，用 JSON 字符串字面量安全传参。
      lines.push(`  click ${idByLocal.get(n.local_id)} call ${callbackName}(${JSON.stringify(n.node_uid)})`);
    }
  }
  return lines.join('\n');
}
