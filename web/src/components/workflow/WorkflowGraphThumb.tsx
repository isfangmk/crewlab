// 画布缩略图：列表行与运行详情共用。
//
// 用 SVG 画一张「结构图」，不是装饰：节点按 BFS 分层自动排布，
// 运行页给每个节点染色（成功绿 / 失败红 / 未执行灰），点一下定位到时间线。

import { useMemo } from 'react';
import type { NodeRunStatus, WorkflowPreview } from '@/api/workflows';
import { cn } from '@/lib/cn';
import { iconOf, layeredLayout, toneOf } from '@/lib/workflowMeta';
import { useThemeMode } from '@/store/mode';

type Props = {
  graph: WorkflowPreview;
  /** 运行快照：node_id → 执行结果。 */
  statuses?: Record<string, NodeRunStatus>;
  /** 停在人工节点等审批/填写：这个节点染成警示色，而不是成功色。 */
  gateId?: string | null;
  selectedId?: string | null;
  onSelect?(nodeId: string): void;
  className?: string;
};

const NODE_W = 108;
const NODE_H = 30;
const PAD = 14;

const PALETTE = {
  dark: { fill: '#18181b', stroke: '#3f3f46', text: '#e4e4e7', muted: '#71717a', edge: '#52525b' },
  light: { fill: '#ffffff', stroke: '#cbd5e1', text: '#1f2937', muted: '#94a3b8', edge: '#94a3b8' },
};

function clampLabel(label: string): string {
  // 中英混排长度差异大，按字符数截断足够（缩略图只表达结构）。
  const text = label.trim() === '' ? '' : label.trim();
  return text.length > 9 ? `${text.slice(0, 9)}…` : text;
}

export function WorkflowGraphThumb({ graph, statuses, gateId, selectedId, onSelect, className }: Props) {
  const { resolved } = useThemeMode();
  const colors = PALETTE[resolved];

  const layout = useMemo(() => ({ graph, positions: layeredLayout(graph) }), [graph]);

  const bounds = useMemo(() => {
    const points = graph.nodes.map((node) => layout.positions[node.id] ?? { x: 0, y: 0 });
    if (points.length === 0) return { minX: 0, minY: 0, width: 1, height: 1 };
    const xs = points.map((p) => p.x);
    const ys = points.map((p) => p.y);
    const minX = Math.min(...xs);
    const minY = Math.min(...ys);
    const maxX = Math.max(...xs) + NODE_W;
    const maxY = Math.max(...ys) + NODE_H;
    return {
      minX: minX - PAD,
      minY: minY - PAD,
      width: maxX - minX + PAD * 2,
      height: maxY - minY + PAD * 2,
    };
  }, [graph.nodes, layout.positions]);

  const labelOf = (id: string): string => {
    const node = graph.nodes.find((item) => item.id === id);
    if (!node) return id;
    return node.label.trim() !== '' ? node.label : id;
  };

  return (
    <svg
      viewBox={`${bounds.minX} ${bounds.minY} ${bounds.width} ${bounds.height}`}
      preserveAspectRatio="xMidYMid meet"
      className={cn('block h-full w-full', className)}
      role="img"
      aria-label="画布结构"
    >
      <defs>
        <marker id="wf-thumb-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M0,0 L8,4 L0,8 z" fill={colors.edge} />
        </marker>
      </defs>

      {graph.edges.map((edge, index) => {
        const from = layout.positions[edge.source];
        const to = layout.positions[edge.target];
        if (!from || !to) return null;
        const x1 = from.x + NODE_W;
        const y1 = from.y + NODE_H / 2;
        const x2 = to.x;
        const y2 = to.y + NODE_H / 2;
        const mid = (x1 + x2) / 2;
        const back = x2 <= x1;
        // 回边（循环）绕到下方，避免与正向边重叠成一团
        const d = back
          ? `M ${x1} ${y1} C ${x1 + 40} ${y1 + 46}, ${x2 - 40} ${y2 + 46}, ${x2} ${y2}`
          : `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`;
        return (
          <path
            key={`${edge.source}-${edge.target}-${edge.route}-${index}`}
            d={d}
            fill="none"
            stroke={colors.edge}
            strokeWidth={1.3}
            markerEnd="url(#wf-thumb-arrow)"
            strokeDasharray={back ? '4 3' : undefined}
          />
        );
      })}

      {graph.nodes.map((node) => {
        const position = layout.positions[node.id];
        if (!position) return null;
        const tone = toneOf(node.kind);
        const status = statuses?.[node.id];
        const stroke =
          node.id === gateId
            ? '#f59e0b'
            : status === 'succeeded'
              ? '#10b981'
              : status === 'failed'
                ? '#ef4444'
                : colors.stroke;
        const active = selectedId === node.id;
        const Icon = iconOf(node.kind);
        return (
          <g
            key={node.id}
            transform={`translate(${position.x} ${position.y})`}
            onClick={onSelect ? () => onSelect(node.id) : undefined}
            className={onSelect ? 'cursor-pointer' : undefined}
          >
            <rect
              width={NODE_W}
              height={NODE_H}
              rx={8}
              fill={colors.fill}
              stroke={active ? '#6366f1' : stroke}
              strokeWidth={active ? 2 : 1.2}
            />
            <rect width={3} height={NODE_H} rx={1.5} fill={tone.dot} />
            <g transform={`translate(9 ${(NODE_H - 11) / 2})`} style={{ color: tone.dot }}>
              <Icon width={11} height={11} />
            </g>
            <text x={25} y={NODE_H / 2 + 3.5} fontSize={9.5} fill={colors.text}>
              {clampLabel(labelOf(node.id))}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
