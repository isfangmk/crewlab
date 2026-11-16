// 会话排查工作台：右栏停靠面板，跑道式展示并行排查（设计稿 parallel-serial-ui）。
// 面板头 kicker + 问题 + 「开始于 · 全程已运行 · N 路同时在跑」+ 多轨 motif；
// 统计三格（完成 / 工具调用 / 用量）；每路一条跑道行：状态徽、目标、最近动作
// 心跳、独立计时、底部进度带，行可展开看发现与日志。执行全程零操作按钮，
// 失败行只留「重试此路」。
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  RefreshCw,
  Search,
  X,
} from 'lucide-react';
import {
  getInvestigation,
  listInvestigations,
  retryWorker,
  subscribeDispatchEvents,
  type InvestigationDispatch,
  type InvestigationFinding,
  type WorkerStatus,
} from '@/api/investigation';
import { DISPATCH_STATUS_META, WORKER_STATUS_META } from '@/lib/investigationStatus';
import { fmtAgo, fmtDuration, isStale, useTicker } from '@/lib/runTimer';
import { requestChatRefetch } from '@/lib/chatLiveBus';
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

export type LocateTarget =
  | { kind: 'finding'; n: number }
  | { kind: 'worker'; workerUid: string };

export function InvestigationWorkbench({
  sessionId,
  locate,
  onClose,
}: {
  sessionId: string;
  /** 引用角标/折叠块跳来的定位请求；seq 变化触发滚动高亮 */
  locate?: (LocateTarget & { seq: number }) | null;
  onClose(): void;
}) {
  const { tr } = useI18n();
  const [items, setItems] = useState<InvestigationDispatch[] | null>(null);
  const [selectedUid, setSelectedUid] = useState<string | null>(null);
  const [wire, setWire] = useState<InvestigationDispatch | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // 实时日志：事件流逐行追加；快照里的 log_text 只是兜底节选
  const [liveLogs, setLiveLogs] = useState<Record<string, string[]>>({});
  // 各路最近动作时刻与工具名：心跳与停滞判断
  const [beats, setBeats] = useState<Record<string, { at: number; tool: string }>>({});
  const startedAt = useRef<Record<string, number>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [highlightN, setHighlightN] = useState<number | null>(null);
  // 待定位目标：切换排查/快照未到时先挂起，锚点就绪再滚动
  const [pendingLocate, setPendingLocate] = useState<LocateTarget | null>(null);
  // 静默刷新序号：只认最后一次请求，避免慢响应回填旧数据
  const loadSeq = useRef(0);

  const statusKey = (wire?.status ?? 'running') as keyof typeof DISPATCH_STATUS_META;
  const active = statusKey === 'running' || statusKey === 'partial';
  const tick = useTicker(active);

  const loadList = useCallback(async () => {
    try {
      const r = await listInvestigations({ session_id: sessionId, limit: 20 });
      setItems(r.items);
      setSelectedUid((cur) => cur ?? r.items[0]?.dispatch_uid ?? null);
      setErr(null);
    } catch (e) {
      setErr(errorText(e));
    }
  }, [sessionId]);

  const loadWire = useCallback(async (uid: string, silent: boolean) => {
    const seq = (loadSeq.current += 1);
    if (!silent) setWire(null);
    try {
      const w = await getInvestigation(uid);
      if (seq !== loadSeq.current) return;
      setWire(w);
      setErr(null);
      // 默认展开：运行中/失败的路露出过程与错误
      setExpanded((prev) => {
        const next = { ...prev };
        for (const worker of w.workers) {
          if (next[worker.worker_uid] === undefined) {
            next[worker.worker_uid] = worker.status === 'running' || worker.status === 'failed';
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

  // 事件订阅：心跳/日志就地记录，生命周期事件静默对齐快照
  useEffect(() => {
    if (!selectedUid) return;
    const ctrl = new AbortController();
    void subscribeDispatchEvents(
      selectedUid,
      (ev) => {
        if (ev.event === 'worker_log') {
          setLiveLogs((prev) => ({
            ...prev,
            [ev.worker_uid]: [...(prev[ev.worker_uid] ?? []), ev.line].slice(-200),
          }));
          setBeats((prev) => ({ ...prev, [ev.worker_uid]: { at: Date.now(), tool: ev.tool } }));
          return;
        }
        if (ev.event === 'worker_started') {
          startedAt.current[ev.worker_uid] = Date.now();
          setExpanded((prev) => ({ ...prev, [ev.worker_uid]: true }));
        }
        if (ev.event === 'dispatch_finished' && ev.summary_message_id) {
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

  // 定位请求：挂起待解析（切换排查/快照未到都等）
  useEffect(() => {
    if (!locate) return;
    setPendingLocate(locate);
    if (locate.kind === 'worker') {
      setExpanded((prev) => ({ ...prev, [locate.workerUid]: true }));
    }
  }, [locate]);

  // 解析挂起的定位：先切到持有目标的排查，锚点就绪后滚动高亮
  useEffect(() => {
    if (!pendingLocate) return;
    const owner = (items ?? []).find((d) =>
      pendingLocate.kind === 'finding'
        ? d.findings.some((f) => f.n === pendingLocate.n && !f.retired)
        : d.workers.some((w) => w.worker_uid === pendingLocate.workerUid),
    );
    if (owner && owner.dispatch_uid !== selectedUid) {
      setSelectedUid(owner.dispatch_uid);
      return;
    }
    if (!wire) return;
    if (pendingLocate.kind === 'finding') {
      const f = wire.findings.find((x) => x.n === pendingLocate.n && !x.retired);
      if (!f) return;
      // 发现的锚点在 worker 展开体内：先展开所属 worker 再滚动
      setExpanded((prev) => ({ ...prev, [f.worker_uid]: true }));
      setHighlightN(pendingLocate.n);
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          document.getElementById(`inv-finding-${pendingLocate.n}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
      });
    } else {
      if (!wire.workers.some((w) => w.worker_uid === pendingLocate.workerUid)) return;
      setExpanded((prev) => ({ ...prev, [pendingLocate.workerUid]: true }));
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          document.getElementById(`inv-worker-${pendingLocate.workerUid}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
      });
    }
    setPendingLocate(null);
  }, [pendingLocate, wire, items, selectedUid]);

  const doRetry = async (workerUid: string) => {
    if (!selectedUid) return;
    setBusyKey(workerUid);
    try {
      await retryWorker(selectedUid, workerUid);
      flash('ok', tr('已重试该路', 'Worker retried'));
      await loadWire(selectedUid, true);
    } catch (e) {
      flash('err', errorText(e));
    } finally {
      setBusyKey(null);
    }
  };

  const stats = wire?.stats;
  const budget = wire?.budget ?? {};
  const usedTokens = typeof budget.used_tokens_total === 'number' ? budget.used_tokens_total : null;
  const findingsOf = (workerUid: string): InvestigationFinding[] =>
    (wire?.findings ?? []).filter((f) => f.worker_uid === workerUid && !f.retired);
  const totalMs =
    wire && wire.finished_at && wire.created_at
      ? new Date(wire.finished_at).getTime() - new Date(wire.created_at).getTime()
      : wire && active && wire.created_at
        ? tick - new Date(wire.created_at).getTime()
        : 0;

  return (
    <aside
      className="flex h-full w-[26rem] shrink-0 flex-col overflow-hidden border-l border-zinc-800/60 bg-zinc-900/40"
      aria-label={tr('排查工作台', 'Investigation workbench')}
    >
      {/* 面板头：kicker + 问题 + 开始时刻/全程计时 + 多轨 motif */}
      <div className="border-b border-zinc-800/60 px-4 pb-3.5 pt-3.5">
        <div className="flex items-center gap-2">
          <Activity size={13} className="shrink-0 text-sky-400" />
          <span className="text-[11px] font-bold tracking-[0.2em] text-sky-400">
            {tr('并行排查 · 跑道', 'Investigation · lanes')}
          </span>
          {wire && <span className="text-[10px] tabular-nums text-zinc-600">#{wire.id}</span>}
          {wire && (
            <StatusPill
              tone={DISPATCH_STATUS_META[wire.status].tone}
              label={tr(DISPATCH_STATUS_META[wire.status].zh, DISPATCH_STATUS_META[wire.status].en)}
              pulse={DISPATCH_STATUS_META[wire.status].pulse}
            />
          )}
          <button
            type="button"
            className="ml-auto grid h-6 w-6 place-items-center rounded-md text-zinc-500 hover:bg-zinc-800/60 hover:text-zinc-200"
            onClick={onClose}
            aria-label={tr('关闭排查工作台', 'Close workbench')}
          >
            <X size={13} />
          </button>
        </div>
        {wire && (
          <>
            <p className="mt-2 line-clamp-2 text-[14px] font-semibold leading-5 text-zinc-100">{wire.question}</p>
            <p className="mt-1.5 text-[11px] text-zinc-500">
              {wire.created_at && (
                <span>{tr(`开始于 ${fullDateTime(wire.created_at)}`, `Started ${fullDateTime(wire.created_at)}`)}</span>
              )}
              {totalMs > 0 && (
                <span className={cn('font-semibold tabular-nums', active ? 'text-sky-300' : 'text-zinc-300')}>
                  · {tr('全程已运行 ', 'elapsed ')}
                  {fmtDuration(totalMs)}
                </span>
              )}
              {active && (stats?.running ?? 0) > 0 && (
                <span>
                  · {tr(`${stats?.running} 路同时在跑`, `${stats?.running} lanes running`)}
                </span>
              )}
            </p>
            {/* motif 徽记条：每路一条轨道，done/run/fail/wait 四态填充，run 扫光 */}
            <div className="mt-3 flex h-[22px] gap-1">
              {wire.workers.map((w) => (
                <MotifTrack key={w.worker_uid} status={w.status} />
              ))}
            </div>
          </>
        )}
        {(items?.length ?? 0) > 1 && (
          <Select
            className="mt-3"
            value={selectedUid ?? ''}
            onValueChange={(v) => setSelectedUid(v)}
            aria-label={tr('切换排查', 'Switch investigation')}
            options={(items ?? []).map((d) => ({
              value: d.dispatch_uid,
              label: `#${d.id} · ${d.question.slice(0, 24)}`,
            }))}
          />
        )}
      </div>

      {err && (
        <div className="border-b border-danger/20 bg-danger/5 px-3 py-2 text-[11px] text-danger">{err}</div>
      )}

      {/* 无排查任务：空态引导，不是加载中 */}
      {!err && items?.length === 0 && (
        <EmptyState
          icon={Search}
          className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center"
          title={tr('本会话还没有排查任务', 'No investigations in this session yet')}
          hint={tr(
            '在对话里描述要排查的问题，助理会派多路只读子代理并行取证',
            'Describe what to investigate in the chat; the assistant will fan out read-only workers',
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
              label={tr('完成', 'Done')}
              value={`${stats.succeeded}/${stats.total}`}
              unit={tr('路', 'lanes')}
            />
            <StatCell label={tr('工具调用', 'Tool calls')} value={stats.tool_calls} />
            <StatCell
              label={tr('用量', 'Usage')}
              value={usedTokens !== null ? (usedTokens / 1000).toFixed(1) : '—'}
              unit="tok"
            />
          </div>

          {/* 跑道行列表（唯一二级滚动区） */}
          <div className="flex-1 overflow-y-auto px-3 py-3">
            <div className="flex flex-col gap-2">
              {wire.workers.map((w) => {
                const meta = WORKER_STATUS_META[w.status];
                const isRunning = w.status === 'running';
                const open = expanded[w.worker_uid] === true;
                const findings = findingsOf(w.worker_uid);
                const evidenceCount = findings.reduce((acc, f) => acc + f.evidence.length, 0);
                const logs = liveLogs[w.worker_uid] ?? (w.log_text ? w.log_text.split('\n') : []);
                const startMs = startedAt.current[w.worker_uid] ?? (w.created_at ? new Date(w.created_at).getTime() : null);
                const elapsedMs = isRunning && startMs ? tick - startMs : w.duration_ms;
                const beat = beats[w.worker_uid] ?? null;
                const stale = isRunning && isStale(beat?.at ?? null, tick);
                return (
                  <div
                    key={w.worker_uid}
                    id={`inv-worker-${w.worker_uid}`}
                    className={cn(
                      'relative overflow-hidden rounded-[11px] border',
                      isRunning
                        ? 'border-sky-400/35 bg-sky-400/[0.06]'
                        : w.status === 'failed'
                          ? 'border-danger/30 bg-danger/[0.06]'
                          : 'border-zinc-800/60 bg-zinc-950/60',
                    )}
                  >
                    <button
                      type="button"
                      aria-expanded={open}
                      onClick={() => setExpanded((prev) => ({ ...prev, [w.worker_uid]: !open }))}
                      className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left hover:bg-zinc-800/30"
                    >
                      {/* 跑道状态徽：✓ / 时钟 / ✕ / 未跑灰点 */}
                      <span
                        className={cn(
                          'grid h-[26px] w-[26px] shrink-0 place-items-center rounded-lg text-[12px] font-semibold',
                          w.status === 'succeeded' && 'bg-ok/15 text-ok',
                          isRunning && 'bg-sky-400/15 text-sky-300',
                          w.status === 'failed' && 'bg-danger/15 text-danger',
                          (w.status === 'queued' || w.status === 'stopped') && 'bg-zinc-800/60 text-zinc-500',
                        )}
                      >
                        {w.status === 'succeeded' ? (
                          <Check size={13} />
                        ) : isRunning ? (
                          <Clock size={13} />
                        ) : w.status === 'failed' ? (
                          <X size={13} />
                        ) : (
                          '·'
                        )}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-1.5">
                          <span className="truncate text-[12.5px] font-semibold text-zinc-100">{w.name}</span>
                          <span
                            className={cn(
                              'shrink-0 rounded px-1.5 py-px text-[9px] font-semibold',
                              isRunning && 'bg-sky-400/15 text-sky-300',
                              w.status === 'succeeded' && 'bg-ok/15 text-ok',
                              w.status === 'failed' && 'bg-danger/15 text-danger',
                              (w.status === 'queued' || w.status === 'stopped') && 'bg-zinc-800/60 text-zinc-500',
                            )}
                          >
                            {tr(meta.zh, meta.en)}
                          </span>
                          {w.attempt > 1 && (
                            <span className="shrink-0 rounded-full border border-zinc-700 px-1.5 text-[9.5px] text-zinc-400">
                              {tr(`第 ${w.attempt} 次`, `#${w.attempt}`)}
                            </span>
                          )}
                        </span>
                        <span className="mt-0.5 block truncate text-[10.5px] text-zinc-500">{w.target}</span>
                        {/* 心跳行：最近动作 + 停滞提示 */}
                        {isRunning && (
                          <span
                            className={cn(
                              'mt-1 flex items-center gap-1.5 text-[10px] tabular-nums',
                              stale ? 'text-warn' : 'text-zinc-400',
                            )}
                          >
                            <span
                              className={cn(
                                'inline-block h-[5px] w-[5px] rounded-full',
                                stale ? 'bg-warn' : 'animate-pulse-dot bg-sky-400',
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
                          </span>
                        )}
                        {!isRunning && w.status === 'succeeded' && (
                          <span className="mt-1 block truncate text-[10px] text-zinc-500">
                            {tr(
                              `发现 ${findings.length} 条 · 证据 ${evidenceCount} 处`,
                              `${findings.length} findings · ${evidenceCount} evidence`,
                            )}
                          </span>
                        )}
                        {!isRunning && w.status === 'failed' && w.error && (
                          <span className="mt-1 block truncate text-[10px] text-danger">{w.error}</span>
                        )}
                        {!isRunning && w.status === 'stopped' && w.conclusion && (
                          <span className="mt-1 block truncate text-[10px] text-zinc-500">{w.conclusion}</span>
                        )}
                      </span>
                      <span className="shrink-0 text-right">
                        <span
                          className={cn(
                            'block text-[13px] font-semibold tabular-nums',
                            isRunning ? 'text-sky-300' : w.status === 'failed' ? 'text-danger' : 'text-zinc-300',
                          )}
                        >
                          {elapsedMs > 0 ? fmtDuration(elapsedMs) : '—'}
                        </span>
                        <span className="mt-px block text-[9px] tracking-[0.1em] text-zinc-600">
                          {elapsedMs > 0 ? tr(isRunning ? '已运行' : '用时', isRunning ? 'elapsed' : 'took') : ''}
                        </span>
                      </span>
                      {open ? (
                        <ChevronDown size={12} className="shrink-0 text-zinc-600" />
                      ) : (
                        <ChevronRight size={12} className="shrink-0 text-zinc-600" />
                      )}
                    </button>
                    {/* 运行中跑道底部进度带 */}
                    {isRunning && (
                      <div className="absolute inset-x-0 bottom-0 h-[2px] bg-zinc-800/80">
                        <div className="h-full w-full animate-grow rounded-r bg-sky-400" />
                      </div>
                    )}
                    {open && (
                      <div className="flex flex-col gap-2 border-t border-zinc-800/60 px-3 py-2.5">
                        {w.conclusion && (
                          <p className="text-[11px] leading-5 text-zinc-300">{w.conclusion}</p>
                        )}
                        {findings.map((f) => (
                          <div
                            key={f.n}
                            id={`inv-finding-${f.n}`}
                            className={cn(
                              'flex gap-2 rounded-md px-1.5 py-1 transition-colors',
                              highlightN === f.n && 'bg-indigo-500/15',
                            )}
                          >
                            <span className="mt-[2px] grid h-4 w-4 shrink-0 place-items-center rounded bg-indigo-500/15 text-[9px] font-medium text-indigo-300">
                              {f.n}
                            </span>
                            <span className="min-w-0 text-[10.5px] leading-4.5 text-zinc-400">
                              <b className="font-medium text-zinc-200">{f.title}</b>
                              {f.detail && <span>：{f.detail}</span>}
                              {f.evidence.map((ev, i) => (
                                <span key={`${ev.call_id}-${i}`} className="mt-0.5 block truncate text-[9.5px] text-zinc-500">
                                  {tr('证据', 'evidence')} {ev.call_id.slice(0, 8)}
                                  {ev.tool ? ` · ${ev.tool}` : ''}
                                  {ev.excerpt ? ` · ${ev.excerpt.slice(0, 60)}` : ''}
                                </span>
                              ))}
                            </span>
                          </div>
                        ))}
                        {logs.length > 0 && (
                          <CodeBlock maxHeightClassName="max-h-36">
                            {logs.slice(-20).join('\n')}
                          </CodeBlock>
                        )}
                        {w.error && w.status !== 'failed' && (
                          <div className="flex items-start gap-1.5 rounded-md border border-danger/25 bg-danger/5 px-2 py-1.5 text-[10px] leading-4 text-danger">
                            <AlertTriangle size={11} className="mt-0.5 shrink-0" />
                            <span className="min-w-0 break-words">{w.error}</span>
                          </div>
                        )}
                        {/* 失败恢复：只留重试（执行全程无干预按钮） */}
                        {(w.status === 'failed' || w.status === 'stopped') && (
                          <div>
                            <RowAction
                              icon={RefreshCw}
                              onClick={() => void doRetry(w.worker_uid)}
                              disabled={busyKey !== null}
                            >
                              {busyKey === w.worker_uid
                                ? tr('重试中…', 'Retrying…')
                                : tr('重试此路', 'Retry')}
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
        </>
      )}
    </aside>
  );
}

/** motif 单轨：done 满填充 / run 半填充+扫光 / fail 红填充 / wait 小填充。 */
function MotifTrack({ status }: { status: WorkerStatus }) {
  return (
    <div className="relative h-full flex-1 overflow-hidden rounded-md bg-zinc-800/40 ring-1 ring-inset ring-zinc-700/50">
      {status === 'succeeded' && <div className="absolute inset-0 bg-ok/20" />}
      {status === 'running' && (
        <>
          <div className="absolute inset-y-0 left-0 w-[62%] bg-sky-400/15" />
          <div className="absolute inset-y-0 left-0 w-1/3 animate-sweep bg-gradient-to-r from-transparent via-sky-400/50 to-transparent" />
        </>
      )}
      {status === 'failed' && <div className="absolute inset-0 bg-danger/20" />}
      {(status === 'queued' || status === 'stopped') && (
        <div className="absolute inset-y-0 left-0 w-[12%] bg-zinc-600/30" />
      )}
    </div>
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
