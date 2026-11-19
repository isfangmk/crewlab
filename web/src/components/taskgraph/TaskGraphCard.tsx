// 任务图折叠卡：对话内展示一张图的节点拓扑、进度与草稿确认动作。
// 按 depends_on 自动分层——无依赖并排（并行跑道），有依赖连线（流水线/汇合）。

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, GitBranch, GripVertical, Plus, Trash2 } from 'lucide-react';

import {
  confirmGraph,
  getTaskGraphDetail,
  stopGraph,
  subscribeGraphEvents,
  updateGraphNodes,
  type GraphStatus,
  type TaskGraph,
  type TaskGraphNode,
  type TaskGraphStreamEvent,
} from '@/api/taskGraph';
import { relaySummaryDelta, requestChatRefetch } from '@/lib/chatLiveBus';
import { useExecLock } from '@/lib/useExecLock';
import { fmtAgo, fmtDuration, useTicker } from '@/lib/runTimer';
import { GRAPH_STATUS_META } from '@/lib/taskGraphStatus';
import { buildMermaidGraph } from '@/lib/taskGraphMermaid';
import { MermaidGraph } from '@/components/MermaidGraph';
import { cn } from '@/lib/cn';
import { StatusPill, flash } from '@/components/ui';
import { useI18n } from '@/i18n/locale';

type GraphToolResult = {
  graph_uid?: string;
  title?: string;
  status?: GraphStatus;
  message?: string;
  nodes?: {
    local_id?: string;
    name?: string;
    depends_on?: string[];
    status?: string;
    summary?: string;
    error?: string;
    agent_id?: string;
  }[];
  stats?: { total?: number; succeeded?: number; running?: number; failed?: number };
};

type EditNode = {
  key: string;
  local_id: string;
  name: string;
  target: string;
  instruction: string;
  tools: string;
  skills: string;
  depends_on: string;
};

export function TaskGraphCard({
  call,
  pending,
  interactive: interactiveProp,
}: {
  call: {
    name: string;
    status?: string;
    arguments?: unknown;
    result?: unknown;
    duration_ms?: number;
  };
  pending?: boolean;
  interactive?: boolean;
}) {
  const { tr } = useI18n();
  const result = (call.result ?? {}) as GraphToolResult;
  const args = (call.arguments ?? {}) as { nodes_json?: string };
  const [wire, setWire] = useState<TaskGraph | null>(null);
  const [expanded, setExpanded] = useState(true);
  const [busy, setBusy] = useState(false);
  const [editNodes, setEditNodes] = useState<EditNode[] | null>(null);
  const [selectedNodeUid, setSelectedNodeUid] = useState<string | null>(null);
  const [beat, setBeat] = useState<{ at: number; tool: string } | null>(null);
  const startedRef = useRef<number | null>(null);

  const graphUid = result.graph_uid ?? wire?.graph_uid ?? null;
  const interactive = interactiveProp && !pending;
  const statusKey: GraphStatus = wire?.status ?? result.status ?? 'draft';
  const active = statusKey === 'running';
  const isDraft = statusKey === 'draft';
  const isPaused = statusKey === 'paused';
  const tick = useTicker(active);

  const loadWire = useCallback(async () => {
    if (!graphUid) return;
    try {
      setWire(await getTaskGraphDetail(graphUid));
    } catch {
      // 快照未就绪时保留工具结果里的静态节点。
    }
  }, [graphUid]);

  useEffect(() => {
    void loadWire();
  }, [loadWire]);

  useEffect(() => {
    if (!graphUid || !active || pending) return;
    const ctrl = new AbortController();
    subscribeGraphEvents(
      graphUid,
      (ev: TaskGraphStreamEvent) => {
        if (ev.event === 'summary_delta') {
          relaySummaryDelta(ev.message_id, ev.delta, ev.partial);
          return;
        }
        if (ev.event === 'node_log') {
          setBeat({ at: Date.now(), tool: ev.tool });
          return;
        }
        if (ev.event === 'graph_finished') {
          void loadWire();
          if (ev.summary_message_id) requestChatRefetch();
          return;
        }
        void loadWire();
      },
      ctrl.signal,
    ).catch(() => {});
    return () => ctrl.abort();
  }, [graphUid, active, pending, loadWire]);

  useExecLock(graphUid ?? '', active);

  const nodes = useMemo<TaskGraphNode[]>(() => {
    if (wire?.nodes?.length) return wire.nodes;
    if (result.nodes?.length) {
      return result.nodes.map((n, i) => ({
        node_uid: n.local_id ?? `arg-${i}`,
        local_id: n.local_id ?? `arg-${i}`,
        depends_on: n.depends_on ?? [],
        order_index: i,
        name: n.name ?? n.local_id ?? `#${i + 1}`,
        target: '',
        instruction: '',
        tools: [],
        skills: [],
        agent_id: n.agent_id ?? '',
        status: (n.status as TaskGraphNode['status']) ?? 'pending',
        attempt: 1,
        summary: n.summary ?? '',
        findings: [],
        tool_trace: [],
        log_text: '',
        usage: {},
        error: n.error ?? '',
        duration_ms: 0,
        started_at: null,
        finished_at: null,
      }));
    }
    // 最后从 arguments 的 nodes_json 兜底（草稿还未落库时）。
    try {
      const raw = JSON.parse(args.nodes_json ?? '[]') as {
        id?: string;
        name?: string;
        target?: string;
        depends_on?: string[];
        agent_id?: string;
      }[];
      return raw.map((n, i) => ({
        node_uid: n.id ?? `arg-${i}`,
        local_id: n.id ?? `arg-${i}`,
        depends_on: n.depends_on ?? [],
        order_index: i,
        name: n.name ?? n.id ?? `#${i + 1}`,
        target: n.target ?? '',
        instruction: '',
        tools: [],
        skills: [],
        agent_id: n.agent_id ?? '',
        status: 'pending' as const,
        attempt: 1,
        summary: '',
        findings: [],
        tool_trace: [],
        log_text: '',
        usage: {},
        error: '',
        duration_ms: 0,
        started_at: null,
        finished_at: null,
      }));
    } catch {
      return [];
    }
  }, [wire, result.nodes, args.nodes_json]);

  const stats = useMemo(() => {
    if (wire?.stats) return wire.stats;
    const base = { total: nodes.length, succeeded: 0, running: 0, failed: 0, skipped: 0, stopped: 0, pending: 0, tool_calls: 0 };
    for (const n of nodes) {
      if (n.status === 'succeeded') base.succeeded++;
      else if (n.status === 'running') base.running++;
      else if (n.status === 'failed') base.failed++;
      else if (n.status === 'skipped') base.skipped++;
      else if (n.status === 'stopped') base.stopped++;
      else base.pending++;
    }
    return { ...base, ...(result.stats ?? {}) };
  }, [wire, nodes, result.stats]);

  // click 回调名：与 definition 里的 click 指令一致，节点点击 → 展开该节点详情。
  const callbackName = useMemo(
    () => `tg_card_click_${(graphUid ?? 'draft').replace(/[^a-zA-Z0-9_]/g, '_')}`,
    [graphUid],
  );
  const mermaidText = useMemo(
    () => buildMermaidGraph(nodes, callbackName),
    [nodes, callbackName],
  );
  const selectedNode = nodes.find((n) => n.node_uid === selectedNodeUid) ?? null;
  const meta = GRAPH_STATUS_META[statusKey] ?? GRAPH_STATUS_META.running;

  const totalMs = useMemo(() => {
    if (wire?.duration_ms && wire.duration_ms > 0) return wire.duration_ms;
    if (wire?.started_at) {
      const from = new Date(wire.started_at).getTime();
      if (wire.finished_at) return new Date(wire.finished_at).getTime() - from;
      if (active) return tick - from;
    }
    if (startedRef.current !== null) return tick - startedRef.current;
    return 0;
  }, [wire, active, tick]);

  const statusLabel = active && stats.running > 0
    ? tr(`${stats.running} 个节点运行中`, `${stats.running} nodes running`)
    : tr(meta.zh, meta.en);

  const enterEdit = () => {
    setEditNodes(
      nodes.map((n, i) => ({
        key: n.node_uid ?? `e-${i}`,
        local_id: n.local_id,
        name: n.name,
        target: n.target,
        instruction: n.instruction,
        tools: n.tools.join(', '),
        skills: n.skills.join(', '),
        depends_on: n.depends_on.join(', '),
      })),
    );
  };

  const saveEdit = async (thenStart: boolean) => {
    if (!graphUid || !editNodes) return;
    const cleaned = editNodes.filter((n) => n.name.trim() !== '');
    if (cleaned.length === 0) {
      flash('err', tr('至少保留一个节点', 'Keep at least one node'));
      return;
    }
    setBusy(true);
    try {
      const payload = cleaned.map((n) => ({
        id: n.local_id.trim() || `n${Math.random().toString(36).slice(2, 6)}`,
        name: n.name.trim(),
        target: n.target.trim() || n.name.trim(),
        instruction: n.instruction.trim(),
        tools: n.tools.split(/[,，\s]+/).map((t) => t.trim()).filter(Boolean),
        skills: n.skills.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean),
        depends_on: n.depends_on.split(/[,，\s]+/).map((d) => d.trim()).filter(Boolean),
      }));
      await updateGraphNodes(graphUid, JSON.stringify(payload));
      if (thenStart) await confirmGraph(graphUid);
      flash('ok', tr('任务图已更新', 'Task graph updated'));
      setEditNodes(null);
      if (thenStart) startedRef.current = Date.now();
      await loadWire();
    } catch (err) {
      flash('err', tr('保存失败', 'Save failed'));
    } finally {
      setBusy(false);
    }
  };

  const doConfirm = async () => {
    if (!graphUid) return;
    setBusy(true);
    try {
      await confirmGraph(graphUid);
      startedRef.current = Date.now();
      flash('ok', tr('任务图已开始执行', 'Task graph started'));
      await loadWire();
    } catch {
      flash('err', tr('启动失败', 'Start failed'));
    } finally {
      setBusy(false);
    }
  };

  const doStop = async () => {
    if (!graphUid) return;
    setBusy(true);
    try {
      await stopGraph(graphUid);
      flash('ok', tr('已请求停止', 'Stop requested'));
      await loadWire();
    } catch {
      flash('err', tr('停止失败', 'Stop failed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={cn(
      'w-full overflow-hidden rounded-[13px] border bg-zinc-900/40',
      stats.failed > 0 ? 'border-danger/30' : 'border-zinc-800/60',
    )}>
      <button
        type="button"
        aria-expanded={expanded}
        aria-label={tr(`任务图 ${nodes.length} 节点`, `Task graph ${nodes.length} nodes`)}
        className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left"
        onClick={() => {
          setExpanded((v) => !v);
          if (!expanded) void loadWire();
        }}
      >
        {expanded ? <ChevronDown size={14} className="text-zinc-500" /> : <ChevronRight size={14} className="text-zinc-500" />}
        <span className="grid h-6 w-6 shrink-0 place-items-center rounded-lg bg-accent/12 text-accent">
          <GitBranch size={14} />
        </span>
        <span className="min-w-0 flex-1 truncate text-[12px] font-semibold text-zinc-100">
          {result.title ?? wire?.title ?? tr('任务图', 'Task graph')} · {nodes.length} {tr('节点', 'nodes')}
        </span>
        <StatusPill tone={meta.tone} label={statusLabel} pulse={meta.pulse} />
        <span className={cn('text-[11px] font-semibold tabular-nums', active ? 'text-accent' : 'text-zinc-300')}>
          {active ? tr('已运行', 'Elapsed') : tr('用时', 'Time')} {fmtDuration(totalMs)}
        </span>
      </button>

      {/* 拓扑图：mermaid 按 depends_on 渲染并行起点与汇合，节点按状态着色 */}
      <div className="border-t border-zinc-800/60 px-2.5 py-2">
        <MermaidGraph
          definition={mermaidText}
          ariaLabel={tr(`任务图拓扑：${nodes.length} 节点（点击查看节点详情）`, `Task graph topology: ${nodes.length} nodes (click a node for details)`)}
          className="[&_svg]:max-h-40 [&_.node]:cursor-pointer"
          callbackName={callbackName}
          onNodeClick={(uid) => setSelectedNodeUid((cur) => (cur === uid ? null : uid))}
        />
      </div>

      {/* 点击节点展开：助理 / 工具 / 指令 / 用量 / 预算 / skill 说明。 */}
      {selectedNode && (
        <div className="border-t border-zinc-800/60 bg-zinc-950/40 px-3.5 py-2.5 text-[11px]">
          <div className="mb-1.5 flex items-center gap-2">
            <span className="font-semibold text-zinc-100">{selectedNode.name}</span>
            <span className="text-zinc-500">{selectedNode.local_id}</span>
            <button
              type="button"
              aria-label={tr('关闭节点详情', 'Close node details')}
              className="ml-auto text-zinc-500 hover:text-zinc-200"
              onClick={() => setSelectedNodeUid(null)}
            >
              ✕
            </button>
          </div>
          <dl className="grid grid-cols-1 gap-x-4 gap-y-1 md:grid-cols-2">
            <div>
              <dt className="text-zinc-500">{tr('使用助理', 'Assistant')}</dt>
              <dd className="text-zinc-200">{selectedNode.agent_id || tr('通用节点人设', 'Generic node persona')}</dd>
            </div>
            <div>
              <dt className="text-zinc-500">{tr('技能', 'Skills')}</dt>
              <dd className="text-zinc-200">
                {selectedNode.skills.length > 0
                  ? selectedNode.skills.join(', ')
                  : tr('无', 'None')}
              </dd>
            </div>
            <div>
              <dt className="text-zinc-500">{tr('本节点工具', 'Node tools')}</dt>
              <dd className="text-zinc-200">
                {selectedNode.tools.length > 0
                  ? selectedNode.tools.join(', ')
                  : tr('由助理自带口径决定', 'Determined by the assistant')}
              </dd>
            </div>
            <div>
              <dt className="text-zinc-500">{tr('预算', 'Budget')}</dt>
              <dd className="text-zinc-200">
                {tr('步数', 'Steps')} {wire?.config?.max_steps_per_node ?? '-'}
                {' · '}{tr('超时', 'Timeout')} {wire?.config?.timeout_seconds_per_node ?? '-'}s
                {' · '}{tr('Token', 'Token')} {wire?.config?.token_budget_per_node ?? '-'}
              </dd>
            </div>
            <div>
              <dt className="text-zinc-500">{tr('排查目标', 'Target')}</dt>
              <dd className="text-zinc-200">{selectedNode.target || '-'}</dd>
            </div>
            <div>
              <dt className="text-zinc-500">{tr('用量', 'Usage')}</dt>
              <dd className="text-zinc-200">
                {tr('Token', 'Token')} {selectedNode.usage?.total_tokens ?? '-'}
                {' · '}{tr('耗时', 'Time')} {fmtDuration(selectedNode.duration_ms)}
              </dd>
            </div>
          </dl>
          {selectedNode.instruction && (
            <div className="mt-1.5">
              <div className="text-zinc-500">{tr('排查指令', 'Instruction')}</div>
              <div className="whitespace-pre-wrap text-zinc-300">{selectedNode.instruction}</div>
            </div>
          )}
          {selectedNode.summary && (
            <div className="mt-1.5">
              <div className="text-zinc-500">{tr('结论', 'Conclusion')}</div>
              <div className="whitespace-pre-wrap text-zinc-300">{selectedNode.summary}</div>
            </div>
          )}
        </div>
      )}

      {expanded && (isDraft || isPaused) && editNodes === null && (
        <div className="flex flex-wrap items-center gap-2 border-t border-zinc-800/60 px-3.5 py-2.5">
          <span className="mr-auto text-[10px] text-zinc-500">
            {pending
              ? tr('模型正在输出，结束后可执行', 'Model is responding; act after it finishes')
              : isDraft
                ? tr('确认后按依赖拓扑执行，收尾自动汇总', 'Confirm to run by topology; auto-summary at the end')
                : tr('已暂停，可继续执行', 'Paused; you can resume')}
          </span>
          {isDraft && (
            <button
              type="button"
              disabled={!interactive || busy}
              onClick={enterEdit}
              className="h-7 rounded-lg border border-zinc-700 px-2.5 text-[11px] font-medium text-zinc-300 hover:border-accent/60 disabled:opacity-40"
            >
              {tr('调整任务图', 'Edit graph')}
            </button>
          )}
          <button
            type="button"
            disabled={!interactive || busy}
            onClick={() => void doConfirm()}
            className="h-7 rounded-lg bg-accent px-2.5 text-[11px] font-medium text-accent-fg hover:brightness-110 disabled:opacity-40"
          >
            {busy ? tr('处理中…', 'Working…') : isDraft ? tr('开始执行', 'Run') : tr('继续执行', 'Resume')}
          </button>
          {active && (
            <button
              type="button"
              disabled={!interactive || busy}
              onClick={() => void doStop()}
              className="h-7 rounded-lg border border-danger/40 px-2.5 text-[11px] font-medium text-danger hover:bg-danger/10 disabled:opacity-40"
            >
              {tr('停止', 'Stop')}
            </button>
          )}
        </div>
      )}

      {expanded && editNodes !== null && (
        <div className="flex flex-col gap-2 border-t border-zinc-800/60 bg-zinc-950/40 px-3.5 py-3">
          {editNodes.map((node, i) => (
            <div key={node.key} className="flex items-start gap-1.5">
              <GripVertical size={13} className="mt-2 shrink-0 text-zinc-600" />
              <div className="grid min-w-0 flex-1 grid-cols-2 gap-1.5">
                <input
                  aria-label={tr('节点名', 'Node name')}
                  value={node.name}
                  onChange={(e) => setEditNodes((prev) => prev!.map((n, j) => j === i ? { ...n, name: e.target.value } : n))}
                  className="h-7 rounded-md border border-zinc-700 bg-zinc-900 px-2 text-[11px] text-zinc-100 focus:border-accent/60 focus:outline-none"
                />
                <input
                  aria-label={tr('依赖节点', 'Depends on')}
                  placeholder="depends_on, e.g. A,B"
                  value={node.depends_on}
                  onChange={(e) => setEditNodes((prev) => prev!.map((n, j) => j === i ? { ...n, depends_on: e.target.value } : n))}
                  className="h-7 rounded-md border border-zinc-700 bg-zinc-900 px-2 text-[11px] text-zinc-100 focus:border-accent/60 focus:outline-none"
                />
                <input
                  aria-label={tr('执行目标', 'Target')}
                  value={node.target}
                  onChange={(e) => setEditNodes((prev) => prev!.map((n, j) => j === i ? { ...n, target: e.target.value } : n))}
                  className="col-span-2 h-7 rounded-md border border-zinc-700 bg-zinc-900 px-2 text-[11px] text-zinc-100 focus:border-accent/60 focus:outline-none"
                />
              </div>
              <button
                type="button"
                aria-label={tr('删除节点', 'Remove node')}
                onClick={() => setEditNodes((prev) => prev!.filter((_, j) => j !== i))}
                className="mt-1 text-zinc-600 hover:text-danger"
              >
                <Trash2 size={13} />
              </button>
            </div>
          ))}
          <div className="flex items-center gap-2 pt-1">
            <button
              type="button"
              disabled={editNodes.length >= 16}
              onClick={() => setEditNodes((prev) => [
                ...(prev ?? []),
                { key: `new-${Date.now()}`, local_id: '', name: '', target: '', instruction: '', tools: '', skills: '', depends_on: '' },
              ])}
              className="flex h-7 items-center gap-1 rounded-lg border border-zinc-700 px-2 text-[11px] text-zinc-300 hover:border-accent/60 disabled:opacity-40"
            >
              <Plus size={12} /> {tr('添加节点', 'Add node')}
            </button>
            <button
              type="button"
              onClick={() => setEditNodes(null)}
              className="h-7 rounded-lg px-2 text-[11px] text-zinc-500 hover:text-zinc-300"
            >
              {tr('取消', 'Cancel')}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void saveEdit(true)}
              className="ml-auto h-7 rounded-lg bg-accent px-2.5 text-[11px] font-medium text-accent-fg hover:brightness-110 disabled:opacity-40"
            >
              {tr('保存并执行', 'Save & run')}
            </button>
          </div>
        </div>
      )}

      <div className="flex items-center gap-2 border-t border-zinc-800/60 px-3.5 py-2 text-[10px] text-zinc-500">
        <GitBranch size={11} />
        {tr('拓扑式：无依赖的节点并行推进，汇合节点等全部前驱完成', 'Topology: independent nodes run in parallel; join nodes wait for all predecessors')}
        {active && beat && <span className="ml-auto">{tr('最近动作', 'Last')} {fmtAgo(beat.at, tick)}</span>}
      </div>
    </div>
  );
}
