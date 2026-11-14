// 画布连线：三阶贝塞尔（curvature 0.16、2px 灰线），末端带实心箭头。
//
// 交互上多做了一件事：鼠标悬到线上时中点浮出「+」，点开可以在两个节点之间
// 直接插入一个节点，免得为了插一个步骤先删线再补两条。

import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  Position,
  useViewport,
  type Edge,
  type EdgeProps,
} from '@xyflow/react';
import { Plus, Search } from 'lucide-react';
import type { CatalogKind, NodeKind } from '@/api/workflows';
import { cn } from '@/lib/cn';
import { useThemeMode } from '@/store/mode';
import { filterKinds, iconOf, toneOf } from '@/lib/workflowMeta';
import { EDGE_ACTIVE_COLOR, EDGE_COLOR } from '@/components/workflow/canvasTheme';
import { useI18n } from '@/i18n/locale';

export type WorkflowEdgeData = {
  /** switch / loop 的分支名，普通连线为空。 */
  route: string;
  /** 允许在连线中间插入的节点类型（已排除开始/结束）。 */
  kinds: CatalogKind[];
  onInsert(kind: NodeKind): void;
};

export type CanvasFlowEdge = Edge<WorkflowEdgeData, 'workflow'>;

/** 弹窗与锚点、视口边缘之间留出的间距。 */
const POPUP_GAP = 8;
const VIEWPORT_MARGIN = 8;

export type EdgePopupAnchor = { left: number; top: number; bottom: number; width: number };
export type EdgePopupSize = { width: number; height: number };
export type EdgePopupViewport = { width: number; height: number };

/**
 * 计算插入节点面板的 fixed 坐标：默认贴在「+」下方，下方放不下时向上翻，
 * 左右按视口夹住，避免面板伸出画布或被节点卡片盖住。
 */
export function placeEdgePopup(
  anchor: EdgePopupAnchor,
  popup: EdgePopupSize,
  viewport: EdgePopupViewport,
): { left: number; top: number } {
  const maxLeft = Math.max(VIEWPORT_MARGIN, viewport.width - popup.width - VIEWPORT_MARGIN);
  const left = Math.min(Math.max(anchor.left + anchor.width / 2 - popup.width / 2, VIEWPORT_MARGIN), maxLeft);

  const below = anchor.bottom + POPUP_GAP;
  const above = anchor.top - POPUP_GAP - popup.height;
  const fitsBelow = below + popup.height + VIEWPORT_MARGIN <= viewport.height;
  const fitsAbove = above >= VIEWPORT_MARGIN;

  let top = below;
  if (!fitsBelow && fitsAbove) top = above;
  const maxTop = Math.max(VIEWPORT_MARGIN, viewport.height - popup.height - VIEWPORT_MARGIN);
  top = Math.min(Math.max(top, VIEWPORT_MARGIN), maxTop);
  return { left, top };
}

export function WorkflowEdge({
  id,
  data,
  selected,
  markerEnd,
  sourceX,
  sourceY,
  targetX,
  targetY,
}: EdgeProps<CanvasFlowEdge>) {
  const { tr, locale } = useI18n();
  const zh = locale === 'zh-CN';
  const { resolved } = useThemeMode();
  const { x: viewportX, y: viewportY, zoom: viewportZoom } = useViewport();
  const [hovered, setHovered] = useState(false);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const anchorRef = useRef<HTMLButtonElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);
  const [popupPos, setPopupPos] = useState<{ left: number; top: number } | null>(null);

  // 锚点盒的外缘就是卡片边缘，React Flow 直接取外缘作为端点；
  // 箭头 marker 也由 React Flow 生成，这里不再做偏移。
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition: Position.Right,
    targetX,
    targetY,
    targetPosition: Position.Left,
    curvature: 0.16,
  });

  const active = hovered || open || selected === true;
  const stroke = active ? EDGE_ACTIVE_COLOR : EDGE_COLOR[resolved];
  const filtered = useMemo(() => filterKinds(data?.kinds ?? [], query, zh), [data, query, zh]);

  // 画布平移/缩放会改变连线中点，弹窗跟着锚点重新定位。
  useLayoutEffect(() => {
    if (!open) return;
    const update = () => {
      const anchor = anchorRef.current;
      const popup = popupRef.current;
      if (anchor === null || popup === null) return;
      const next = placeEdgePopup(
        anchor.getBoundingClientRect(),
        { width: popup.offsetWidth, height: popup.offsetHeight },
        { width: window.innerWidth, height: window.innerHeight },
      );
      setPopupPos((current) =>
        current !== null && current.left === next.left && current.top === next.top ? current : next,
      );
    };
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [open, query, labelX, labelY, viewportX, viewportY, viewportZoom]);

  // 面板挂到 body：React Flow 的节点层会盖住连线标签层，留在原地会被节点卡片遮住。
  const popup = data === undefined ? null : (
    <div
      ref={popupRef}
      className="nodrag nopan nowheel pointer-events-auto fixed z-50 w-[212px] overflow-hidden rounded-xl border border-border bg-card shadow-xl"
      style={{
        left: popupPos?.left ?? 0,
        top: popupPos?.top ?? 0,
        visibility: popupPos === null ? 'hidden' : 'visible',
      }}
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
  );

  return (
    <g onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        interactionWidth={24}
        style={{ stroke, strokeWidth: 2, strokeLinecap: 'round', transition: 'stroke 120ms' }}
      />

      {data !== undefined && data.route !== '' && (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan pointer-events-none absolute rounded-md border border-border/70 bg-card px-1.5 py-[1px] font-sans text-[10px] leading-4 text-text-muted shadow-sm"
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
              ref={anchorRef}
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

            {open && popup !== null && createPortal(popup, document.body)}
          </div>
        </EdgeLabelRenderer>
      )}
    </g>
  );
}
