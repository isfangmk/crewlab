// 会话排查工作台：右栏停靠面板，实时展示并行排查的 worker 状态/日志/发现/预算。
// 实时走 dispatch 事件流（软截止后后台续跑也进同一条流），低频事件静默重拉快照对齐；
// 引用角标定位：citeTarget 变化即滚动到对应发现/worker 并高亮。
import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, ChevronDown, ChevronRight, RefreshCw, Search, X } from 'lucide-react';
import {
  getInvestigation,
  listInvestigations,
  retryWorker,
  stopInvestigation,
  subscribeDispatchEvents,
  type InvestigationDispatch,
  type InvestigationFinding,
} from '@/api/investigation';
import { DISPATCH_STATUS_META, WORKER_STATUS_META } from '@/lib/investigationStatus';
import { errorText } from '@/lib/errors';
import { fullDateTime } from '@/lib/format';
import { useI18n } from '@/i18n/locale';
import { cn } from '@/lib/cn';
import {
  Button,
  CodeBlock,
  DetailStat,
  DetailStatGrid,
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

export type LocateTarget =
  | { kind: 'finding'; n: number }
  | { kind: 'worker'; workerUid: string };

export function InvestigationWorkbench({
  sessionId,
  locate,
  onRequestSummary,
  onClose,
}: {
  sessionId: string;
  /** 引用角标/折叠块跳来的定位请求；seq 变化触发滚动高亮 */
  locate?: (LocateTarget & { seq: number }) | null;
  onRequestSummary?(text: string): void;
  onClose(): void;
}) {
  const { tr } = useI18n();
  const [items, setItems] = useState<InvestigationDispatch[] | null>(null);
  const [selectedUid, setSelectedUid] = useState<string | null>(null);
  const [wire, setWire] = useState<InvestigationDispatch | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // 实时日志：事件流逐行追加；快照里的 log_text 只是兜底节选
  const [liveLogs, setLiveLogs] = useState<Record<string, string[]>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [highlightN, setHighlightN] = useState<number | null>(null);
  // 静默刷新序号：只认最后一次请求，避免慢响应回填旧数据
  const loadSeq = useRef(0);

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

  const loadWire = useCallback(
    async (uid: string, silent: boolean) => {
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
    },
    [],
  );

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
    void subscribeDispatchEvents(
      selectedUid,
      (ev) => {
        if (ev.event === 'worker_log') {
          setLiveLogs((prev) => ({
            ...prev,
            [ev.worker_uid]: [...(prev[ev.worker_uid] ?? []), ev.line].slice(-200),
          }));
          return;
        }
        if (ev.event === 'worker_started') {
          setExpanded((prev) => ({ ...prev, [ev.worker_uid]: true }));
        }
        void loadWire(selectedUid, true);
      },
      ctrl.signal,
    ).catch((e) => {
      if ((e as Error).name !== 'AbortError') setErr(errorText(e));
    });
    return () => ctrl.abort();
  }, [selectedUid, loadWire]);

  // 定位请求：滚动到发现/worker 并短暂高亮
  useEffect(() => {
    if (!locate) return;
    if (locate.kind === 'finding') {
      setHighlightN(locate.n);
      const el = document.getElementById(`inv-finding-${locate.n}`);
      el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else {
      setExpanded((prev) => ({ ...prev, [locate.workerUid]: true }));
      const el = document.getElementById(`inv-worker-${locate.workerUid}`);
      el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [locate]);

  const doStop = async () => {
    if (!selectedUid) return;
    setBusyKey('stop');
    try {
      const r = await stopInvestigation(selectedUid);
      if (!r.stopped) flash('ok', tr('没有可停止的 worker', 'No running workers to stop'));
      else flash('ok', tr('已请求全部停止', 'Stop requested'));
      await loadWire(selectedUid, true);
    } catch (e) {
      flash('err', errorText(e));
    } finally {
      setBusyKey(null);
    }
  };

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

  const askSummary = () => {
    onRequestSummary?.(
      tr(
        '请根据本轮排查的最新发现（含后台完成的部分）汇总结论',
        'Please summarize the latest investigation findings (including background completions)',
      ),
    );
  };

  const stats = wire?.stats;
  const budget = wire?.budget ?? {};
  const tokenBudget = typeof budget.token_budget_per_worker === 'number' ? budget.token_budget_per_worker : null;
  const usedTokens = typeof budget.used_tokens_total === 'number' ? budget.used_tokens_total : null;
  const findingsOf = (workerUid: string): InvestigationFinding[] =>
    (wire?.findings ?? []).filter((f) => f.worker_uid === workerUid && !f.retired);

  return (
    <aside
      className="flex h-full w-[26rem] shrink-0 flex-col overflow-hidden border-l border-zinc-800/60 bg-zinc-900/40"
      aria-label={tr('排查工作台', 'Investigation workbench')}
    >
      {/* 头部：排查标识 + 状态 + 问题 */}
      <div className="border-b border-zinc-800/60 px-3 py-2.5">
        <div className="flex items-center gap-2">
          <Search size={13} className="shrink-0 text-indigo-300" />
          <span className="text-[11px] font-medium tracking-wide text-zinc-300">
            {tr('排查工作台', 'Investigation')}
            {wire ? ` #${wire.id}` : ''}
          </span>
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
            <p className="mt-1.5 line-clamp-2 text-[12px] leading-5 text-zinc-200">{wire.question}</p>
            <p className="mt-1 text-[10px] text-zinc-500">
              {wire.created_at ? fullDateTime(wire.created_at) : ''}
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

      {!wire && !err && (
        <div className="flex flex-1 items-center justify-center text-[11px] text-zinc-500">
          {tr('加载中…', 'Loading…')}
        </div>
      )}

      {wire && stats && (
        <>
          {/* 统计与预算 */}
          <div className="border-b border-zinc-800/60 px-3 py-2.5">
            <DetailStatGrid columns={3}>
              <DetailStat label={tr('完成', 'Done')} value={`${stats.succeeded}/${stats.total}`} />
              <DetailStat label={tr('发现', 'Findings')} value={stats.findings} />
              <DetailStat label={tr('工具调用', 'Tool calls')} value={stats.tool_calls} />
            </DetailStatGrid>
            <div className="mt-2 flex items-center justify-between text-[10px] text-zinc-500">
              <span>{tr('预算用量', 'Budget')}</span>
              <span className="tabular-nums">
                {usedTokens !== null ? `${(usedTokens / 1000).toFixed(1)}k` : '—'}
                {tokenBudget !== null ? ` / ${(tokenBudget * stats.total / 1000).toFixed(0)}k tokens` : ''}
              </span>
            </div>
            <div className="mt-1 h-1 overflow-hidden rounded-full bg-zinc-800/60">
              <div
                className="h-full rounded-full bg-indigo-500"
                style={{
                  width:
                    usedTokens !== null && tokenBudget
                      ? `${Math.min(100, (usedTokens / (tokenBudget * stats.total)) * 100).toFixed(1)}%`
                      : '0%',
                }}
              />
            </div>
          </div>

          {/* worker 卡列表（唯一二级滚动区） */}
          <div className="flex-1 overflow-y-auto px-3 py-2.5">
            <div className="flex flex-col gap-2">
              {wire.workers.map((w) => {
                const meta = WORKER_STATUS_META[w.status];
                const open = expanded[w.worker_uid] === true;
                const findings = findingsOf(w.worker_uid);
                const logs = liveLogs[w.worker_uid] ?? (w.log_text ? w.log_text.split('\n') : []);
                return (
                  <div
                    key={w.worker_uid}
                    id={`inv-worker-${w.worker_uid}`}
                    className={cn(
                      'overflow-hidden rounded-lg border border-zinc-800/60 bg-zinc-950/60',
                      w.status === 'running' && 'border-info/30',
                      w.status === 'failed' && 'border-danger/30',
                    )}
                  >
                    <button
                      type="button"
                      aria-expanded={open}
                      onClick={() =>
                        setExpanded((prev) => ({ ...prev, [w.worker_uid]: !open }))
                      }
                      className="flex w-full items-center gap-2 px-2.5 py-2 text-left hover:bg-zinc-800/40"
                    >
                      {open ? (
                        <ChevronDown size={13} className="shrink-0 text-zinc-500" />
                      ) : (
                        <ChevronRight size={13} className="shrink-0 text-zinc-500" />
                      )}
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-1.5">
                          <span className="truncate text-[12px] font-medium text-zinc-200">{w.name}</span>
                          {w.attempt > 1 && (
                            <span className="rounded-full border border-zinc-700 px-1.5 text-[9.5px] text-zinc-400">
                              {tr(`第 ${w.attempt} 次`, `#${w.attempt}`)}
                            </span>
                          )}
                        </span>
                        <span className="mt-0.5 block truncate text-[10px] text-zinc-500">{w.target}</span>
                      </span>
                      <span className="flex shrink-0 flex-col items-end gap-1">
                        <StatusPill tone={meta.tone} label={tr(meta.zh, meta.en)} pulse={meta.pulse} />
                        {w.duration_ms > 0 && (
                          <span className="text-[9.5px] tabular-nums text-zinc-500">{fmtDuration(w.duration_ms)}</span>
                        )}
                      </span>
                    </button>
                    {open && (
                      <div className="flex flex-col gap-2 border-t border-zinc-800/60 px-2.5 py-2">
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
                        {w.error && (
                          <div className="flex items-start gap-1.5 rounded-md border border-danger/25 bg-danger/5 px-2 py-1.5 text-[10px] leading-4 text-danger">
                            <AlertTriangle size={11} className="mt-0.5 shrink-0" />
                            <span className="min-w-0 break-words">{w.error}</span>
                          </div>
                        )}
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

          {/* 底部动作：全部停止 / 汇总结论回对话 */}
          <div className="flex gap-2 border-t border-zinc-800/60 px-3 py-2.5">
            <Button
              variant="dangerGhost"
              className="flex-1"
              disabled={busyKey !== null || wire.stats.running === 0}
              onClick={() => void doStop()}
            >
              {busyKey === 'stop' ? tr('停止中…', 'Stopping…') : tr('全部停止', 'Stop all')}
            </Button>
            <Button
              variant="primary"
              className="flex-1"
              disabled={wire.stats.findings === 0 || onRequestSummary === undefined}
              onClick={askSummary}
            >
              {tr('汇总结论回对话', 'Summarize in chat')}
            </Button>
          </div>
        </>
      )}
    </aside>
  );
}
