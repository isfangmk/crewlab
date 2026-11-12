// 画布节点卡片：实色图标块 + 名称 + 一行摘要 + 输出变量。
//
// 版式：240px 定宽、15px 圆角、浅描边 + 很轻的投影，
// 连线锚点固定在头部上下方 20px 处，这样多条分支从同一侧出发时
// 不会挤在卡片中间打架。颜色只出现在图标块上：整张画布保持中性，
// 节点类型靠图标和名称区分。

import { Handle, Position, type Node, type NodeProps } from '@xyflow/react';
import { AlertTriangle, ClipboardCopy, Clock, Copy, Loader2, Play, Trash2 } from 'lucide-react';
import type { WorkflowNode } from '@/api/workflows';
import { cn } from '@/lib/cn';
import { iconOf, toneOf } from '@/lib/workflowMeta';
import { useI18n } from '@/i18n/locale';

/** 画布上节点的一种执行状态：来自实时事件或进入页面时恢复的上次运行。 */
export type NodeExecutionStatus = 'running' | 'succeeded' | 'failed' | 'waiting';

export type CanvasNodeData = {
  node: WorkflowNode;
  title: string;
  summary: string;
  issue: string | null;
  /** 执行状态：running 正在跑，waiting 停在人工节点等审批/填写。 */
  status?: NodeExecutionStatus;
  /** waiting 时区分「待审批」与「待填写」，决定文案与配色。 */
  waitingKind?: 'approval' | 'input';
  /** 节点上方悬浮工具条：试跑 / 复制 / 删除，交给编辑器处理定义变更。 */
  onRun?: () => void;
  running?: boolean;
  onDuplicate?: () => void;
  onDelete?: () => void;
};

export type CanvasFlowNode = Node<CanvasNodeData, 'workflow'>;

// 状态只用于三处：卡片描边、标题行的圆点 + 文案、人工节点的底部提示条。
// 用语义令牌而不是固定色值，浅色/深色主题下都能站得住。
const STATUS_STYLE: Record<
  NodeExecutionStatus,
  { border: string; ring: string; dot: string; text: string }
> = {
  running: { border: 'border-accent/50', ring: 'ring-2 ring-accent/10', dot: 'bg-accent', text: 'text-accent' },
  succeeded: { border: 'border-ok/45', ring: 'ring-1 ring-ok/10', dot: 'bg-ok', text: 'text-ok' },
  failed: { border: 'border-danger/50', ring: 'ring-1 ring-danger/10', dot: 'bg-danger', text: 'text-danger' },
  waiting: { border: 'border-warn/50', ring: 'ring-2 ring-warn/10', dot: 'bg-warn', text: 'text-warn' },
};

export function WorkflowNodeCard({ data, selected }: NodeProps<CanvasFlowNode>) {
  const { tr } = useI18n();
  const { node, title, summary, issue, status, waitingKind, onRun, running, onDuplicate, onDelete } = data;
  const tone = toneOf(node.kind);
  const Icon = iconOf(node.kind);
  const heading = node.label.trim() === '' ? title : node.label;
  const style = status === undefined ? null : STATUS_STYLE[status];
  const waiting = waitingKind === 'input' ? tr('待填写', 'Waiting input') : tr('待审批', 'Waiting approval');
  const statusLabel =
    status === 'running'
      ? tr('运行中', 'Running')
      : status === 'succeeded'
        ? tr('成功', 'Succeeded')
        : status === 'failed'
          ? tr('失败', 'Failed')
          : status === 'waiting'
            ? waiting
            : '';

  return (
    <div className="group relative w-[240px]">
      {/* 悬浮操作条：贴在卡片右上角上方，选中时常驻。 */}
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

      {/* 目标锚点在左、源锚点在右（top 20px，外扩 7px）。 */}
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
          'overflow-hidden rounded-[15px] border bg-card shadow-[0_1px_2px_rgba(0,0,0,0.06)] transition-[box-shadow,border-color] duration-150',
          style === null
            ? selected
              ? 'border-accent/60'
              : 'border-border/70'
            : style.border,
          style !== null && style.ring,
          // 只有「还在动」或「需要人处理」的节点加重投影；成功节点保持安静，
          // 否则整张画布 18 个节点全是重投影，反而看不出重点。
          (selected || status === 'running' || status === 'waiting' || status === 'failed') && 'shadow-lg',
          selected || style !== null ? '' : 'hover:shadow-md',
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
          {/* 状态：一个小圆点 + 文案，跟随主题令牌；运行中/等待中的点会呼吸 */}
          {status !== undefined && style !== null && (
            <span
              className={cn('ml-auto inline-flex shrink-0 items-center gap-1 text-[10px] font-medium leading-4', style.text)}
              title={statusLabel}
            >
              <span
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  style.dot,
                  (status === 'running' || status === 'waiting') && 'animate-pulse-dot',
                )}
                aria-hidden
              />
              {statusLabel}
            </span>
          )}
        </div>

        <div className="line-clamp-2 min-h-[18px] px-3 pb-2 text-[11px] leading-[15px] text-text-muted">{summary}</div>

        {(node.output_key !== '' || issue !== null) && (
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
          </div>
        )}

        {/* 人工节点：整条卡片底部加一条提示，明确「这一轮停在这里等人」，区别于跑成功 */}
        {status === 'waiting' && (
          <div
            className={cn(
              'flex items-center gap-1.5 border-t px-3 py-1.5 text-[10px] font-medium',
              waitingKind === 'input'
                ? 'border-info/25 bg-info/[0.07] text-info'
                : 'border-warn/25 bg-warn/[0.07] text-warn',
            )}
          >
            <Clock size={11} aria-hidden />
            {waitingKind === 'input'
              ? tr('等待人工填写，运行已暂停', 'Waiting for input; run paused')
              : tr('等待人工审批，运行已暂停', 'Waiting for approval; run paused')}
          </div>
        )}
      </div>
    </div>
  );
}

