// 对话内的「并行排查 · N 路」折叠块：dispatch_investigation 工具卡的专属渲染。
// 折叠头带计数与状态（规范：折叠标题必须显示数量或状态）；展开看每路一句话结论，
// 行尾可跳右栏工作台定位。数据 = 工具卡静态参数/结果 + 排查快照补活。
import { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, MapPin } from 'lucide-react';
import {
  getInvestigation,
  subscribeDispatchEvents,
  type InvestigationDispatch,
  type InvestigationWorker,
  type WorkerStatus,
} from '@/api/investigation';
import { DISPATCH_STATUS_META, WORKER_STATUS_META } from '@/lib/investigationStatus';
import type { LocateTarget } from './InvestigationWorkbench';
import { errorText } from '@/lib/errors';
import { useI18n } from '@/i18n/locale';
import { cn } from '@/lib/cn';
import { RowActionIcon, StatusPill } from '@/components/ui';

/** dispatch_investigation 的工具返回形状（与后端 tool_result_wire 对齐）。 */
type DispatchToolResult = {
  dispatch_uid?: string;
  status?: string;
  partial?: boolean;
  message?: string;
  still_running?: { worker_uid: string; name: string }[];
  workers?: {
    worker_uid: string;
    name: string;
    status: WorkerStatus;
    conclusion: string;
    error: string;
    duration_ms: number;
  }[];
};

function fmtDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  return `${m}m${Math.round(s - m * 60)}s`;
}

export function DispatchBlock({
  call,
  pending,
  onLocate,
}: {
  call: {
    name: string;
    arguments?: unknown;
    result?: unknown;
    duration_ms?: number;
  };
  /** 回合未收口：折叠块只展示进行态，交互禁用 */
  pending?: boolean;
  onLocate?: (target: LocateTarget) => void;
}) {
  const { tr } = useI18n();
  const [open, setOpen] = useState(false);
  const [wire, setWire] = useState<InvestigationDispatch | null>(null);

  const args = (call.arguments ?? {}) as { question?: string; workers_json?: string };
  const result = (call.result ?? {}) as DispatchToolResult;
  const dispatchUid = result.dispatch_uid ?? wire?.dispatch_uid ?? null;

  // 快照补活：重试/后台续跑后数字与结论会变，工具卡静态结果是旧的
  const loadWire = useCallback(async () => {
    if (!dispatchUid) return;
    try {
      setWire(await getInvestigation(dispatchUid));
    } catch {
      // 快照拉不到不打扰对话：工具卡静态结果仍可展示
    }
  }, [dispatchUid]);

  useEffect(() => {
    void loadWire();
  }, [loadWire]);

  // 未终态时订阅事件流，完成即对齐快照
  useEffect(() => {
    if (!dispatchUid) return;
    const terminal = wire ? ['succeeded', 'failed', 'stopped'].includes(wire.status) : false;
    if (terminal || pending) return;
    const ctrl = new AbortController();
    void subscribeDispatchEvents(dispatchUid, (ev) => {
      if (ev.event === 'dispatch_updated' || ev.event === 'dispatch_finished') {
        void loadWire();
      }
    }, ctrl.signal).catch(() => {});
    return () => ctrl.abort();
  }, [dispatchUid, wire?.status, pending, loadWire]);

  // 路数：快照 > 工具结果 > 工具入参
  const workersFromArgs = (() => {
    try {
      const parsed = JSON.parse(args.workers_json ?? '[]') as { name?: string }[];
      return parsed.map((w, i) => ({ name: w.name ?? `#${i + 1}` }));
    } catch {
      return [];
    }
  })();
  const workers: {
    key: string;
    name: string;
    status: WorkerStatus | null;
    conclusion: string;
    duration_ms: number;
  }[] = (wire?.workers ?? result.workers ?? []).map((w: InvestigationWorker | NonNullable<DispatchToolResult['workers']>[number]) => ({
    key: w.worker_uid,
    name: w.name,
    status: w.status ?? null,
    conclusion: w.conclusion ?? '',
    duration_ms: w.duration_ms ?? 0,
  }));
  if (workers.length === 0 && workersFromArgs.length > 0) {
    for (const [i, w] of workersFromArgs.entries()) {
      workers.push({ key: `arg-${i}`, name: w.name, status: null, conclusion: '', duration_ms: 0 });
    }
  }

  const stats = wire?.stats;
  const counts = {
    total: stats?.total ?? workers.length,
    succeeded: stats?.succeeded ?? workers.filter((w) => w.status === 'succeeded').length,
    running:
      stats?.running ??
      // 无状态的占位行（工具未出结果）都算未完
      workers.filter((w) => w.status === null || w.status === 'running' || w.status === 'queued').length,
    failed: stats?.failed ?? workers.filter((w) => w.status === 'failed').length,
  };
  const usedTokens = typeof wire?.budget?.used_tokens_total === 'number' ? wire.budget.used_tokens_total : null;

  // 总状态：快照状态优先；工具未出结果时是进行中
  const statusKey = wire?.status ?? (pending || result.status === undefined ? 'running' : (result.status as keyof typeof DISPATCH_STATUS_META));
  const meta = DISPATCH_STATUS_META[statusKey] ?? DISPATCH_STATUS_META.running;

  return (
    <div
      className={cn(
        'w-full overflow-hidden rounded-lg border bg-zinc-900/40 text-xs',
        statusKey === 'failed' ? 'border-danger/30' : 'border-zinc-800/60',
      )}
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        aria-label={tr(`并行排查 ${counts.total} 路`, `Investigation ${counts.total} lanes`)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-zinc-300 hover:bg-zinc-800/40"
      >
        {open ? (
          <ChevronDown size={13} className="shrink-0 text-zinc-500" />
        ) : (
          <ChevronRight size={13} className="shrink-0 text-zinc-500" />
        )}
        <span className="font-medium text-zinc-200">
          {tr(`并行排查 · ${counts.total} 路`, `Investigation · ${counts.total} lanes`)}
        </span>
        <StatusPill tone={meta.tone} label={tr(meta.zh, meta.en)} pulse={meta.pulse} />
        <span className="ml-auto flex items-center gap-1.5 text-[10px] tabular-nums text-zinc-500">
          <span>
            {counts.succeeded} {tr('完成', 'done')}
          </span>
          <span>·</span>
          <span>
            {counts.running} {tr('运行中', 'running')}
          </span>
          {counts.failed > 0 && (
            <>
              <span>·</span>
              <span className="text-danger">
                {counts.failed} {tr('失败', 'failed')}
              </span>
            </>
          )}
          {usedTokens !== null && (
            <>
              <span>·</span>
              <span>{(usedTokens / 1000).toFixed(1)}k tok</span>
            </>
          )}
        </span>
      </button>
      {(result.message ?? null) !== null && (
        <div className="border-t border-zinc-800/60 bg-zinc-950/40 px-3 py-1.5 text-[10.5px] leading-4 text-zinc-400">
          {result.message}
        </div>
      )}
      {open && (
        <div className="border-t border-zinc-800/60 bg-zinc-950/40 px-2 py-1.5">
          {workers.map((w) => {
            const wMeta = w.status ? WORKER_STATUS_META[w.status] : null;
            return (
              <div key={w.key} className="flex items-center gap-2 rounded-md px-1.5 py-1 hover:bg-zinc-800/40">
                {wMeta ? (
                  <StatusPill tone={wMeta.tone} label={tr(wMeta.zh, wMeta.en)} pulse={wMeta.pulse} />
                ) : (
                  <span className="inline-flex h-[18px] items-center rounded-full border border-zinc-700 bg-zinc-800/40 px-1.5 text-[11px] leading-4 text-zinc-400">
                    {tr('排队中', 'Queued')}
                  </span>
                )}
                <span className="w-14 shrink-0 truncate text-[11px] font-medium text-zinc-200">{w.name}</span>
                <span
                  className={cn(
                    'min-w-0 flex-1 truncate text-[10.5px]',
                    w.status === 'failed' ? 'text-danger' : 'text-zinc-400',
                  )}
                >
                  {w.conclusion || (w.status === 'failed' ? tr('失败', 'failed') : '')}
                </span>
                {w.duration_ms > 0 && (
                  <span className="shrink-0 text-[9.5px] tabular-nums text-zinc-500">{fmtDuration(w.duration_ms)}</span>
                )}
                {onLocate && w.key.startsWith('arg-') === false && (
                  <RowActionIcon
                    icon={MapPin}
                    label={tr(`在工作台定位 ${w.name}`, `Locate ${w.name} in workbench`)}
                    onClick={() => onLocate({ kind: 'worker', workerUid: w.key })}
                  />
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
