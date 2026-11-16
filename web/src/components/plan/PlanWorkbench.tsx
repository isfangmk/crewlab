// 会话计划工作台：右栏停靠面板，实时展示执行计划的步骤时间线/结论/用量。
// 实时走计划事件流（暂停/重试/跳过后的续跑也进同一条流），生命周期事件静默
// 重拉快照对齐；定位请求变化即滚动到对应步骤并高亮。
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  ListChecks,
  Pause,
  Play,
  RefreshCw,
  SkipForward,
  Square,
  X,
} from 'lucide-react';
import {
  getPlan,
  listPlans,
  pausePlan,
  retryPlanStep,
  skipPlanStep,
  startPlan,
  stopPlan,
  subscribePlanEvents,
  type PlanExecution,
  type PlanStep,
} from '@/api/plan';
import { PLAN_STATUS_META, STEP_STATUS_META } from '@/lib/planStatus';
import { errorText } from '@/lib/errors';
import { fullDateTime } from '@/lib/format';
import { useI18n } from '@/i18n/locale';
import { cn } from '@/lib/cn';
import {
  Button,
  CodeBlock,
  DetailStat,
  DetailStatGrid,
  EmptyState,
  flash,
  RowAction,
  Select,
  StatusPill,
} from '@/components/ui';

/** 耗时展示：与 MessageBubble 同口径（时/分/秒，秒保留一位）。 */
function fmtDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  return `${m}m${Math.round(s - m * 60)}s`;
}

export type PlanLocateTarget = { kind: 'step'; planUid: string; stepUid: string };

export function PlanWorkbench({
  sessionId,
  locate,
  onRequestSummary,
  onClose,
}: {
  sessionId: string;
  /** 计划卡跳来的定位请求；seq 变化触发滚动高亮 */
  locate?: (PlanLocateTarget & { seq: number }) | null;
  onRequestSummary?(text: string): void;
  onClose(): void;
}) {
  const { tr } = useI18n();
  const [items, setItems] = useState<PlanExecution[] | null>(null);
  const [selectedUid, setSelectedUid] = useState<string | null>(null);
  const [wire, setWire] = useState<PlanExecution | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // 实时日志：事件流逐行追加；快照里的 log_text 只是兜底节选
  const [liveLogs, setLiveLogs] = useState<Record<string, { line: string; tool: string }[]>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [busyKey, setBusyKey] = useState<string | null>(null);
  // 待定位目标：切换计划/快照未到时先挂起，锚点就绪再滚动
  const [pendingLocate, setPendingLocate] = useState<PlanLocateTarget | null>(null);
  // 静默刷新序号：只认最后一次请求，避免慢响应回填旧数据
  const loadSeq = useRef(0);

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

  const loadWire = useCallback(async (uid: string, silent: boolean) => {
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
  }, []);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    if (selectedUid) void loadWire(selectedUid, false);
  }, [selectedUid, loadWire]);

  // 事件订阅：日志本地追加，生命周期事件静默对齐快照
  useEffect(() => {
    if (!selectedUid) return;
    const ctrl = new AbortController();
    void subscribePlanEvents(
      selectedUid,
      (ev) => {
        if (ev.event === 'step_log') {
          setLiveLogs((prev) => ({
            ...prev,
            [ev.step_uid]: [...(prev[ev.step_uid] ?? []), { line: ev.line, tool: ev.tool }].slice(-200),
          }));
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

  const doStart = async () => {
    if (!selectedUid) return;
    setBusyKey('start');
    try {
      await startPlan(selectedUid);
      flash('ok', tr('计划已开始执行', 'Plan started'));
      await loadWire(selectedUid, true);
    } catch (e) {
      flash('err', errorText(e));
    } finally {
      setBusyKey(null);
    }
  };

  const doPause = async () => {
    if (!selectedUid) return;
    setBusyKey('pause');
    try {
      await pausePlan(selectedUid);
      flash('ok', tr('已请求暂停', 'Pause requested'));
      await loadWire(selectedUid, true);
    } catch (e) {
      flash('err', errorText(e));
    } finally {
      setBusyKey(null);
    }
  };

  const doStop = async () => {
    if (!selectedUid) return;
    setBusyKey('stop');
    try {
      const r = await stopPlan(selectedUid);
      if (!r.stopped) flash('ok', tr('没有可停止的步骤', 'Nothing to stop'));
      else flash('ok', tr('已全部停止', 'Plan stopped'));
      await loadWire(selectedUid, true);
    } catch (e) {
      flash('err', errorText(e));
    } finally {
      setBusyKey(null);
    }
  };

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

  const askSummary = () => {
    onRequestSummary?.(
      tr(
        '请根据执行计划各步的结论汇总结论（跳过的步骤标注缺口）',
        'Summarize the plan results (flag gaps for skipped steps)',
      ),
    );
  };

  const stats = wire?.stats;
  const total = stats?.total ?? 0;
  const doneCount = (stats?.succeeded ?? 0) + (stats?.skipped ?? 0);
  const progressPct = total > 0 ? Math.round((doneCount / total) * 100) : 0;

  return (
    <aside
      className="flex h-full w-[26rem] shrink-0 flex-col overflow-hidden border-l border-zinc-800/60 bg-zinc-900/40"
      aria-label={tr('执行进度', 'Plan workbench')}
    >
      {/* 头部：计划标题 + 状态 + 总目标 */}
      <div className="border-b border-zinc-800/60 px-3 py-2.5">
        <div className="flex items-center gap-2">
          <ListChecks size={13} className="shrink-0 text-indigo-300" />
          <span className="text-[11px] font-medium tracking-wide text-zinc-300">
            {tr('执行进度', 'Plan progress')}
            {wire ? ` #${wire.id}` : ''}
          </span>
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
            <p className="mt-1.5 line-clamp-2 text-[12px] leading-5 text-zinc-200">{wire.title || wire.question}</p>
            <p className="mt-1 text-[10px] text-zinc-500">
              {wire.started_at ? tr(`开始于 ${fullDateTime(wire.started_at)}`, `Started ${fullDateTime(wire.started_at)}`) : wire.created_at ? fullDateTime(wire.created_at) : ''}
              {wire.finished_at
                ? ` → ${fullDateTime(wire.finished_at)}`
                : wire.duration_ms > 0
                  ? ` · ${fmtDuration(wire.duration_ms)}`
                  : ''}
            </p>
          </>
        )}
        {(items?.length ?? 0) > 1 && (
          <Select
            className="mt-2"
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
          {/* 统计与总进度 */}
          <div className="border-b border-zinc-800/60 px-3 py-2.5">
            <DetailStatGrid columns={3}>
              <DetailStat label={tr('步骤', 'Steps')} value={`${doneCount}/${total}`} />
              <DetailStat label={tr('工具调用', 'Tool calls')} value={stats.tool_calls} />
              <DetailStat label={tr('用量', 'Usage')} value={wire.used_tokens > 0 ? `${(wire.used_tokens / 1000).toFixed(1)}k` : '—'} />
            </DetailStatGrid>
            <div className="mt-2 flex items-center justify-between text-[10px] text-zinc-500">
              <span>{tr('总进度', 'Progress')}</span>
              <span className="tabular-nums">{progressPct}%</span>
            </div>
            <div className="mt-1 h-1 overflow-hidden rounded-full bg-zinc-800/60">
              <div className="h-full rounded-full bg-indigo-500" style={{ width: `${progressPct}%` }} />
            </div>
          </div>

          {/* 步骤时间线（唯一二级滚动区） */}
          <div className="flex-1 overflow-y-auto px-3 py-2.5">
            <div className="flex flex-col gap-2">
              {wire.steps.map((s) => {
                const meta = STEP_STATUS_META[s.status];
                const open = expanded[s.step_uid] === true;
                const logs = liveLogs[s.step_uid] ?? (s.log_text ? s.log_text.split('\n').map((line) => ({ line, tool: '' })) : []);
                const nowLine = s.status === 'running' && logs.length > 0 ? logs[logs.length - 1] : null;
                return (
                  <div
                    key={s.step_uid}
                    id={`plan-step-${s.step_uid}`}
                    className={cn(
                      'overflow-hidden rounded-lg border border-zinc-800/60 bg-zinc-950/60',
                      s.status === 'running' && 'border-info/30',
                      s.status === 'failed' && 'border-danger/30',
                    )}
                  >
                    <button
                      type="button"
                      aria-expanded={open}
                      onClick={() => setExpanded((prev) => ({ ...prev, [s.step_uid]: !open }))}
                      className="flex w-full items-center gap-2 px-2.5 py-2 text-left hover:bg-zinc-800/40"
                    >
                      {open ? (
                        <ChevronDown size={13} className="shrink-0 text-zinc-500" />
                      ) : (
                        <ChevronRight size={13} className="shrink-0 text-zinc-500" />
                      )}
                      {/* 时间线圆点：完成打勾 / 执行中钟 / 失败叉 / 未跑序号 */}
                      <span
                        className={cn(
                          'grid h-5 w-5 shrink-0 place-items-center rounded-full text-[9px] font-medium',
                          s.status === 'succeeded' && 'bg-ok/15 text-ok',
                          s.status === 'running' && 'bg-info/15 text-info',
                          s.status === 'failed' && 'bg-danger/15 text-danger',
                          s.status === 'skipped' && 'bg-warn/15 text-warn',
                          (s.status === 'pending' || s.status === 'queued' || s.status === 'stopped') &&
                            'bg-zinc-800/60 text-zinc-400',
                        )}
                      >
                        {s.status === 'succeeded' ? (
                          <Check size={10} />
                        ) : s.status === 'running' ? (
                          <Clock size={10} />
                        ) : s.status === 'failed' ? (
                          <X size={10} />
                        ) : (
                          s.order_index + 1
                        )}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-1.5">
                          <span className="truncate text-[12px] font-medium text-zinc-200">
                            {s.order_index + 1} · {s.name}
                          </span>
                          {s.attempt > 1 && (
                            <span className="rounded-full border border-zinc-700 px-1.5 text-[9.5px] text-zinc-400">
                              {tr(`第 ${s.attempt} 次`, `#${s.attempt}`)}
                            </span>
                          )}
                        </span>
                        {nowLine && (
                          <span className="mt-0.5 flex items-center gap-1 truncate text-[10px] text-zinc-400">
                            <span className="inline-block h-2 w-2 shrink-0 animate-spin rounded-full border border-info border-t-transparent" />
                            {nowLine.tool
                              ? tr(`正在调用 ${nowLine.tool} · ${nowLine.line}`, `${nowLine.tool} · ${nowLine.line}`)
                              : nowLine.line}
                          </span>
                        )}
                      </span>
                      <span className="flex shrink-0 flex-col items-end gap-1">
                        <StatusPill tone={meta.tone} label={tr(meta.zh, meta.en)} pulse={meta.pulse} />
                        <span className="text-[9.5px] tabular-nums text-zinc-500">
                          {s.duration_ms > 0
                            ? fmtDuration(s.duration_ms)
                            : s.status === 'pending' || s.status === 'queued'
                              ? tr('排队中', 'Queued')
                              : ''}
                        </span>
                      </span>
                    </button>
                    {open && (
                      <div className="flex flex-col gap-2 border-t border-zinc-800/60 px-2.5 py-2">
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
                            {logs.slice(-20).map((l) => l.line).join('\n')}
                          </CodeBlock>
                        )}
                        {s.error && (
                          <div className="flex items-start gap-1.5 rounded-md border border-danger/25 bg-danger/5 px-2 py-1.5 text-[10px] leading-4 text-danger">
                            <AlertTriangle size={11} className="mt-0.5 shrink-0" />
                            <span className="min-w-0 break-words">{s.error}</span>
                          </div>
                        )}
                        {/* 失败/停止的步骤：重试或跳过，失败只影响本步 */}
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
                            <RowAction
                              icon={Square}
                              onClick={() => void doStop()}
                              disabled={busyKey !== null}
                            >
                              {tr('从这里停止', 'Stop here')}
                            </RowAction>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          {/* 底部动作：按计划状态切换 */}
          <div className="flex gap-2 border-t border-zinc-800/60 px-3 py-2.5">
            {wire.status === 'draft' && (
              <Button
                variant="primary"
                className="flex-1"
                disabled={busyKey !== null}
                onClick={() => void doStart()}
              >
                {busyKey === 'start' ? tr('执行中…', 'Starting…') : tr('开始执行', 'Start plan')}
              </Button>
            )}
            {wire.status === 'running' && (
              <>
                <Button
                  variant="ghost"
                  className="flex-1"
                  disabled={busyKey !== null}
                  onClick={() => void doPause()}
                >
                  {busyKey === 'pause' ? (
                    tr('暂停中…', 'Pausing…')
                  ) : (
                    <span className="flex items-center justify-center gap-1">
                      <Pause size={11} />
                      {tr('暂停', 'Pause')}
                    </span>
                  )}
                </Button>
                <Button
                  variant="dangerGhost"
                  className="flex-1"
                  disabled={busyKey !== null}
                  onClick={() => void doStop()}
                >
                  {busyKey === 'stop' ? tr('停止中…', 'Stopping…') : tr('全部停止', 'Stop all')}
                </Button>
              </>
            )}
            {wire.status === 'paused' && (
              <>
                <Button
                  variant="primary"
                  className="flex-1"
                  disabled={busyKey !== null}
                  onClick={() => void doStart()}
                >
                  {busyKey === 'start' ? (
                    tr('恢复中…', 'Resuming…')
                  ) : (
                    <span className="flex items-center justify-center gap-1">
                      <Play size={11} />
                      {tr('继续执行', 'Resume')}
                    </span>
                  )}
                </Button>
                <Button
                  variant="dangerGhost"
                  className="flex-1"
                  disabled={busyKey !== null}
                  onClick={() => void doStop()}
                >
                  {busyKey === 'stop' ? tr('停止中…', 'Stopping…') : tr('全部停止', 'Stop all')}
                </Button>
              </>
            )}
            {(wire.status === 'succeeded' || wire.status === 'stopped') && (
              <Button
                variant="primary"
                className="flex-1"
                disabled={doneCount === 0 || onRequestSummary === undefined}
                onClick={askSummary}
              >
                {tr('汇总结论回对话', 'Summarize in chat')}
              </Button>
            )}
          </div>
        </>
      )}
    </aside>
  );
}
