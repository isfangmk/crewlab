// 运行详情的顶部信息带：四张摘要卡，只回答「现在什么情况」。
//
// 状态、耗时、节点进度、触发方式各占一张卡，明细点开再看，
// 避免同一条信息在页面别处重复出现。

import type { WaitingGate, WorkflowRunDetail } from '@/api/workflows';
import { StatusPill } from '@/components/ui';
import { formatDuration } from '@/components/workflow/RunStatusPill';
import { RUN_STATUS_META, triggerText } from '@/lib/runStatus';
import { cn } from '@/lib/cn';
import { useI18n } from '@/i18n/locale';

/** 只给时:分:秒，完整日期放在 title 里。 */
function clockText(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleTimeString('zh-CN', { hour12: false });
}

type Props = {
  run: WorkflowRunDetail;
  runningCount: number;
  gate: WaitingGate | null;
  /** 节点在画布里的名字，用于说明「卡在哪一步」。 */
  gateLabel: string;
};

export function RunOverviewCards({ run, runningCount, gate, gateLabel }: Props) {
  const { tr } = useI18n();
  const total = run.definition.nodes.length;
  let ok = 0;
  let failed = 0;
  for (const node of run.nodes) {
    if (node.status === 'succeeded') ok += 1;
    else if (node.status === 'failed') failed += 1;
  }
  const waitingCount = gate === null ? 0 : 1;
  const running = Math.max(runningCount, 0);
  const done = ok + failed + waitingCount + running;
  const rest = Math.max(total - done, 0);
  const pct = (count: number) => (total === 0 ? 0 : (count / total) * 100);
  const meta = RUN_STATUS_META[run.status];
  const stateText =
    gate === null
      ? tr(meta.zh, meta.en)
      : gate.kind === 'input'
        ? tr('等待人工填写', 'Waiting for input')
        : tr('等待人工审批', 'Waiting for approval');

  return (
    <section
      className="grid grid-cols-2 gap-2.5 border-b border-zinc-800/60 px-4 py-3 lg:grid-cols-4"
      aria-label={tr('运行概况', 'Run overview')}
    >
      {/* 状态卡：唯一用强调色描边的卡，一眼先看到「现在卡在哪」 */}
      <div
        className={cn(
          'flex min-w-0 flex-col gap-1 rounded-xl border px-3 py-2.5',
          meta.tone === 'warn' && 'border-warn/35 bg-warn/[0.07]',
          meta.tone === 'danger' && 'border-danger/35 bg-danger/[0.06]',
          (meta.tone === 'ok' || meta.tone === 'info' || meta.tone === 'neutral') &&
            'border-zinc-800/60 bg-zinc-900/40',
        )}
      >
        <div className="flex items-center justify-between gap-2 text-[10px] text-text-faint">
          <span>{tr('当前状态', 'Status')}</span>
          <StatusPill tone={meta.tone} label={tr(meta.zh, meta.en)} pulse={meta.pulse} />
        </div>
        <div
          className={cn(
            'truncate text-[14px] font-semibold leading-5',
            meta.tone === 'warn' ? 'text-warn' : 'text-text',
          )}
        >
          {stateText}
        </div>
        <div className="truncate text-[10px] leading-4 text-text-faint">
          {gate !== null
            ? tr(`停在「${gateLabel}」，处理后继续`, `Paused at “${gateLabel}”`)
            : run.finished_at === null
              ? tr('尚未结束', 'Not finished yet')
              : tr(`结束于 ${clockText(run.finished_at)}`, `Finished ${clockText(run.finished_at)}`)}
        </div>
      </div>

      {/* 耗时卡 */}
      <div className="flex min-w-0 flex-col gap-1 rounded-xl border border-zinc-800/60 bg-zinc-900/40 px-3 py-2.5">
        <div className="text-[10px] text-text-faint">{tr('耗时', 'Duration')}</div>
        <div className="font-mono text-[17px] font-semibold leading-5 tabular-nums text-text">
          {formatDuration(run.duration_ms)}
        </div>
        <div className="truncate text-[10px] leading-4 text-text-faint">
          {clockText(run.created_at)} {tr('起算', 'start')}
        </div>
      </div>

      {/* 进度卡：比例条 + 计数 */}
      <div className="flex min-w-0 flex-col gap-1 rounded-xl border border-zinc-800/60 bg-zinc-900/40 px-3 py-2.5">
        <div className="flex items-center justify-between gap-2 text-[10px] text-text-faint">
          <span>{tr('节点进度', 'Progress')}</span>
          <span className="font-mono tabular-nums text-text-muted">
            {done} / {total}
          </span>
        </div>
        <div className="flex flex-wrap items-baseline gap-x-1.5 text-[10.5px] leading-4">
          <span className="text-ok">
            {tr('成功', 'ok')} <span className="font-mono tabular-nums">{ok}</span>
          </span>
          {running > 0 && (
            <span className="text-info">
              · {tr('运行中', 'running')} <span className="font-mono tabular-nums">{running}</span>
            </span>
          )}
          {waitingCount > 0 && (
            <span className="text-warn">
              · {tr('待人工', 'waiting')} <span className="font-mono tabular-nums">{waitingCount}</span>
            </span>
          )}
          <span className="text-text-faint">
            · {tr('未执行', 'pending')} <span className="font-mono tabular-nums">{rest}</span>
          </span>
        </div>
        <div className="mt-0.5 flex h-1.5 overflow-hidden rounded-full bg-zinc-800" aria-hidden>
          <span className="bg-ok" style={{ width: `${pct(ok)}%` }} />
          <span className="bg-info" style={{ width: `${pct(running)}%` }} />
          <span className="bg-warn" style={{ width: `${pct(waitingCount)}%` }} />
        </div>
      </div>

      {/* 触发卡 */}
      <div className="flex min-w-0 flex-col gap-1 rounded-xl border border-zinc-800/60 bg-zinc-900/40 px-3 py-2.5">
        <div className="text-[10px] text-text-faint">{tr('触发方式', 'Trigger')}</div>
        <div className="truncate text-[14px] font-semibold leading-5 text-text">
          {triggerText(run.trigger, tr)}
        </div>
        <div className="truncate text-[10px] leading-4 text-text-faint">{run.triggered_by || '—'}</div>
      </div>
    </section>
  );
}
