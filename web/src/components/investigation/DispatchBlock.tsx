// 对话内的「并行排查 · N 路」卡片：dispatch_investigation 工具卡的专属渲染
// （设计稿 parallel-serial-ui 对话内卡片形态）。头部 = 折叠箭头 + 模式图标 +
// 状态 pill + 全程计时；分段进度条每路一段；行列表 = 跑道缩影（编号徽 + 名称
// + 最近动作/结论 + 计时）；脚注 = 形态说明 + 最近动作心跳。数据 = 工具卡静
// 态结果 + 排查快照补活（重试/后台续跑后会变）。
import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, Waypoints } from 'lucide-react';
import {
  getInvestigation,
  subscribeDispatchEvents,
  type InvestigationDispatch,
  type InvestigationWorker,
  type WorkerStatus,
} from '@/api/investigation';
import { DISPATCH_STATUS_META, WORKER_STATUS_META } from '@/lib/investigationStatus';
import { fmtAgo, fmtDuration, isStale, useTicker } from '@/lib/runTimer';
import { requestChatRefetch } from '@/lib/chatLiveBus';
import { useI18n } from '@/i18n/locale';
import { cn } from '@/lib/cn';
import { StatusPill } from '@/components/ui';

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

export function DispatchBlock({
  call,
  pending,
}: {
  call: {
    name: string;
    arguments?: unknown;
    result?: unknown;
    duration_ms?: number;
  };
  /** 回合未收口：折叠块只展示进行态，交互禁用 */
  pending?: boolean;
}) {
  const { tr } = useI18n();
  const [open, setOpen] = useState(true);
  const [wire, setWire] = useState<InvestigationDispatch | null>(null);
  // 各路起点（事件到达时刻）与最近动作时刻：计时与心跳的锚点
  const startedAt = useRef<Record<string, number>>({});
  const [lastBeat, setLastBeat] = useState<Record<string, { at: number; tool: string }>>({});

  const args = (call.arguments ?? {}) as { question?: string; workers_json?: string };
  const result = (call.result ?? {}) as DispatchToolResult;
  const dispatchUid = result.dispatch_uid ?? wire?.dispatch_uid ?? null;

  const statusKey = (wire?.status ??
    (pending || result.status === undefined ? 'running' : result.status)) as keyof typeof DISPATCH_STATUS_META;
  const active = statusKey === 'running' || statusKey === 'partial';
  const tick = useTicker(active);

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

  // 未终态时订阅事件流：心跳就地记录，收口即对齐快照并刷新消息（自动汇总已落库）
  useEffect(() => {
    if (!dispatchUid) return;
    if (active === false && wire !== null) return;
    if (pending) return;
    const ctrl = new AbortController();
    void subscribeDispatchEvents(
      dispatchUid,
      (ev) => {
        if (ev.event === 'worker_started') {
          startedAt.current[ev.worker_uid] = Date.now();
          void loadWire();
          return;
        }
        if (ev.event === 'worker_log') {
          setLastBeat((prev) => ({
            ...prev,
            [ev.worker_uid]: { at: Date.now(), tool: ev.tool },
          }));
          return;
        }
        if (ev.event === 'worker_finished' || ev.event === 'dispatch_updated') {
          void loadWire();
          return;
        }
        if (ev.event === 'dispatch_finished') {
          void loadWire();
          // 自动汇总已随终态落库：拉进消息列表
          if (ev.summary_message_id) requestChatRefetch();
        }
      },
      ctrl.signal,
    ).catch(() => {});
    return () => ctrl.abort();
  }, [dispatchUid, active, wire !== null, pending, loadWire]);

  // 路数：快照 > 工具结果 > 工具入参
  const workersFromArgs = (() => {
    try {
      const parsed = JSON.parse(args.workers_json ?? '[]') as { name?: string; target?: string }[];
      return parsed.map((w, i) => ({ name: w.name ?? `#${i + 1}`, target: w.target ?? '' }));
    } catch {
      return [];
    }
  })();
  const workers: {
    key: string;
    name: string;
    target: string;
    status: WorkerStatus | null;
    conclusion: string;
    error: string;
    duration_ms: number;
    created_at: string | null;
    finished_at: string | null;
  }[] = (wire?.workers ?? result.workers ?? []).map(
    (w: InvestigationWorker | NonNullable<DispatchToolResult['workers']>[number]) => ({
      key: w.worker_uid,
      name: w.name,
      target: 'target' in w ? w.target : '',
      status: w.status ?? null,
      conclusion: w.conclusion ?? '',
      error: w.error ?? '',
      duration_ms: w.duration_ms ?? 0,
      created_at: 'created_at' in w ? w.created_at : null,
      finished_at: 'finished_at' in w ? w.finished_at : null,
    }),
  );
  if (workers.length === 0 && workersFromArgs.length > 0) {
    for (const [i, w] of workersFromArgs.entries()) {
      workers.push({
        key: `arg-${i}`,
        name: w.name,
        target: w.target,
        status: null,
        conclusion: '',
        error: '',
        duration_ms: 0,
        created_at: null,
        finished_at: null,
      });
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
  const findingsCountOf = (key: string) =>
    (wire?.findings ?? []).filter((f) => f.worker_uid === key && !f.retired).length;

  const meta = DISPATCH_STATUS_META[statusKey] ?? DISPATCH_STATUS_META.running;
  // 状态 pill：运行中显示「N 路运行中」，其余用状态文案
  const pillLabel = active
    ? tr(`${counts.running} 路运行中`, `${counts.running} running`)
    : tr(meta.zh, meta.en);
  // 全程计时：开始于工具入参时刻（本地首达），收口后定格为总用时
  const totalMs =
    wire?.finished_at && wire.created_at
      ? new Date(wire.finished_at).getTime() - new Date(wire.created_at).getTime()
      : wire?.duration_ms && wire.duration_ms > 0
        ? wire.duration_ms
        : active && wire?.created_at
          ? tick - new Date(wire.created_at).getTime()
          : 0;
  // 最近动作：全部路里最新一次心跳
  const beatTimes = Object.values(lastBeat).map((b) => b.at);
  const latestBeatAt = beatTimes.length > 0 ? Math.max(...beatTimes) : null;

  return (
    <div
      className={cn(
        'w-full overflow-hidden rounded-[13px] border bg-zinc-900/40',
        counts.failed > 0 ? 'border-danger/30' : 'border-zinc-800/60',
      )}
    >
      {/* c-head：折叠 + 模式图标 + 名称 + 状态 pill + 全程计时 */}
      <button
        type="button"
        aria-expanded={open}
        onClick={() => {
          setOpen((v) => !v);
          // 展开即重拉快照：重试/后台续跑后的数字与结论不靠订阅也能对齐
          void loadWire();
        }}
        aria-label={tr(`并行排查 ${counts.total} 路`, `Investigation ${counts.total} lanes`)}
        className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left hover:bg-zinc-800/30"
      >
        {open ? (
          <ChevronDown size={12} className="shrink-0 text-zinc-500" />
        ) : (
          <ChevronRight size={12} className="shrink-0 text-zinc-500" />
        )}
        <span className="grid h-6 w-6 shrink-0 place-items-center rounded-lg bg-sky-400/15 text-sky-300">
          <Waypoints size={12} />
        </span>
        <span className="text-[12px] font-semibold text-zinc-100">
          {tr(`并行排查 · ${counts.total} 路`, `Investigation · ${counts.total} lanes`)}
        </span>
        <StatusPill tone={meta.tone} label={pillLabel} pulse={meta.pulse} />
        <span className="ml-auto shrink-0 text-right">
          {totalMs > 0 && (
            <>
              <span
                className={cn(
                  'block text-[11px] font-semibold tabular-nums',
                  active ? 'text-sky-300' : 'text-zinc-300',
                )}
              >
                {fmtDuration(totalMs)}
              </span>
              <span className="block text-[8.5px] tracking-[0.1em] text-zinc-600">
                {tr(active ? '已运行' : '用时', active ? 'elapsed' : 'took')}
              </span>
            </>
          )}
        </span>
      </button>
      {open && (
        <>
          {/* c-strip 分段进度条：每路一段，状态即色，run 行进 */}
          <div className="flex gap-1.5 px-3.5 pb-2.5">
            {workers.map((w) => (
              <div
                key={w.key}
                className={cn(
                  'relative h-[5px] flex-1 overflow-hidden rounded-full',
                  w.status === 'succeeded' && 'bg-ok/20',
                  w.status === 'failed' && 'bg-danger/20',
                  w.status === 'running' && 'bg-sky-400/15',
                  (w.status === null || w.status === 'queued' || w.status === 'stopped') && 'bg-zinc-800',
                )}
              >
                {w.status === 'running' && (
                  <div className="absolute inset-y-0 left-0 w-[45%] animate-grow-strip rounded-full bg-sky-400" />
                )}
              </div>
            ))}
          </div>
          {/* c-body 行列表：跑道缩影 */}
          <div className="flex flex-col gap-1 border-t border-zinc-800/60 bg-zinc-950/40 px-3.5 py-2.5">
            {workers.map((w) => {
              const isRunning = w.status === 'running';
              const startMs = startedAt.current[w.key] ?? (w.created_at ? new Date(w.created_at).getTime() : null);
              const elapsedMs = isRunning && startMs ? tick - startMs : w.duration_ms;
              const beat = lastBeat[w.key] ?? null;
              const stale = isRunning && isStale(beat?.at ?? null, tick);
              const wMeta = w.status ? WORKER_STATUS_META[w.status] : null;
              const nFindings = findingsCountOf(w.key);
              return (
                <div key={w.key} className="flex items-center gap-2">
                  {/* 行首编号徽：✓ / ▶ / ✕ / 灰点 */}
                  <span
                    className={cn(
                      'grid h-[18px] w-[18px] shrink-0 place-items-center rounded-md text-[9px] font-bold',
                      w.status === 'succeeded' && 'bg-ok/15 text-ok',
                      isRunning && 'bg-sky-400 text-white',
                      w.status === 'failed' && 'bg-danger/15 text-danger',
                      (w.status === null || w.status === 'queued' || w.status === 'stopped') &&
                        'bg-zinc-800/60 text-zinc-500',
                    )}
                  >
                    {w.status === 'succeeded' ? '✓' : isRunning ? '▶' : w.status === 'failed' ? '✕' : '·'}
                  </span>
                  <span
                    className={cn(
                      'min-w-0 flex-1 truncate text-[11px]',
                      isRunning ? 'font-medium text-zinc-200' : 'text-zinc-400',
                    )}
                    title={wMeta ? `${w.name} · ${tr(wMeta.zh, wMeta.en)}` : w.name}
                  >
                    {isRunning
                      ? stale
                        ? tr(`${w.name} · 疑似停滞`, `${w.name} · possibly stalled`)
                        : beat
                          ? tr(
                              `${w.name} · 正在调用 ${beat.tool || '工具'}`,
                              `${w.name} · calling ${beat.tool || 'tool'}`,
                            )
                          : tr(`${w.name} · 等待工具响应…`, `${w.name} · waiting…`)
                      : w.status === 'succeeded'
                        ? tr(
                            `${w.name} · ${nFindings} 条发现`,
                            `${w.name} · ${nFindings} findings`,
                          )
                        : w.status === 'failed' && w.error
                          ? `${w.name} · ${w.error}`
                          : `${w.name}${w.target ? ` · ${w.target}` : ''}`}
                  </span>
                  <span
                    className={cn(
                      'shrink-0 text-[10px] tabular-nums',
                      isRunning ? 'font-semibold text-sky-300' : 'text-zinc-500',
                    )}
                  >
                    {isRunning || w.duration_ms > 0 ? fmtDuration(elapsedMs) : ''}
                  </span>
                </div>
              );
            })}
          </div>
          {/* c-foot：形态说明 + 最近动作心跳 */}
          <div className="flex items-center gap-2 border-t border-zinc-800/60 px-3.5 py-2 text-[10px] text-zinc-500">
            <span>
              {tr(
                `跑道式：${counts.total} 路同时推进，各有时钟`,
                `Lanes: ${counts.total} in parallel, each with its own clock`,
              )}
            </span>
            {active && (
              <span className="ml-auto tabular-nums">
                {latestBeatAt !== null
                  ? tr(`最近动作 ${fmtAgo(latestBeatAt, tick)}`, `last activity ${fmtAgo(latestBeatAt, tick)}`)
                  : tr('等待首个动作…', 'waiting for first activity…')}
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
