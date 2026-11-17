// 统一任务图工作台：右栏按依赖拓扑展示一张图的所有节点，
// 点节点展开结论/证据/日志/工具轨迹；SSE 静默对齐 + loadSeq 竞态保护 + 定位高亮。

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  GitBranch,
  RefreshCw,
  SkipForward,
  X,
} from 'lucide-react';

import {
  getTaskGraphDetail,
  listTaskGraphs,
  retryGraphNode,
  skipGraphNode,
  subscribeGraphEvents,
  type GraphFinding,
  type TaskGraph,
  type TaskGraphNode,
  type TaskGraphStreamEvent,
} from '@/api/taskGraph';
import { CHAT_LIVE_EVENT, relaySummaryDelta, requestChatRefetch, type ChatLiveDetail } from '@/lib/chatLiveBus';
import { fmtDuration, useTicker } from '@/lib/runTimer';
import { fullDateTime } from '@/lib/format';
import { GRAPH_STATUS_META, NODE_STATUS_META } from '@/lib/taskGraphStatus';
import { buildMermaidGraph } from '@/lib/taskGraphMermaid';
import { MermaidGraph } from '@/components/MermaidGraph';
import { cn } from '@/lib/cn';
import {
  CodeBlock,
  EmptyState,
  RowAction,
  Select,
  StatusPill,
  flash,
} from '@/components/ui';
import { errorText } from '@/lib/errors';
import { useI18n } from '@/i18n/locale';

export type TaskGraphLocateTarget =
  | { kind: 'finding'; n: number }
  | { kind: 'node'; nodeUid: string };

export function TaskGraphWorkbench({
  sessionId,
  locate,
  onClose,
}: {
  sessionId: string;
  locate?: (TaskGraphLocateTarget & { seq: number }) | null;
  onClose(): void;
}) {
  const { tr } = useI18n();
  const [items, setItems] = useState<TaskGraph[] | null>(null);
  const [selectedUid, setSelectedUid] = useState<string | null>(null);
  const [wire, setWire] = useState<TaskGraph | null>(null);
  const [err, setErr] = useState('');
  const [liveLogs, setLiveLogs] = useState<Record<string, string[]>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [highlightN, setHighlightN] = useState<number | null>(null);
  const [pendingLocate, setPendingLocate] = useState<TaskGraphLocateTarget | null>(null);
  const loadSeq = useRef(0);

  const active = (wire?.status ?? 'running') === 'running';
  const tick = useTicker(active);

  const loadList = useCallback(async () => {
    try {
      const res = await listTaskGraphs({ session_id: sessionId, limit: 20 });
      setItems(res.items);
      setSelectedUid((cur) => (cur && res.items.some((g) => g.graph_uid === cur) ? cur : res.items[0]?.graph_uid ?? null));
    } catch (e) {
      setErr(errorText(e));
    }
  }, [sessionId]);

  const loadWire = useCallback(async (uid: string, silent: boolean) => {
    const seq = (loadSeq.current += 1);
    if (!silent) setWire(null);
    try {
      const data = await getTaskGraphDetail(uid);
      if (seq !== loadSeq.current) return; // 过期快照不覆盖新的
      setWire(data);
      setErr('');
    } catch (e) {
      if (seq !== loadSeq.current) return;
      if (!silent) setErr(errorText(e));
    }
  }, []);

  useEffect(() => { void loadList(); }, [loadList]);

  // graph_created 事件：重拉列表并切到新图。
  useEffect(() => {
    const onLive = (e: Event) => {
      const detail = (e as CustomEvent<ChatLiveDetail>).detail;
      if (detail?.type === 'graph_created' && detail.graphUid) {
        setSelectedUid(detail.graphUid);
        void loadList();
      }
    };
    window.addEventListener(CHAT_LIVE_EVENT, onLive);
    return () => window.removeEventListener(CHAT_LIVE_EVENT, onLive);
  }, [loadList]);

  useEffect(() => {
    if (selectedUid) void loadWire(selectedUid, false);
  }, [selectedUid, loadWire]);

  useEffect(() => {
    if (!selectedUid) return;
    const ctrl = new AbortController();
    subscribeGraphEvents(
      selectedUid,
      (ev: TaskGraphStreamEvent) => {
        if (ev.event === 'summary_delta') {
          relaySummaryDelta(ev.message_id, ev.delta, ev.partial);
          return;
        }
        if (ev.event === 'node_log') {
          setLiveLogs((prev) => ({
            ...prev,
            [ev.node_uid]: [...(prev[ev.node_uid] ?? []), ev.line].slice(-200),
          }));
          return;
        }
        if (ev.event === 'graph_finished' && ev.summary_message_id) {
          requestChatRefetch();
        }
        void loadWire(selectedUid, true); // 静默对齐 DB，不闪空
      },
      ctrl.signal,
    ).catch((e) => {
      if ((e as Error).name !== 'AbortError') setErr(errorText(e));
    });
    return () => ctrl.abort();
  }, [selectedUid, loadWire]);

  // 定位：从引用角标/折叠块跳到具体节点或发现。
  useEffect(() => {
    if (!locate) return;
    setPendingLocate(locate);
    if (locate.kind === 'node') setExpanded((prev) => ({ ...prev, [locate.nodeUid]: true }));
  }, [locate]);

  useEffect(() => {
    if (!pendingLocate || !items) return;
    let owner: TaskGraph | undefined;
    if (pendingLocate.kind === 'node') {
      owner = items.find((g) => g.nodes.some((n) => n.node_uid === pendingLocate.nodeUid));
    } else {
      owner = items.find((g) => g.nodes.some((n) => n.findings.some((f, i) => i + 1 === pendingLocate.n)));
    }
    if (!owner) return;
    if (owner.graph_uid !== selectedUid) {
      setSelectedUid(owner.graph_uid);
      return; // 等新图的 wire 加载完再滚
    }
    if (!wire) return;
    let targetId: string | null = null;
    if (pendingLocate.kind === 'node') {
      setExpanded((prev) => ({ ...prev, [pendingLocate.nodeUid]: true }));
      targetId = `tg-node-${pendingLocate.nodeUid}`;
    } else {
      setHighlightN(pendingLocate.n);
      const node = wire.nodes.find((n) => n.findings.some((_f, i) => i + 1 === pendingLocate.n));
      if (node) {
        setExpanded((prev) => ({ ...prev, [node.node_uid]: true }));
        targetId = `tg-finding-${node.node_uid}-${pendingLocate.n}`;
      }
    }
    if (targetId) {
      requestAnimationFrame(() => requestAnimationFrame(() => {
        document.getElementById(targetId)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }));
    }
    setPendingLocate(null);
  }, [pendingLocate, items, selectedUid, wire]);

  const stats = wire?.stats;
  const mermaidText = useMemo(() => buildMermaidGraph(wire?.nodes ?? []), [wire]);
  const meta = wire ? GRAPH_STATUS_META[wire.status] : GRAPH_STATUS_META.running;

  const doRetry = async (nodeUid: string) => {
    if (!selectedUid) return;
    setBusyKey(nodeUid);
    try {
      await retryGraphNode(selectedUid, nodeUid);
      flash('ok', tr('已重试节点', 'Node retried'));
      await loadWire(selectedUid, true);
    } catch (e) {
      flash('err', errorText(e));
    } finally {
      setBusyKey(null);
    }
  };

  const doSkip = async (nodeUid: string) => {
    if (!selectedUid) return;
    setBusyKey(nodeUid);
    try {
      await skipGraphNode(selectedUid, nodeUid);
      flash('ok', tr('已跳过节点', 'Node skipped'));
      await loadWire(selectedUid, true);
    } catch (e) {
      flash('err', errorText(e));
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <aside
      className="flex h-full w-[26rem] shrink-0 flex-col overflow-hidden border-l border-zinc-800/60 bg-zinc-900/40"
      aria-label={tr('任务图工作台', 'Task graph workbench')}
    >
      <div className="border-b border-zinc-800/60 px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="grid h-6 w-6 shrink-0 place-items-center rounded-lg bg-accent/12 text-accent">
            <GitBranch size={14} />
          </span>
          <span className="text-[10px] font-semibold tracking-[0.08em] text-accent">
            {tr('任务图 · 拓扑', 'TASK GRAPH · TOPOLOGY')}
          </span>
          {wire && <span className="text-[10px] text-zinc-600">#{wire.id}</span>}
          {wire && <StatusPill tone={meta.tone} label={tr(meta.zh, meta.en)} pulse={meta.pulse} />}
          <button type="button" onClick={onClose} aria-label={tr('关闭', 'Close')} className="ml-auto text-zinc-500 hover:text-zinc-200">
            <X size={15} />
          </button>
        </div>
        {wire && (
          <>
            <div className="mt-2 line-clamp-2 text-[14px] font-semibold text-zinc-100">
              {wire.title || wire.question}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-zinc-500">
              <span>{tr('开始于', 'Started')} {wire.started_at || wire.created_at ? fullDateTime(wire.started_at ?? wire.created_at ?? '') : '—'}</span>
              <span>{tr('全程', 'Total')} {fmtDuration(wire.duration_ms || (active && wire.started_at ? tick - new Date(wire.started_at).getTime() : 0))}</span>
              <span>{stats?.running ?? 0} {tr('个节点在跑', 'nodes running')}</span>
            </div>
            {/* 拓扑图：mermaid 按 depends_on 渲染并行起点与汇合，节点按状态着色 */}
            <div className="mt-3 rounded-lg border border-zinc-800/60 bg-zinc-950/40 px-2 py-2">
              {wire && wire.nodes.length > 0 && (
                <MermaidGraph
                  definition={mermaidText}
                  ariaLabel={tr('任务图拓扑', 'Task graph topology')}
                  className="[&_svg]:max-h-44"
                />
              )}
            </div>
            {items && items.length > 1 && (
              <div className="mt-3">
                <Select
                  value={selectedUid ?? ''}
                  onValueChange={(v) => setSelectedUid(v)}
                  options={items.map((g) => ({ value: g.graph_uid, label: g.title || g.question || g.graph_uid.slice(0, 8) }))}
                  aria-label={tr('切换任务图', 'Switch task graph')}
                />
              </div>
            )}
          </>
        )}
      </div>

      {err && <div className="border-b border-danger/20 bg-danger/5 px-4 py-2 text-[11px] text-danger">{err}</div>}

      {items !== null && items.length === 0 ? (
        <EmptyState icon={GitBranch} title={tr('本会话还没有任务图', 'No task graph in this session')} hint={tr('提交任务图后，进度会实时出现在这里', 'Progress appears here after you submit a graph')} />
      ) : !wire ? (
        <div className="flex h-40 items-center justify-center text-[12px] text-zinc-600">{tr('加载中…', 'Loading…')}</div>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-2 border-b border-zinc-800/60 px-4 py-3">
            <StatCell label={tr('进度', 'Progress')} value={`${(stats?.succeeded ?? 0) + (stats?.skipped ?? 0)}/${stats?.total ?? 0}`} />
            <StatCell label={tr('工具调用', 'Tool calls')} value={String(stats?.tool_calls ?? 0)} />
            <StatCell label={tr('用量', 'Usage')} value={wire.used_tokens > 0 ? `${(wire.used_tokens / 1000).toFixed(1)}k` : '—'} unit="tok" />
          </div>
          <div className="flex-1 overflow-y-auto px-3 py-3">
            <div className="flex flex-col gap-1.5">
              {wire.nodes.map((node) => (
                <NodeRow
                  key={node.node_uid}
                  node={node}
                  logs={liveLogs[node.node_uid] ?? []}
                  expanded={expanded[node.node_uid] ?? false}
                  onToggle={() => setExpanded((prev) => ({ ...prev, [node.node_uid]: !prev[node.node_uid] }))}
                  busy={busyKey === node.node_uid}
                  onRetry={() => void doRetry(node.node_uid)}
                  onSkip={() => void doSkip(node.node_uid)}
                  highlightN={highlightN}
                  tick={tick}
                />
              ))}
            </div>
          </div>
        </>
      )}
    </aside>
  );
}

function StatCell({ label, value, unit }: { label: string; value: string; unit?: string }) {
  return (
    <div className="rounded-lg border border-zinc-800/60 bg-zinc-950/40 px-2.5 py-2">
      <div className="text-[9.5px] tracking-wide text-zinc-500">{label}</div>
      <div className="mt-0.5 text-[15px] font-semibold tabular-nums text-zinc-100">
        {value}{unit && <span className="ml-0.5 text-[9px] font-normal text-zinc-500">{unit}</span>}
      </div>
    </div>
  );
}

function NodeRow({
  node, logs, expanded, onToggle, busy, onRetry, onSkip, highlightN, tick,
}: {
  node: TaskGraphNode;
  logs: string[];
  expanded: boolean;
  onToggle(): void;
  busy: boolean;
  onRetry(): void;
  onSkip(): void;
  highlightN: number | null;
  tick: number;
}) {
  const { tr } = useI18n();
  const nm = NODE_STATUS_META[node.status] ?? NODE_STATUS_META.pending;
  const canRecover = node.status === 'failed' || node.status === 'stopped';
  const elapsed = node.duration_ms > 0
    ? node.duration_ms
    : node.started_at && node.status === 'running'
      ? tick - new Date(node.started_at).getTime()
      : 0;

  return (
    <div
      id={`tg-node-${node.node_uid}`}
      className={cn(
        'overflow-hidden rounded-lg border',
        node.status === 'running' ? 'border-accent/30 bg-accent/5'
          : node.status === 'failed' ? 'border-danger/30 bg-danger/5'
          : 'border-zinc-800/60 bg-zinc-950/40',
      )}
    >
      <button type="button" onClick={onToggle} className="flex w-full items-center gap-2 px-2.5 py-2 text-left">
        <span className={cn(
          'grid h-6 w-6 shrink-0 place-items-center rounded-md text-[10px] font-bold',
          node.status === 'succeeded' ? 'bg-ok/15 text-ok'
            : node.status === 'failed' ? 'bg-danger/15 text-danger'
            : node.status === 'skipped' ? 'bg-warn/15 text-warn'
            : 'bg-zinc-800 text-zinc-400',
        )}>
          {node.status === 'succeeded' ? <Check size={13} />
            : node.status === 'failed' ? <X size={13} />
            : node.status === 'skipped' ? <SkipForward size={12} />
            : node.order_index + 1}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="truncate text-[12px] font-medium text-zinc-100">{node.name}</span>
            {node.attempt > 1 && <span className="rounded bg-zinc-800 px-1 text-[9px] text-zinc-400">#{node.attempt}</span>}
          </span>
          {node.depends_on.length > 0 && (
            <span className="block truncate text-[9.5px] text-zinc-600">
              {tr('依赖', 'depends on')} {node.depends_on.join(', ')}
            </span>
          )}
        </span>
        {node.status !== 'running' && <StatusPill tone={nm.tone} label={tr(nm.zh, nm.en)} />}
        <span className="w-14 shrink-0 text-right text-[10px] tabular-nums text-zinc-500">
          {elapsed > 0 ? fmtDuration(elapsed) : node.status === 'pending' || node.status === 'queued' ? tr('排队中', 'Queued') : ''}
        </span>
        {expanded ? <ChevronDown size={13} className="text-zinc-500" /> : <ChevronRight size={13} className="text-zinc-500" />}
      </button>
      {node.status === 'running' && (
        <div className="mx-2.5 mb-2 h-0.5 overflow-hidden rounded-full bg-zinc-800">
          <div className="h-full w-2/3 animate-pulse-dot rounded-full bg-accent/50" />
        </div>
      )}
      {expanded && (
        <div className="flex flex-col gap-2 border-t border-zinc-800/60 px-2.5 py-2.5">
          {node.summary && <p className="text-[11px] leading-relaxed text-zinc-300">{node.summary}</p>}
          {node.findings.map((f, i) => (
            <FindingRow key={i} finding={f} n={i + 1} highlighted={highlightN === i + 1} anchorId={`tg-finding-${node.node_uid}-${i + 1}`} />
          ))}
          {logs.length > 0 && (
            <CodeBlock maxHeightClassName="max-h-36">{logs.slice(-20).join('\n')}</CodeBlock>
          )}
          {node.error && (
            <div className="flex items-start gap-1.5 rounded-md border border-danger/20 bg-danger/5 px-2 py-1.5 text-[10.5px] text-danger">
              <AlertTriangle size={12} className="mt-0.5 shrink-0" /> {node.error}
            </div>
          )}
          {canRecover && (
            <div className="flex items-center gap-2">
              <RowAction icon={RefreshCw} tone="positive" onClick={onRetry} disabled={busy}>
                {busy ? tr('处理中…', 'Working…') : tr('重试此节点', 'Retry node')}
              </RowAction>
              <RowAction icon={SkipForward} onClick={onSkip} disabled={busy}>
                {tr('跳过并继续', 'Skip & continue')}
              </RowAction>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function FindingRow({ finding, n, highlighted, anchorId }: {
  finding: GraphFinding; n: number; highlighted: boolean; anchorId: string;
}) {
  const { tr } = useI18n();
  return (
    <div id={anchorId} className={cn('rounded-md border px-2 py-1.5', highlighted ? 'border-info/40 bg-info/10' : 'border-zinc-800/60')}>
      <div className="flex items-start gap-1.5">
        <span className="mt-0.5 inline-flex h-4 min-w-4 shrink-0 items-center justify-center rounded bg-info/15 px-1 text-[9px] font-semibold text-info">[{n}]</span>
        <span className="min-w-0 flex-1">
          <span className="block text-[11px] font-medium text-zinc-200">{finding.title}</span>
          {finding.detail && <span className="block text-[10.5px] leading-relaxed text-zinc-400">{finding.detail}</span>}
        </span>
      </div>
      {finding.evidence.length > 0 && (
        <div className="mt-1 flex flex-col gap-0.5 pl-6">
          {finding.evidence.slice(0, 3).map((ev, i) => (
            <div key={i} className="truncate text-[9.5px] text-zinc-600">
              {tr('证据', 'evidence')} {ev.call_id.slice(0, 8)} · {ev.tool} · {ev.excerpt.slice(0, 60)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
