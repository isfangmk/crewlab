// 工作流节点元信息：图标、配色、默认值、摘要文案与布局。
//
// 画布、节点库、运行时间线、缩略图都从这里取同一份「kind → 外观」映射，
// 避免同一类节点在四个地方出现四种颜色。

import type { IconType } from '@/lib/icon';
import type { CatalogKind, NodeKind, WorkflowDefinition, WorkflowNode } from '@/api/workflows';
import {
  ArrowRightLeft,
  BadgeCheck,
  Bot,
  Circle,
  CircleDot,
  Code2,
  Database,
  GitBranch,
  Globe,
  Merge,
  Play,
  Repeat,
  UserCheck,
  Variable,
  Wrench,
} from 'lucide-react';

/** 四个分组决定节点配色：编排中性、模型强调、动作青绿、人工琥珀。 */
export type NodeGroupTone = 'flow' | 'model' | 'action' | 'human';

export type ToneStyle = {
  /** 图标块底色 + 前景（画布节点、节点库、时间线共用）。 */
  iconBox: string;
  icon: string;
  /** 选中/悬停时的描边色。 */
  ring: string;
  /** 缩略图与时间线用的纯色点。 */
  dot: string;
  /** 画布连线高亮。 */
  stroke: string;
};

// 画布视觉参考 Dify：图标是实色小方块 + 白色字形，画布本身保持中性，
// 颜色只出现在图标块和缩略图里，避免整张图变成调色盘。
export const TONE_STYLE: Record<NodeGroupTone, ToneStyle> = {
  flow: {
    iconBox: 'bg-slate-500',
    icon: 'text-white',
    ring: 'ring-slate-400/50',
    dot: '#64748b',
    stroke: '#94a3b8',
  },
  model: {
    iconBox: 'bg-indigo-500',
    icon: 'text-white',
    ring: 'ring-indigo-500/60',
    dot: '#6366f1',
    stroke: '#818cf8',
  },
  action: {
    iconBox: 'bg-emerald-500',
    icon: 'text-white',
    ring: 'ring-emerald-500/60',
    dot: '#10b981',
    stroke: '#34d399',
  },
  human: {
    iconBox: 'bg-amber-500',
    icon: 'text-white',
    ring: 'ring-amber-500/60',
    dot: '#f59e0b',
    stroke: '#fbbf24',
  },
};

export const NODE_ICON: Record<NodeKind, IconType> = {
  start: Play,
  end: CircleDot,
  join: Merge,
  switch: GitBranch,
  loop: Repeat,
  agent: Bot,
  knowledge: Database,
  search: Globe,
  mcp: Wrench,
  http: ArrowRightLeft,
  code: Code2,
  assign: Variable,
  approval: BadgeCheck,
  input: UserCheck,
};

/** 分组归属；与后端 NODE_KINDS 的分组一致，这里只用于配色。 */
export const NODE_TONE: Record<NodeKind, NodeGroupTone> = {
  start: 'flow',
  end: 'flow',
  join: 'flow',
  switch: 'flow',
  loop: 'flow',
  agent: 'model',
  knowledge: 'action',
  search: 'action',
  mcp: 'action',
  http: 'action',
  code: 'action',
  assign: 'action',
  approval: 'human',
  input: 'human',
};

/** 唯一入口：节点的配色。 */
export function toneOf(kind: NodeKind): ToneStyle {
  return TONE_STYLE[NODE_TONE[kind]];
}

export function iconOf(kind: NodeKind): IconType {
  return NODE_ICON[kind] ?? Circle;
}

/** 节点库里的中文/英文标题：优先用后端目录，缺失时回退到本地常量。 */
const FALLBACK_TITLE: Record<NodeKind, [string, string]> = {
  start: ['开始', 'Start'],
  end: ['结束', 'End'],
  join: ['汇合', 'Join'],
  switch: ['条件分支', 'Switch'],
  loop: ['循环', 'Loop'],
  agent: ['LLM 节点', 'LLM node'],
  knowledge: ['知识检索', 'Knowledge search'],
  search: ['联网搜索', 'Web search'],
  mcp: ['MCP 工具', 'MCP tool'],
  http: ['HTTP 请求', 'HTTP request'],
  code: ['代码', 'Code'],
  assign: ['变量赋值', 'Set variables'],
  approval: ['人工审批', 'Approval'],
  input: ['人工输入', 'User input'],
};

export function kindTitle(kind: NodeKind, catalog?: CatalogKind[] | null, zh = true): string {
  const fromCatalog = catalog?.find((item) => item.key === kind);
  if (fromCatalog) return zh ? fromCatalog.title : fromCatalog.title_en;
  const fallback = FALLBACK_TITLE[kind];
  return zh ? fallback[0] : fallback[1];
}

/** 会自动产出结果的节点：结果变量名直接取节点 ID，用户不需要自己起名。 */
const OUTPUT_KINDS: ReadonlySet<NodeKind> = new Set<NodeKind>([
  'agent',
  'knowledge',
  'search',
  'mcp',
  'http',
  'code',
  'assign',
]);

/** 节点结果写回共享状态用的变量名；空字符串表示这个节点不写回。 */
export function autoOutputKey(kind: NodeKind, id: string): string {
  return OUTPUT_KINDS.has(kind) ? id : '';
}

/** 老定义里 output_key 是空的，加载时补成自动变量名，用户不必关心这个字段。 */
export function withAutoOutputKeys<T extends { nodes: WorkflowNode[] }>(definition: T): T {
  return {
    ...definition,
    nodes: definition.nodes.map((node) => {
      const auto = autoOutputKey(node.kind, node.id);
      return node.output_key === '' && auto !== '' ? { ...node, output_key: auto } : node;
    }),
  };
}

/**
 * 画布上能被 {{变量}} 引用的全部名字：开始节点的输入变量 + 各节点的结果变量。
 * 用于给结束节点挑「运行输出」，用户从下拉里选，不用记变量名。
 */
export function availableVariables(definition: WorkflowDefinition, excludeNodeId: string): string[] {
  const names: string[] = [];
  for (const node of definition.nodes) {
    if (node.kind === 'start') {
      for (const variable of node.variables) names.push(variable.name);
      continue;
    }
    if (node.id !== excludeNodeId && node.output_key !== '') names.push(node.output_key);
  }
  return [...new Set(names)];
}

/** 每类节点在画布上的落地默认值；必须让后端「不属于该 kind 的字段为空」。 */
export function emptyNode(kind: NodeKind, id: string, position: { x: number; y: number }): WorkflowNode {
  return {
    id,
    kind,
    label: '',
    x: position.x,
    y: position.y,
    output_key: autoOutputKey(kind, id),
    variables: [],
    provider: '',
    model: '',
    instruction: '',
    query: '',
    limit: 5,
    paths: [],
    mcp_server: '',
    mcp_tool: '',
    arguments: {},
    method: 'GET',
    url: '',
    headers: {},
    body: '',
    code: kind === 'code' ? 'def run(state):\n    return state\n' : '',
    assignments: {},
    branches: kind === 'switch' ? ['yes', 'no'] : [],
    expression: '',
    branch_mode: 'expr',
    max_iterations: 5,
    prompt: '',
    timeout_seconds: null,
    retry_count: 0,
  };
}

/** 新节点 ID：同 kind 递增，避开已有 ID。 */
export function nextNodeId(kind: NodeKind, nodes: WorkflowNode[]): string {
  const used = new Set(nodes.map((node) => node.id));
  if (!used.has(kind)) return kind;
  let index = 2;
  while (used.has(`${kind}_${index}`)) index += 1;
  return `${kind}_${index}`;
}

export const NODE_ID_PATTERN = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
export const VAR_PATTERN = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;

/** 节点自身是否填全（只做必填项提示，权威校验走 /workflows/validate）。 */
export function nodeIssue(node: WorkflowNode, zh = true): string | null {
  const need = (value: string) => value.trim() === '';
  switch (node.kind) {
    case 'start':
      if (node.variables.length === 0) return zh ? '需要至少一个输入变量' : 'Add at least one input variable';
      return null;
    case 'agent':
      if (need(node.provider) || need(node.model)) return zh ? '请选择模型' : 'Pick a model';
      if (need(node.instruction)) return zh ? '请填写指令' : 'Instruction required';
      return null;
    case 'knowledge':
      return need(node.query) ? (zh ? '请填写检索语句' : 'Query required') : null;
    case 'search':
      return need(node.query) ? (zh ? '请填写搜索语句' : 'Query required') : null;
    case 'mcp':
      if (need(node.mcp_server) || need(node.mcp_tool)) return zh ? '请选择 MCP 工具' : 'Pick an MCP tool';
      return null;
    case 'http':
      return need(node.url) ? (zh ? '请填写 URL' : 'URL required') : null;
    case 'code':
      return need(node.code) ? (zh ? '请填写代码' : 'Code required') : null;
    case 'assign':
      return Object.keys(node.assignments).length === 0 ? (zh ? '至少一个赋值' : 'Add an assignment') : null;
    case 'switch':
      if (node.branches.length < 2) return zh ? '至少两个分支' : 'Two branches minimum';
      if (node.branch_mode === 'expr' && need(node.expression)) return zh ? '请填写判断表达式' : 'Expression required';
      return null;
    case 'approval':
      return need(node.prompt) ? (zh ? '请填写审批说明' : 'Prompt required') : null;
    case 'input':
      return need(node.prompt) ? (zh ? '请填写提示文案' : 'Prompt required') : null;
    default:
      return null;
  }
}

/** 画布节点副标题：一句话说清这个节点在做什么。 */
export function nodeSummary(node: WorkflowNode, zh = true): string {
  switch (node.kind) {
    case 'start':
      return node.variables.length === 0
        ? zh ? '未声明输入变量' : 'No input variables'
        : node.variables.map((v) => v.name).join(', ');
    case 'agent':
      return node.model === '' ? (zh ? '未选择模型' : 'No model') : node.model;
    case 'knowledge':
    case 'search':
      return node.query.trim() === '' ? (zh ? '未填写语句' : 'No query') : node.query;
    case 'mcp':
      return node.mcp_server === '' ? zh ? '未选择工具' : 'No tool' : `${node.mcp_server} · ${node.mcp_tool}`;
    case 'http':
      return node.url.trim() === '' ? zh ? '未填写 URL' : 'No URL' : `${node.method} ${node.url}`;
    case 'code': {
      const first = node.code.split('\n').find((line) => line.trim() !== '') ?? '';
      return first === '' ? zh ? '未填写代码' : 'No code' : first.trim();
    }
    case 'assign': {
      const keys = Object.keys(node.assignments);
      return keys.length === 0 ? zh ? '未设置赋值' : 'No assignment' : keys.map((k) => `${k} = …`).join(', ');
    }
    case 'switch':
      return node.branches.length === 0 ? zh ? '未设置分支' : 'No branches' : node.branches.join(' / ');
    case 'loop':
      return node.expression.trim() === ''
        ? zh ? `最多 ${node.max_iterations} 轮` : `Up to ${node.max_iterations} rounds`
        : `${node.expression} · ≤${node.max_iterations}`;
    case 'approval':
    case 'input':
      return node.prompt.trim() === '' ? zh ? '未填写文案' : 'No prompt' : node.prompt;
    case 'end':
      // 输出变量已在下方的 → key 徽标里展示，摘要只说行为，避免同一句话出现两次。
      return node.output_key === ''
        ? zh ? '输出整份状态' : 'Outputs whole state'
        : zh ? '运行输出取该变量' : 'Run output is this variable';
    default:
      return zh ? '汇聚上游结果' : 'Merges upstream';
  }
}

/** 连线标签：switch / loop 的 route 就是分支名。 */
export function edgeLabel(route: string): string | null {
  return route === '' ? null : route;
}

const COLUMN_GAP = 300;
const ROW_GAP = 132;

/**
 * 分层布局：从开始节点做 BFS，同层纵向排列。
 * 画布「整理布局」与列表/运行页缩略图共用，保证两边看起来是同一张图。
 */
export function layeredLayout(graph: {
  nodes: { id: string; kind?: NodeKind }[];
  edges: { source: string; target: string }[];
}): Record<string, { x: number; y: number }> {
  const definition = graph;
  const start = definition.nodes.find((node) => node.kind === 'start');
  const adjacency = new Map<string, string[]>();
  for (const node of definition.nodes) adjacency.set(node.id, []);
  for (const edge of definition.edges) {
    adjacency.get(edge.source)?.push(edge.target);
  }

  const depth = new Map<string, number>();
  if (start) {
    const queue: string[] = [start.id];
    depth.set(start.id, 0);
    while (queue.length > 0) {
      const current = queue.shift() as string;
      const nextDepth = (depth.get(current) ?? 0) + 1;
      for (const target of adjacency.get(current) ?? []) {
        if (depth.has(target)) continue;
        depth.set(target, nextDepth);
        queue.push(target);
      }
    }
  }
  // 不可达节点（保存会被拒绝，但编辑过程中会短暂出现）单独排在最右列。
  const orphanDepth = Math.max(0, ...Array.from(depth.values())) + 1;
  for (const node of definition.nodes) {
    if (!depth.has(node.id)) depth.set(node.id, orphanDepth);
  }

  const columns = new Map<number, string[]>();
  for (const node of definition.nodes) {
    const d = depth.get(node.id) ?? 0;
    const bucket = columns.get(d) ?? [];
    bucket.push(node.id);
    columns.set(d, bucket);
  }

  const positions: Record<string, { x: number; y: number }> = {};
  for (const [d, ids] of columns) {
    ids.forEach((id, index) => {
      positions[id] = {
        x: d * COLUMN_GAP,
        y: (index - (ids.length - 1) / 2) * ROW_GAP,
      };
    });
  }
  return positions;
}

/** {{变量}} 在文本里出现的地方，用于「这个节点引用了谁」提示。 */
export function placeholdersIn(text: string): string[] {
  const out: string[] = [];
  const pattern = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g;
  let match = pattern.exec(text);
  while (match !== null) {
    out.push(match[1]);
    match = pattern.exec(text);
  }
  return out;
}
