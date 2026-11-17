// 会话计划工作台：右栏停靠面板，流水线式展示执行计划（设计稿 parallel-serial-ui）。
// 面板头 kicker + 标题 + 「开始于 · 全程已运行 · 正在第 k/N 步」+ 单链 motif；
// 统计三格（进度 / 工具调用 / 用量）；步骤竖排编号链：连线表先后、当前步呼吸
// 高亮、完成步结论摘要框、排队步不显示时钟。执行全程零操作按钮，失败/跳过
// 步骤行内只留「重试此步 / 跳过并继续」。
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  ListChecks,
  RefreshCw,
  SkipForward,
  X,
} from 'lucide-react';
import {
  getPlan,
  listPlans,
  retryPlanStep,
  skipPlanStep,
  subscribePlanEvents,
  type PlanExecution,
  type PlanStep,
  type StepStatus,
} from '@/api/plan';
import { PLAN_STATUS_META, STEP_STATUS_META } from '@/lib/planStatus';
import { fmtAgo, fmtDuration, isStale, useTicker } from '@/lib/runTimer';
import {
  CHAT_LIVE_EVENT,
  relaySummaryDelta,
  requestChatRefetch,
  type ChatLiveDetail,
} from '@/lib/chatLiveBus';
import { errorText } from '@/lib/errors';
import { fullDateTime } from '@/lib/format';
import { useI18n } from '@/i18n/locale';
import { cn } from '@/lib/cn';
import {
  CodeBlock,
  EmptyState,
  flash,
  RowAction,
  Select,
  StatusPill,
} from '@/components/ui';

export type PlanLocateTarget = { kind: 'step'; planUid: string; stepUid: string };

export function PlanWorkbench({
  sessionId,
  locate,
  onClose,
}: {
  sessionId: string;
  /** 计划卡跳来的定位请求；seq 变化触发滚动高亮 */
  locate?: (PlanLocateTarget & { seq: number }) | null;
  onClose(): void;
}) {
  const { tr } = useI18n();
  const [items, setItems] = useState<PlanExecution[] | null>(null);
  const [selectedUid, setSelectedUid] = useState<string | null>(null);
  const [wire, setWire] = useState<PlanExecution | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // 实时日志：事件流逐行追加；快照里的 log_text 只是兜底节选
  const [liveLogs, setLiveLogs] = useState<Record<string, string[]>>({});
  // 当前步最近动作时刻：心跳与停滞判断
  const [beat, setBeat] = useState<{ at: number; tool: string } | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [busyKey, setBusyKey] = useState<string | null>(null);
  // 待定位目标：切换计划/快照未到时先挂起，锚点就绪再滚动
  const [pendingLocate, setPendingLocate] = useState<PlanLocateTarget | null>(null);
  // 静默刷新序号：只认最后一次请求，避免慢响应回填旧数据
  const loadSeq = useRef(0);

  const statusKey = (wire?.status ?? 'running') as keyof typeof PLAN_STATUS_META;
  const active = statusKey === 'running';
  const tick = useTicker(active);

  const loadList = useCallback(async () => {
    try {
      const r = await listPlans({ session_id: sessionId, limit: 20 });
      setItems(r.items);
      setSelectedUid((cur) => cur ?? r.items[0]?.plan_uid ?? null);
      setErr(null);
    } catch (e) {
      setErr(errorText(e));
    }
  }, [sessionId]);

  const loadWire = useCallback(
    async (uid: string, silent: boolean) => {
      const seq = (loadSeq.current += 1);
      if (!silent) setWire(null);
      try {
        const w = await getPlan(uid);
        if (seq !== loadSeq.current) return;
        setWire(w);
        setErr(null);
        // 默认展开：运行中/失败的步骤露出过程与错误
        setExpanded((prev) => {
          const next = { ...prev };
          for (const step of w.steps) {
            if (next[step.step_uid] === undefined) {
              next[step.step_uid] = step.status === 'running' || step.status === 'failed';
            }
          }
          return next;
        });
      } catch (e) {
        if (seq !== loadSeq.current) return;
        if (!silent) setErr(errorText(e));
      }
    },
    [],
  );

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    if (selectedUid) void loadWire(selectedUid, false);
  }, [selectedUid, loadWire]);

  // 新建计划落库：重拉列表并切到新计划（否则工作台停在刚打开时选中的旧计划）
  useEffect(() => {
    const onLive = (ev: Event) => {
      const detail = (ev as CustomEvent<ChatLiveDetail>).detail;
      if (detail?.type !== 'plan_created') return;
      setSelectedUid(detail.planUid);
      void loadList();
    };
    window.addEventListener(CHAT_LIVE_EVENT, onLive);
    return () => window.removeEventListener(CHAT_LIVE_EVENT, onLive);
  }, [loadList]);

  // 事件订阅：日志/心跳就地记录，生命周期事件静默对齐快照
  useEffect(() => {
    if (!selectedUid) return;
    const ctrl = new AbortController();
    void subscribePlanEvents(
      selectedUid,
      (ev) => {
        if (ev.event === 'summary_delta') {
          relaySummaryDelta(ev.message_id, ev.delta, ev.partial);
          return;
        }
        if (ev.event === 'step_log') {
          setLiveLogs((prev) => ({
            ...prev,
            [ev.step_uid]: [...(prev[ev.step_uid] ?? []), ev.line].slice(-200),
          }));
          setBeat({ at: Date.now(), tool: ev.tool });
          return;
        }
        if (ev.event === 'step_started') {
          setExpanded((prev) => ({ ...prev, [ev.step_uid]: true }));
          // 新步骤开始：时间线滚到它，一眼看到走到哪一步
          requestAnimationFrame(() => {
            requestAnimationFrame(() => {
              document.getElementById(`plan-step-${ev.step_uid}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
            });
          });
        }
        if (ev.event === 'plan_finished' && ev.summary_message_id) {
          // 自动汇总已随终态落库：拉进消息列表
          requestChatRefetch();
        }
        void loadWire(selectedUid, true);
      },
      ctrl.signal,
    ).catch((e) => {
      if ((e as Error).name !== 'AbortError') setErr(errorText(e));
    });
    return () => ctrl.abort();
  }, [selectedUid, loadWire]);

  // 定位请求：挂起待解析（切换计划/快照未到都等）
  useEffect(() => {
    if (!locate) return;
    setPendingLocate(locate);
    setExpanded((prev) => ({ ...prev, [locate.stepUid]: true }));
  }, [locate]);

  // 解析挂起的定位：先切到持有目标的计划，锚点就绪后滚动高亮
  useEffect(() => {
    if (!pendingLocate) return;
    const owner = (items ?? []).find((p) => p.steps.some((s) => s.step_uid === pendingLocate.stepUid));
    if (owner && owner.plan_uid !== selectedUid) {
      setSelectedUid(owner.plan_uid);
      return;
    }
    if (!wire) return;
    if (!wire.steps.some((s) => s.step_uid === pendingLocate.stepUid)) return;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        document.getElementById(`plan-step-${pendingLocate.stepUid}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
    });
    setPendingLocate(null);
  }, [pendingLocate, wire, items, selectedUid]);

  const doRetry = async (stepUid: string) => {
    if (!selectedUid) return;
    setBusyKey(stepUid);
    try {
      await retryPlanStep(selectedUid, stepUid);
      flash('ok', tr('已重试该步骤', 'Step retried'));
      await loadWire(selectedUid, true);
    } catch (e) {
      flash('err', errorText(e));
    } finally {
      setBusyKey(null);
    }
  };

  const doSkip = async (stepUid: string) => {
    if (!selectedUid) return;
    setBusyKey(stepUid);
    try {
      await skipPlanStep(selectedUid, stepUid);
      flash('ok', tr('已跳过该步骤', 'Step skipped'));
      await loadWire(selectedUid, true);
    } catch (e) {
      flash('err', errorText(e));
    } finally {
      setBusyKey(null);
    }
  };

  const stats = wire?.stats;
  const total = stats?.total ?? 0;
  const runningIndex = wire?.steps.findIndex((s) => s.status === 'running') ?? -1;
  const totalMs =
    wire && wire.finished_at && wire.started_at
      ? new Date(wire.finished_at).getTime() - new Date(wire.started_at).getTime()
      : wire && active && wire.started_at
        ? tick - new Date(wire.started_at).getTime()
        : 0;

  return (
    <aside
      className="flex h-full w-[26rem] shrink-0 flex-col overflow-hidden border-l border-zinc-800/60 bg-zinc-900/40"
      aria-label={tr('执行进度', 'Plan workbench')}
    >
      {/* 面板头：kicker + 标题 + 开始时刻/全程计时/当前步 + 单链 motif */}
      <div className="border-b border-zinc-800/60 px-4 pb-3.5 pt-3.5">
        <div className="flex items-center gap-2">
          <ListChecks size={13} className="shrink-0 text-accent" />
          <span className="text-[11px] font-bold tracking-[0.08em] text-accent">
            {tr('执行计划 · 流水线', 'Plan · pipeline')}
          </span>
          {wire && <span className="text-[10px] tabular-nums text-zinc-600">#{wire.id}</span>}
          {wire && (
            <StatusPill
              tone={PLAN_STATUS_META[wire.status].tone}
              label={tr(PLAN_STATUS_META[wire.status].zh, PLAN_STATUS_META[wire.status].en)}
              pulse={PLAN_STATUS_META[wire.status].pulse}
            />
          )}
          <button
            type="button"
            className="ml-auto grid h-6 w-6 place-items-center rounded-md text-zinc-500 hover:bg-zinc-800/60 hover:text-zinc-200"
            onClick={onClose}
            aria-label={tr('关闭执行进度', 'Close plan progress')}
          >
            <X size={13} />
          </button>
        </div>
        {wire && (
          <>
            <p className="mt-2 line-clamp-2 text-[14px] font-semibold leading-5 text-zinc-100">
              {wire.title || wire.question}
            </p>
            <p className="mt-1.5 text-[11px] text-zinc-500">
              {wire.started_at && (
                <span>{tr(`开始于 ${fullDateTime(wire.started_at)}`, `Started ${fullDateTime(wire.started_at)}`)}</span>
              )}
              {totalMs > 0 && (
                <span className={cn('font-semibold tabular-nums', active ? 'text-accent' : 'text-zinc-300')}>
                  · {tr('全程已运行 ', 'elapsed ')}
                  {fmtDuration(totalMs)}
                </span>
              )}
              {active && runningIndex >= 0 && (
                <span> · {tr(`正在第 ${runningIndex + 1}/${total} 步`, `step ${runningIndex + 1}/${total}`)}</span>
              )}
            </p>
            {/* motif 单链：节点+连线，走到第几步一目了然 */}
            <div className="mt-3 flex items-center">
              {wire.steps.map((s, i) => (
                <FragmentMotif key={s.step_uid} status={s.status} last={i === wire.steps.length - 1} />
              ))}
            </div>
          </>
        )}
        {(items?.length ?? 0) > 1 && (
          <Select
            className="mt-3"
            value={selectedUid ?? ''}
            onValueChange={(v) => setSelectedUid(v)}
            aria-label={tr('切换计划', 'Switch plan')}
            options={(items ?? []).map((p) => ({
              value: p.plan_uid,
              label: `#${p.id} · ${(p.title || p.question).slice(0, 24)}`,
            }))}
          />
        )}
      </div>

      {err && (
        <div className="border-b border-danger/20 bg-danger/5 px-3 py-2 text-[11px] text-danger">{err}</div>
      )}

      {/* 无计划任务：空态引导，不是加载中 */}
      {!err && items?.length === 0 && (
        <EmptyState
          icon={ListChecks}
          className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center"
          title={tr('本会话还没有执行计划', 'No plans in this session yet')}
          hint={tr(
            '复杂任务让助理先拆成执行计划，确认后逐步执行',
            'Ask the assistant to break a complex task into a plan, then run it step by step',
          )}
        />
      )}

      {!wire && !err && (items === null || items.length > 0) && (
        <div className="flex flex-1 items-center justify-center text-[11px] text-zinc-500">
          {tr('加载中…', 'Loading…')}
        </div>
      )}

      {wire && stats && (
        <>
          {/* 统计三格 */}
          <div className="flex border-b border-zinc-800/60">
            <StatCell
              label={tr('进度', 'Progress')}
              value={`${(stats.succeeded ?? 0) + (stats.skipped ?? 0)}/${total}`}
              unit={tr('步', 'steps')}
            />
            <StatCell label={tr('工具调用', 'Tool calls')} value={stats.tool_calls} />
            <StatCell
              label={tr('用量', 'Usage')}
              value={wire.used_tokens > 0 ? (wire.used_tokens / 1000).toFixed(1) : '—'}
              unit="tok"
            />
          </div>

          {/* 流水线时间线（唯一二级滚动区） */}
          <div className="flex-1 overflow-y-auto px-3 py-3">
            <div className="flex flex-col">
              {wire.steps.map((s, i) => {
                const meta = STEP_STATUS_META[s.status];
                const isRunning = s.status === 'running';
                const open = expanded[s.step_uid] === true;
                const logs = liveLogs[s.step_uid] ?? (s.log_text ? s.log_text.split('\n') : []);
                const startMs = s.started_at ? new Date(s.started_at).getTime() : null;
                const elapsedMs = isRunning && startMs ? tick - startMs : s.duration_ms;
                const stale = isRunning && isStale(beat?.at ?? null, tick);
                const stepTok = typeof s.usage?.total_tokens === 'number' ? s.usage.total_tokens : 0;
                return (
                  <div key={s.step_uid} className="flex items-start gap-3">
                    {/* 流水线轨道：编号节点 + 连线 */}
                    <div className="flex w-[24px] shrink-0 flex-col items-center self-stretch">
                      <span
                        className={cn(
                          'grid h-[24px] w-[24px] place-items-center rounded-full text-[10.5px] font-bold',
                          s.status === 'succeeded' && 'bg-ok text-white',
                          s.status === 'skipped' && 'bg-warn text-white',
                          isRunning && 'animate-pulse bg-accent text-accent-fg',
                          s.status === 'failed' && 'bg-danger text-white',
                          (s.status === 'pending' || s.status === 'queued' || s.status === 'stopped') &&
                            'bg-zinc-800 text-zinc-500 ring-1 ring-zinc-700',
                        )}
                      >
                        {s.status === 'succeeded' ? (
                          <Check size={12} />
                        ) : s.status === 'skipped' ? (
                          <SkipForward size={11} />
                        ) : s.status === 'failed' ? (
                          <X size={12} />
                        ) : (
                          s.order_index + 1
                        )}
                      </span>
                      {i < wire.steps.length - 1 && (
                        <span
                          className={cn(
                            'mt-1 w-[2px] flex-1 rounded-full',
                            s.status === 'succeeded' || s.status === 'skipped'
                              ? 'bg-ok'
                              : isRunning
                                ? 'bg-gradient-to-b from-accent to-zinc-700/60'
                                : 'bg-zinc-800',
                          )}
                        />
                      )}
                    </div>
                    {/* 步骤体 */}
                    <div
                      id={`plan-step-${s.step_uid}`}
                      className={cn('min-w-0 flex-1', i < wire.steps.length - 1 && 'pb-3')}
                    >
                      <button
                        type="button"
                        aria-expanded={open}
                        onClick={() => setExpanded((prev) => ({ ...prev, [s.step_uid]: !open }))}
                        className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-zinc-800/30"
                      >
                        <span className="min-w-0 flex-1">
                          <span className="flex items-center gap-1.5">
                            <span className="truncate text-[12.5px] font-semibold text-zinc-100">
                              {s.order_index + 1} · {s.name}
                            </span>
                            {s.attempt > 1 && (
                              <span className="shrink-0 rounded-full border border-zinc-700 px-1.5 text-[9.5px] text-zinc-400">
                                {tr(`第 ${s.attempt} 次`, `#${s.attempt}`)}
                              </span>
                            )}
                            {!isRunning && (
                              <StatusPill tone={meta.tone} label={tr(meta.zh, meta.en)} pulse={meta.pulse} />
                            )}
                          </span>
                          {/* 当前步心跳行 */}
                          {isRunning && (
                            <span
                              className={cn(
                                'mt-0.5 flex items-center gap-1.5 text-[10px] tabular-nums',
                                stale ? 'text-warn' : 'text-zinc-400',
                              )}
                            >
                              <span
                                className={cn(
                                  'inline-block h-[5px] w-[5px] rounded-full',
                                  stale ? 'bg-warn' : 'animate-pulse-dot bg-accent',
                                )}
                              />
                              {stale
                                ? tr('疑似停滞', 'possibly stalled')
                                : beat
                                  ? tr(
                                      `正在调用 ${beat.tool || '工具'} · ${fmtAgo(beat.at, tick)}`,
                                      `${beat.tool || 'tool'} · ${fmtAgo(beat.at, tick)}`,
                                    )
                                  : tr('等待工具响应…', 'waiting for tools…')}
                              {stepTok > 0 && (
                                <span className="text-zinc-500">
                                  · {tr(`本步已用 ${(stepTok / 1000).toFixed(1)}k tok`, `${(stepTok / 1000).toFixed(1)}k tok`)}
                                </span>
                              )}
                            </span>
                          )}
                        </span>
                        <span className="shrink-0 text-right">
                          {elapsedMs > 0 ? (
                            <>
                              <span
                                className={cn(
                                  'block text-[11.5px] font-semibold tabular-nums',
                                  isRunning ? 'text-accent' : 'text-zinc-300',
                                )}
                              >
                                {fmtDuration(elapsedMs)}
                              </span>
                              <span className="block text-[9px] tracking-[0.1em] text-zinc-600">
                                {tr(isRunning ? '已运行' : '用时', isRunning ? 'elapsed' : 'took')}
                              </span>
                            </>
                          ) : (
                            <span className="text-[9.5px] text-zinc-600">
                              {(s.status === 'pending' || s.status === 'queued') && tr('排队中', 'Queued')}
                            </span>
                          )}
                        </span>
                        {open ? (
                          <ChevronDown size={12} className="shrink-0 text-zinc-600" />
                        ) : (
                          <ChevronRight size={12} className="shrink-0 text-zinc-600" />
                        )}
                      </button>
                      {/* 完成步结论摘要框 / 排队步提示 */}
                      {!open && !isRunning && s.summary && (
                        <div
                          className={cn(
                            'mx-2 mt-1 rounded-lg border bg-zinc-900/50 px-2.5 py-2 text-[10.5px] leading-[1.6]',
                            s.status === 'skipped'
                              ? 'border-warn/30 text-warn'
                              : s.status === 'failed'
                                ? 'border-danger/25 text-danger'
                                : 'border-zinc-800/60 text-zinc-400',
                          )}
                        >
                          {s.summary}
                        </div>
                      )}
                      {open && (
                        <div className="mx-2 mt-1 flex flex-col gap-2 rounded-lg border border-zinc-800/60 bg-zinc-950/60 px-2.5 py-2">
                          {s.summary && (
                            <p
                              className={cn(
                                'text-[11px] leading-5',
                                s.status === 'skipped' ? 'text-warn' : 'text-zinc-300',
                              )}
                            >
                              {s.summary}
                            </p>
                          )}
                          {logs.length > 0 && (
                            <CodeBlock maxHeightClassName="max-h-36">
                              {logs.slice(-20).join('\n')}
                            </CodeBlock>
                          )}
                          {s.error && (
                            <div className="flex items-start gap-1.5 rounded-md border border-danger/25 bg-danger/5 px-2 py-1.5 text-[10px] leading-4 text-danger">
                              <AlertTriangle size={11} className="mt-0.5 shrink-0" />
                              <span className="min-w-0 break-words">{s.error}</span>
                            </div>
                          )}
                          {/* 失败恢复：重试 / 跳过（执行全程无干预按钮） */}
                          {(s.status === 'failed' || s.status === 'stopped') && (
                            <div className="flex gap-1.5">
                              <RowAction
                                icon={RefreshCw}
                                onClick={() => void doRetry(s.step_uid)}
                                disabled={busyKey !== null}
                              >
                                {busyKey === s.step_uid ? tr('重试中…', 'Retrying…') : tr('重试此步', 'Retry step')}
                              </RowAction>
                              <RowAction
                                icon={SkipForward}
                                onClick={() => void doSkip(s.step_uid)}
                                disabled={busyKey !== null}
                              >
                                {tr('跳过并继续', 'Skip & continue')}
                              </RowAction>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </>
      )}
    </aside>
  );
}

/** motif 单链一节：done 实心绿 / run 呼吸紫 / fail 红 / 待跑灰，连线表先后。 */
function FragmentMotif({ status, last }: { status: StepStatus; last: boolean }) {
  return (
    <>
      <span
        className={cn(
          'z-10 h-[10px] w-[10px] shrink-0 rounded-full border-[1.5px]',
          status === 'succeeded' && 'border-ok bg-ok',
          status === 'skipped' && 'border-warn bg-warn',
          status === 'running' &&
            'animate-pulse border-accent bg-accent',
          status === 'failed' && 'border-danger bg-danger',
          (status === 'pending' || status === 'queued' || status === 'stopped') && 'border-zinc-700 bg-zinc-800',
        )}
      />
      {!last && (
        <span
          className={cn(
            'h-[2px] flex-1',
            status === 'succeeded' || status === 'skipped'
              ? 'bg-ok'
              : status === 'running'
                ? 'bg-gradient-to-r from-ok to-accent'
                : 'bg-zinc-700/60',
          )}
        />
      )}
    </>
  );
}

/** 统计三格单元：小标签 + 大数字（+单位）。 */
function StatCell({ label, value, unit }: { label: string; value: string | number; unit?: string }) {
  return (
    <div className="flex-1 border-r border-zinc-800/60 px-4 py-3 last:border-r-0">
      <div className="text-[10px] tracking-[0.15em] text-zinc-500">{label}</div>
      <div className="mt-0.5 text-[17px] font-semibold tabular-nums text-zinc-100">
        {value}
        {unit && <span className="ml-1 text-[10.5px] font-normal text-zinc-500">{unit}</span>}
      </div>
    </div>
  );
}
