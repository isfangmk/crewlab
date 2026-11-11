// 画布节点卡片：实色图标块 + 名称 + 一行摘要 + 输出变量。
//
// 版式对齐 Dify 的工作流节点：240px 定宽、15px 圆角、浅描边 + 很轻的投影，
// 连线锚点固定在头部上下方 20px 处（Dify 同样把左右锚点放在 top-4），
// 这样多条分支从同一侧出发时不会挤在卡片中间打架。
// 颜色只出现在图标块上：整张画布保持中性，节点类型靠图标和名称区分。

import { Handle, Position, type Node, type NodeProps } from '@xyflow/react';
import { AlertTriangle, Check, ClipboardCopy, Copy, Loader2, Play, Trash2, X } from 'lucide-react';
import type { NodeRunStatus, WorkflowNode } from '@/api/workflows';
import { cn } from '@/lib/cn';
import { iconOf, toneOf } from '@/lib/workflowMeta';
import { useI18n } from '@/i18n/locale';

export type CanvasNodeData = {
  node: WorkflowNode;
  title: string;
  summary: string;
  issue: string | null;
  /** 运行快照里复用它显示节点执行结果。 */
  runStatus?: NodeRunStatus | 'pending';
  /** 节点上方悬浮工具条：试跑 / 复制 / 删除，交给编辑器处理定义变更。 */
  onRun?: () => void;
  running?: boolean;
  onDuplicate?: () => void;
  onDelete?: () => void;
};

export type CanvasFlowNode = Node<CanvasNodeData, 'workflow'>;

export function WorkflowNodeCard({ data, selected }: NodeProps<CanvasFlowNode>) {
  const { tr } = useI18n();
  const { node, title, summary, issue, runStatus, onRun, running, onDuplicate, onDelete } = data;
  const tone = toneOf(node.kind);
  const Icon = iconOf(node.kind);
  const heading = node.label.trim() === '' ? title : node.label;

  const statusBorder =
    runStatus === 'succeeded'
      ? 'border-ok/60'
      : runStatus === 'failed'
        ? 'border-danger/60'
        : selected
          ? 'border-indigo-500'
          : 'border-border/70';

  return (
    <div className="group relative w-[240px]">
      {/* 悬浮操作条：与 Dify 一样贴在卡片右上角上方，选中时常驻。 */}
      {(onRun !== undefined || onDuplicate !== undefined || onDelete !== undefined) && (
        <div
          className={cn(
            'nodrag nopan nowheel absolute -top-8 right-0 flex h-7 items-center gap-0.5 rounded-lg border border-border/70 bg-card/95 px-0.5 text-text-faint shadow-md backdrop-blur transition-opacity',
            selected ? 'pointer-events-auto opacity-100' : 'pointer-events-none opacity-0 group-hover:pointer-events-auto group-hover:opacity-100',
          )}
          onClick={(event) => event.stopPropagation()}
        >
          {onRun !== undefined && (
            <button
              type="button"
              aria-label={tr('运行此步', 'Run this step')}
              title={tr('运行此步', 'Run this step')}
              disabled={running === true}
              onClick={onRun}
              className={cn(
                'flex h-6 w-6 items-center justify-center rounded-md hover:bg-indigo-500/15 hover:text-indigo-400',
                running === true && 'cursor-wait text-indigo-400',
              )}
            >
              {running === true ? <Loader2 size={12} className="animate-spin" aria-hidden /> : <Play size={12} aria-hidden />}
            </button>
          )}
          <button
            type="button"
            aria-label={tr('复制节点 ID', 'Copy node ID')}
            title={tr('复制节点 ID', 'Copy node ID')}
            onClick={() => void navigator.clipboard?.writeText(node.id)}
            className="flex h-6 w-6 items-center justify-center rounded-md hover:bg-zinc-500/10 hover:text-text"
          >
            <ClipboardCopy size={12} aria-hidden />
          </button>
          {onDuplicate !== undefined && (
            <button
              type="button"
              aria-label={tr('复制节点', 'Duplicate node')}
              title={tr('复制节点', 'Duplicate node')}
              onClick={onDuplicate}
              className="flex h-6 w-6 items-center justify-center rounded-md hover:bg-zinc-500/10 hover:text-text"
            >
              <Copy size={12} aria-hidden />
            </button>
          )}
          {onDelete !== undefined && node.kind !== 'start' && (
            <button
              type="button"
              aria-label={tr('删除节点', 'Delete node')}
              title={tr('删除节点', 'Delete node')}
              onClick={onDelete}
              className="flex h-6 w-6 items-center justify-center rounded-md hover:bg-danger/10 hover:text-danger"
            >
              <Trash2 size={12} aria-hidden />
            </button>
          )}
        </div>
      )}

      {/* 目标锚点在左、源锚点在右；位置与 Dify 一致（top 20px，外扩 7px）。 */}
      <Handle
        type="target"
        position={Position.Left}
        className="!left-[-7px] !top-[20px] !h-3 !w-3 !border-2 !border-card !bg-zinc-400 transition-all hover:!scale-125 hover:!bg-indigo-500"
      />
      <Handle
        type="source"
        position={Position.Right}
        className="!right-[-7px] !top-[20px] !h-3 !w-3 !border-2 !border-card !bg-zinc-400 transition-all hover:!scale-125 hover:!bg-indigo-500"
      />

      <div
        className={cn(
          'rounded-[15px] border bg-card shadow-[0_1px_2px_rgba(0,0,0,0.06)] transition-[box-shadow,border-color] duration-150',
          statusBorder,
          // 选中与运行态用投影而不是粗描边，避免缩放后边缘发糊。
          (selected || runStatus === 'succeeded' || runStatus === 'failed') && 'shadow-lg',
          !selected && 'hover:shadow-md',
        )}
      >
        <div className="flex items-center gap-2 px-3 pb-1.5 pt-2.5">
          <span className={cn('flex h-6 w-6 shrink-0 items-center justify-center rounded-[7px] shadow-sm', tone.iconBox)}>
            <Icon size={13} strokeWidth={2.2} className={tone.icon} aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-[12.5px] font-semibold leading-4 text-text">{heading}</div>
            {/* 第二行只放「类型 · ID」，保持卡片安静；真正有用的摘要放正文 */}
            <div className="mt-[1px] flex items-center gap-1 truncate text-[10px] leading-3 text-text-faint">
              <span className="truncate">{title}</span>
              <span className="opacity-60">·</span>
              <span className="truncate font-mono">{node.id}</span>
            </div>
          </div>
          {node.retry_count > 0 && (
            <span className="shrink-0 rounded bg-zinc-500/10 px-1 text-[9.5px] leading-4 text-text-faint" title="重试次数">
              ↻{node.retry_count}
            </span>
          )}
        </div>

        <div className="line-clamp-2 min-h-[18px] px-3 pb-2 text-[11px] leading-[15px] text-text-muted">{summary}</div>

        {(node.output_key !== '' || issue !== null || runStatus !== undefined) && (
          <div className="flex items-center gap-1 px-3 pb-2">
            {node.output_key !== '' && (
              <span className="inline-flex items-center rounded border border-border/70 bg-bg/60 px-1 py-[1px] font-mono text-[9.5px] leading-3 text-text-faint">
                → {node.output_key}
              </span>
            )}
            {issue !== null && (
              <span className="inline-flex items-center gap-0.5 rounded border border-warn/30 bg-warn/10 px-1 py-[1px] text-[9.5px] leading-3 text-warn">
                <AlertTriangle size={9} aria-hidden />
                {issue}
              </span>
            )}
            {runStatus !== undefined && (
              <span
                className={cn(
                  'ml-auto inline-flex items-center gap-0.5 rounded-full px-1.5 text-[9.5px] leading-4',
                  runStatus === 'succeeded' && 'bg-ok/10 text-ok',
                  runStatus === 'failed' && 'bg-danger/10 text-danger',
                  runStatus === 'pending' && 'bg-zinc-500/10 text-text-faint',
                )}
              >
                {runStatus === 'succeeded' ? <Check size={9} aria-hidden /> : null}
                {runStatus === 'failed' ? <X size={9} aria-hidden /> : null}
                {runStatus === 'succeeded' ? tr('完成', 'Done') : runStatus === 'failed' ? tr('失败', 'Failed') : tr('未执行', 'Skipped')}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
