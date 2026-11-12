// 画布连线：三阶贝塞尔（curvature 0.16、2px 灰线、无箭头）。
//
// 交互上多做了一件事：鼠标悬到线上时中点浮出「+」，点开可以在两个节点之间
// 直接插入一个节点，免得为了插一个步骤先删线再补两条。

import { useMemo, useState } from 'react';
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  Position,
  type Edge,
  type EdgeProps,
} from '@xyflow/react';
import { Plus, Search } from 'lucide-react';
import type { CatalogKind, NodeKind } from '@/api/workflows';
import { cn } from '@/lib/cn';
import { useThemeMode } from '@/store/mode';
import { iconOf, toneOf } from '@/lib/workflowMeta';
import { useI18n } from '@/i18n/locale';

export type WorkflowEdgeData = {
  /** switch / loop 的分支名，普通连线为空。 */
  route: string;
  /** 允许在连线中间插入的节点类型（已排除开始/结束）。 */
  kinds: CatalogKind[];
  onInsert(kind: NodeKind): void;
};

export type CanvasFlowEdge = Edge<WorkflowEdgeData, 'workflow'>;

export function WorkflowEdge({
  id,
  data,
  sourceX,
  sourceY,
  targetX,
  targetY,
}: EdgeProps<CanvasFlowEdge>) {
  const { tr, locale } = useI18n();
  const zh = locale === 'zh-CN';
  const { resolved } = useThemeMode();
  const [hovered, setHovered] = useState(false);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');

  const [path, labelX, labelY] = getBezierPath({
    sourceX: sourceX - 2,
    sourceY,
    sourcePosition: Position.Right,
    targetX: targetX + 2,
    targetY,
    targetPosition: Position.Left,
    curvature: 0.16,
  });

  const active = hovered || open;
  const stroke = active ? '#6366f1' : resolved === 'light' ? '#cbd5e1' : '#3f3f46';
  const filtered = useMemo(() => {
    const list = data?.kinds ?? [];
    const q = query.trim().toLowerCase();
    if (q === '') return list;
    return list.filter((kind) => {
      const title = zh ? kind.title : kind.title_en;
      return title.toLowerCase().includes(q) || kind.key.includes(q);
    });
  }, [data, query, zh]);

  return (
    <g onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}>
      <BaseEdge
        id={id}
        path={path}
        interactionWidth={24}
        style={{ stroke, strokeWidth: 2, strokeLinecap: 'round', transition: 'stroke 120ms' }}
      />

      {data !== undefined && data.route !== '' && (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan pointer-events-none absolute rounded-md border border-border/70 bg-card px-1.5 py-[1px] font-mono text-[10px] leading-4 text-text-muted shadow-sm"
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY - 16}px)` }}
          >
            {data.route}
          </div>
        </EdgeLabelRenderer>
      )}

      {data !== undefined && (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan pointer-events-none absolute"
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          >
            <button
              type="button"
              aria-label={tr('在连线中间插入节点', 'Insert a node on this edge')}
              aria-expanded={open}
              onMouseEnter={() => setHovered(true)}
              onMouseLeave={() => setHovered(false)}
              onClick={() => {
                setOpen((current) => !current);
                setQuery('');
              }}
              className={cn(
                'pointer-events-auto flex h-5 w-5 items-center justify-center rounded-full border bg-card text-text-muted shadow-sm transition-all duration-150 hover:scale-110 hover:border-indigo-500 hover:text-indigo-500',
                active ? 'border-border opacity-100' : 'border-transparent opacity-0',
              )}
            >
              <Plus size={12} strokeWidth={2.5} aria-hidden />
            </button>

            {open && (
              <div
                className="pointer-events-auto absolute left-1/2 top-7 z-20 w-[212px] -translate-x-1/2 overflow-hidden rounded-xl border border-border bg-card shadow-xl"
                onMouseEnter={() => setHovered(true)}
                onMouseLeave={() => setHovered(false)}
              >
                <div className="flex items-center gap-1.5 border-b border-border/70 px-2 py-1.5">
                  <Search size={12} className="shrink-0 text-text-faint" aria-hidden />
                  <input
                    autoFocus
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Escape') setOpen(false);
                    }}
                    placeholder={tr('插入哪种节点', 'Node type to insert')}
                    aria-label={tr('搜索要插入的节点', 'Search node types')}
                    className="h-5 w-full bg-transparent text-[11.5px] text-text placeholder:text-text-faint focus:outline-none"
                  />
                </div>
                <div className="max-h-[228px] overflow-y-auto p-1">
                  {filtered.map((kind) => {
                    const Icon = iconOf(kind.key);
                    const tone = toneOf(kind.key);
                    return (
                      <button
                        key={kind.key}
                        type="button"
                        onClick={() => {
                          setOpen(false);
                          data.onInsert(kind.key);
                        }}
                        className="flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left transition-colors hover:bg-zinc-500/10"
                      >
                        <span className={cn('flex h-5 w-5 shrink-0 items-center justify-center rounded-[5px]', tone.iconBox)}>
                          <Icon size={11} className={tone.icon} aria-hidden />
                        </span>
                        <span className="truncate text-[11.5px] text-text">{zh ? kind.title : kind.title_en}</span>
                      </button>
                    );
                  })}
                  {filtered.length === 0 && (
                    <div className="px-2 py-3 text-center text-[11px] text-text-faint">{tr('没有匹配的节点', 'No matching node')}</div>
                  )}
                </div>
              </div>
            )}
          </div>
        </EdgeLabelRenderer>
      )}
    </g>
  );
}

/** 拖线预览：和正式连线同一条贝塞尔，末端加一个小方块表示落点。 */
export function WorkflowConnectionLine({
  fromX,
  fromY,
  toX,
  toY,
}: {
  fromX: number;
  fromY: number;
  toX: number;
  toY: number;
}) {
  const [path] = getBezierPath({
    sourceX: fromX,
    sourceY: fromY,
    sourcePosition: Position.Right,
    targetX: toX,
    targetY: toY,
    targetPosition: Position.Left,
    curvature: 0.16,
  });
  return (
    <g>
      <path fill="none" stroke="#6366f1" strokeWidth={2} strokeDasharray="6 4" d={path} />
      <circle cx={toX} cy={toY} r={3} fill="#6366f1" />
    </g>
  );
}
