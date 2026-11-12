// 左栏节点导航：整条流程按执行顺序列出来。
//
// 圆点颜色就是节点结果；已执行与未执行分成两段，中间有分隔说明。

import type { WorkflowNode, WorkflowNodeRun } from '@/api/workflows';
import { formatDuration } from '@/components/workflow/RunStatusPill';
import { NOT_RUN_META, RUN_STATUS_META } from '@/lib/runStatus';
import { cn } from '@/lib/cn';
import { useI18n } from '@/i18n/locale';

/** 导航项 = 已执行记录 + 还没执行的定义节点（后者只展示，点不开详情）。 */
export type RailItem =
  | { type: 'run'; run: WorkflowNodeRun; key: string }
  | { type: 'pending'; node: WorkflowNode; key: string };

export function buildRailItems(
  nodes: WorkflowNodeRun[],
  definition: WorkflowNode[],
): RailItem[] {
  const executed = nodes.map((node) => ({
    type: 'run' as const,
    run: node,
    key: `${node.node_id}#${node.attempt}`,
  }));
  const seen = new Set(executed.map((item) => item.run.node_id));
  const pending: RailItem[] = definition
    .filter((node) => !seen.has(node.id))
    .map((node) => ({ type: 'pending' as const, node, key: `todo-${node.id}` }));
  return [...executed, ...pending];
}

type Props = {
  items: RailItem[];
  /** node_id → 画布里的显示名。 */
  labelOf(nodeId: string): string;
  selectedId: string | null;
  /** 停在人工节点的 id：这个节点显示警示色而不是结果色。 */
  gateId: string | null;
  /** 运行中的节点 id。 */
  runningIds: Set<string>;
  onSelect(nodeId: string): void;
};

export function RunNodeRail({ items, labelOf, selectedId, gateId, runningIds, onSelect }: Props) {
  const { tr } = useI18n();
  const executed = items.filter((item) => item.type === 'run');
  const pending = items.filter((item) => item.type === 'pending');

  return (
    <nav
      className="flex min-h-0 min-w-0 flex-col border-b border-zinc-800/60 lg:border-b-0 lg:border-r"
      aria-label={tr('节点列表', 'Nodes')}
    >
      <div className="flex items-center justify-between px-3 py-2 text-[10px] uppercase tracking-wide text-text-faint">
        <span>{tr('执行流程', 'Execution')}</span>
        <span className="tabular-nums normal-case">
          {executed.length}
          {pending.length > 0 && ` / ${items.length}`}
        </span>
      </div>
      <ol className="min-h-0 flex-1 overflow-y-auto pb-2">
        {executed.map((item) => {
          if (item.type !== 'run') return null;
          const id = item.run.node_id;
          const isGate = gateId !== null && id === gateId;
          const isRunning = runningIds.has(id) || item.run.status === 'running';
          const dot = isGate
            ? RUN_STATUS_META.waiting.dot
            : isRunning
              ? RUN_STATUS_META.running.dot
              : item.run.status === 'failed'
                ? RUN_STATUS_META.failed.dot
                : RUN_STATUS_META.succeeded.dot;
          return (
            <li key={item.key}>
              <RailButton
                selected={id === selectedId}
                disabled={false}
                onClick={() => onSelect(id)}
                dot={dot}
                pulse={isGate || isRunning}
                label={labelOf(id)}
                trailing={
                  isGate
                    ? tr('待审批', 'Waiting')
                    : item.run.status === 'running'
                      ? null
                      : formatDuration(item.run.duration_ms)
                }
              />
            </li>
          );
        })}

        {pending.length > 0 && (
          <>
            <li className="px-3 pb-1 pt-2.5 text-[9px] uppercase tracking-wide text-text-faint">
              {tr(`未执行 · ${pending.length}`, `Not run · ${pending.length}`)}
            </li>
            {pending.map((item) => {
              if (item.type !== 'pending') return null;
              return (
                <li key={item.key}>
                  <RailButton
                    selected={item.node.id === selectedId}
                    disabled
                    onClick={() => undefined}
                    dot={NOT_RUN_META.dot}
                    pulse={false}
                    label={labelOf(item.node.id)}
                    trailing={null}
                  />
                </li>
              );
            })}
          </>
        )}

        {items.length === 0 && (
          <li className="px-3 py-6 text-center text-[11px] text-text-faint">
            {tr('还没有节点执行记录', 'No node has run yet')}
          </li>
        )}
      </ol>
    </nav>
  );
}

function RailButton({
  selected,
  disabled,
  onClick,
  dot,
  pulse,
  label,
  trailing,
}: {
  selected: boolean;
  disabled: boolean;
  onClick(): void;
  dot: string;
  pulse: boolean;
  label: string;
  trailing: string | null;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      aria-current={selected ? 'true' : undefined}
      className={cn(
        'group relative flex w-full items-center gap-1.5 py-1.5 pl-2.5 pr-2 text-left transition-colors',
        selected ? 'bg-accent/10' : 'hover:bg-zinc-500/[0.06]',
        disabled && 'cursor-default opacity-45',
      )}
    >
      {selected && <span className="absolute inset-y-0 left-0 w-[2px] bg-accent" aria-hidden />}
      <span
        className={cn('h-1.5 w-1.5 shrink-0 rounded-full', dot, pulse && 'animate-pulse-dot')}
        aria-hidden
      />
      <span className="min-w-0 flex-1 truncate text-[11.5px] text-text-muted">{label}</span>
      {trailing !== null && (
        <span className="shrink-0 font-mono text-[9.5px] tabular-nums text-text-faint">{trailing}</span>
      )}
    </button>
  );
}
