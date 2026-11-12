// 运行详情：一次运行的完整诊断视图。
//
// 版面分两层：
//   顶部信息带 = 四张等宽卡（当前状态 / 耗时 / 节点进度 / 触发方式），横跨下面三栏。
//   下面三栏：
//     左栏 = 节点导航：整条流程按执行顺序列出来，● 颜色就是结果，点一下切换中间详情。
//     中栏 = 选中节点的详情：人工确认 / 输入 / 输出 / 错误都在这里，跟节点绑死。
//     右栏 = 运行上下文：画布快照、运行输入、运行输出。
//
// 每条信息只出现一处：状态与耗时只在信息带，run_uid 只在顶栏，运行输入输出只在右栏。
//
// 运行中通过事件流实时长出行，输入输出随 node_started / node_finished 立即出现，
// 不需要等整条工作流落库。

import { flushSync } from 'react-dom';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Maximize2,
  Play,
  RefreshCw,
  ShieldCheck,
  Square,
  X,
} from 'lucide-react';
import { ApiError } from '@/api/client';
import {
  getRun,
  getWorkflow,
  parseJsonText,
  resumeRun,
  startWorkflowRun,
  stopWorkflowRun,
  subscribeRunEvents,
  type NodeKind,
  type NodeRunStatus,
  type RunStatus,
  type WaitingGate,
  type WorkflowNode,
  type WorkflowNodeRun,
  type WorkflowRunDetail,
} from '@/api/workflows';
import { RunWorkflowDialog } from '@/components/workflow/RunWorkflowDialog';
import { Modal } from '@/components/Modal';
import { RunInputRows } from '@/components/workflow/RunInputRows';
import { DraftTag, formatDuration } from '@/components/workflow/RunStatusPill';
import { WorkflowGraphThumb } from '@/components/workflow/WorkflowGraphThumb';
import {
  Button,
  CodeBlock,
  EmptyStateCard,
  Input,
  RowAction,
  RowActionIcon,
  StatusPill,
  Toast,
  type ToastTone,
} from '@/components/ui';
import { cn } from '@/lib/cn';
import { fullDateTime } from '@/lib/format';
import { iconOf, kindTitle, toneOf } from '@/lib/workflowMeta';
import { useI18n } from '@/i18n/locale';

/**
 * 按事件把节点结果写进时间线。
 *
 * 节点记录要等运行整体结束才落库，运行中库里查不到它们；
 * 但事件本身带着输入、状态、耗时与输出，直接用它渲染，记录就会随运行逐步长出来。
 * 事件里的输入输出与落库字段同口径（输入保持变量结构，输出是序列化文本），
 * 所以这里能直接写进时间线，不用等收尾那次 load(true)。
 */
function markNode(
  nodes: WorkflowNodeRun[],
  event: {
    node_id: string;
    node_kind: string;
    status: NodeRunStatus | 'running';
    duration_ms: number;
    input?: Record<string, unknown>;
    error?: string;
    route?: string;
    output?: unknown;
  },
): WorkflowNodeRun[] {
  const index = nodes.findIndex((node) => node.node_id === event.node_id);
  // 输入照后端给的变量结构原样保留，时间线按变量逐行展示；
  // 事件没带输入时（老进程补发的事件）不写这个键，免得用空串盖掉库里已有的值
  const patch: Partial<WorkflowNodeRun> = {
    node_kind: event.node_kind as WorkflowNodeRun['node_kind'],
    status: event.status,
    duration_ms: event.duration_ms,
    error: event.error ?? '',
    route: event.route ?? '',
    // 输出与历史记录同口径：结构化结果序列化成 JSON 文本
    output:
      event.output === undefined || event.output === null
        ? ''
        : typeof event.output === 'string'
          ? event.output
          : JSON.stringify(event.output),
  };
  if (event.input !== undefined) patch.input = JSON.stringify(event.input);
  if (index >= 0) {
    const next = [...nodes];
    next[index] = { ...next[index], ...patch };
    return next;
  }
  const created: WorkflowNodeRun = {
    id: -1,
    node_id: event.node_id,
    node_kind: event.node_kind as WorkflowNodeRun['node_kind'],
    attempt: 1,
    status: event.status,
    input: '',
    output: '',
    error: '',
    duration_ms: event.duration_ms,
    route: '',
    created_at: new Date().toISOString(),
    // 事件带了的字段覆盖占位值
    ...patch,
  };
  return [...nodes, created];
}

/** 导航项 = 已执行记录 + 还没执行的定义节点（后者只展示，点不开详情）。 */
type RailItem =
  | { type: 'run'; run: WorkflowNodeRun; key: string }
  | { type: 'pending'; node: WorkflowNode; key: string };

const KIND_TONE: Record<'ok' | 'danger' | 'info', string> = {
  ok: 'bg-ok',
  danger: 'bg-danger',
  info: 'bg-info',
};

export default function WorkflowRunPage() {
  const { runUid } = useParams();
  const navigate = useNavigate();
  const { tr } = useI18n();
  const [run, setRun] = useState<WorkflowRunDetail | null>(null);
  const [workflowName, setWorkflowName] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ tone: ToastTone; text: string } | null>(null);
  const [runOpen, setRunOpen] = useState(false);
  const [gateInput, setGateInput] = useState('');
  // 正在跑的节点：明细里只存终态，运行中要单独记一份用于显示
  const [runningNodes, setRunningNodes] = useState<Set<string>>(new Set());
  // 中栏当前展示哪个节点；null 表示还没选，默认落在这条流程的「最新一步」
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // 右栏画布缩略图折叠：排查字段时把宽度让给中栏
  const [canvasOpen, setCanvasOpen] = useState(true);
  // 画布放大弹层：窄栏只放得下结构，看节点名和分支关系要整宽摊开
  const [canvasZoom, setCanvasZoom] = useState(false);

  const flash = useCallback((tone: ToastTone, text: string) => {
    setToast({ tone, text });
    window.setTimeout(() => setToast(null), 4000);
  }, []);

  const uid = runUid ?? '';

  const load = useCallback(
    async (silent = false) => {
      if (uid === '') return;
      if (silent) setRefreshing(true);
      else setLoading(true);
      try {
        const detail = await getRun(uid);
        setRun((current) => {
          // 运行中刷新不能丢掉事件带来的节点行：库里还没有这些节点（要等运行结束才落库），
          // 整份覆盖会让导航在中途回到空白。
          if (current !== null && current.status === 'running' && detail.nodes.length === 0) {
            return { ...detail, nodes: current.nodes };
          }
          return detail;
        });
        setError(null);
        try {
          const workflow = await getWorkflow(detail.workflow_id);
          setWorkflowName(workflow.name);
        } catch {
          // 工作流被删除后运行记录仍可回看：标题退回 #id，不阻断页面。
          setWorkflowName(`#${detail.workflow_id}`);
        }
      } catch (err) {
        setError(err instanceof ApiError ? err.message : (err as Error).message);
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [uid],
  );
  // 加载函数的引用必须稳定：它在别处被当作依赖，身份一变（例如 run 更新导致
  // 重渲染时被重建），订阅 effect 就会被拆掉重连，刚收到的事件行全丢。
  const loadRef = useRef(load);
  useEffect(() => {
    loadRef.current = load;
  }, [load]);

  useEffect(() => {
    void loadRef.current();
  }, []);

  // 运行还在跑就接上事件流：节点状态自己往前走，不用手动刷新。
  // 订阅端会先补发已发生的事件，所以跳转过来时已经跑过几步也不会缺。
  // 只依赖 uid：run 每次更新都是新对象，直接依赖 run 会让订阅反复重连，
  // 每次重连结束后又会再 load(true) 一遍，事件写进去的行跟着被覆盖。
  const streamUid = run?.status === 'running' ? uid : '';
  useEffect(() => {
    if (streamUid === '') return;
    const controller = new AbortController();
    void (async () => {
      try {
        await subscribeRunEvents(
          streamUid,
          (event) => {
            if (event.event === 'node_started') {
              setRunningNodes((current) => new Set(current).add(event.node_id));
              // 节点一开始就把这一行插进导航：运行中可以点进去看它读到的输入
              setRun((current) =>
                current === null
                  ? current
                  : {
                      ...current,
                      nodes: markNode(current.nodes, {
                        ...event,
                        status: 'running',
                        duration_ms: 0,
                      }),
                    },
              );
            } else if (event.event === 'node_finished') {
              setRunningNodes((current) => {
                const next = new Set(current);
                next.delete(event.node_id);
                return next;
              });
              setRun((current) =>
                current === null
                  ? current
                  : { ...current, nodes: markNode(current.nodes, event) },
              );
            }
          },
          controller.signal,
        );
        // 终止事件到达（或流正常关闭）：拉一次权威结果对齐耗时、输出与人工节点信息
        void loadRef.current(true);
      } catch {
        // 订阅中断（网络抖动、后端重启）时也要对齐一次，
        // 否则页面会停在「运行中」，只能靠用户手动刷新
        if (!controller.signal.aborted) void loadRef.current(true);
      }
    })();
    return () => controller.abort();
  }, [streamUid]);

  const gate = useMemo<WaitingGate | null>(() => {
    if (run === null || run.status !== 'waiting') return null;
    const parsed = parseJsonText(run.output);
    if (parsed === null || typeof parsed !== 'object') return null;
    const raw = parsed as Record<string, unknown>;
    if (typeof raw.gate !== 'string' || typeof raw.prompt !== 'string') return null;
    return { gate: raw.gate, kind: raw.kind === 'input' ? 'input' : 'approval', prompt: raw.prompt };
  }, [run]);

  const gateNode = useMemo<WorkflowNode | null>(() => {
    if (run === null || gate === null) return null;
    return run.definition.nodes.find((node) => node.id === gate.gate) ?? null;
  }, [gate, run]);

  /** 导航项 = 已执行记录 + 还没执行的定义节点（后者只展示，点不开详情）。 */
  const railItems = useMemo<RailItem[]>(() => {
    if (run === null) return [];
    const executed = run.nodes.map((node) => ({
      type: 'run' as const,
      run: node,
      key: `${node.node_id}#${node.attempt}`,
    }));
    const seen = new Set(executed.map((item) => item.run.node_id));
    const pending: RailItem[] = run.definition.nodes
      .filter((node) => !seen.has(node.id))
      .map((node) => ({ type: 'pending', node, key: `todo-${node.id}` }));
    return [...executed, ...pending];
  }, [run]);

  // 默认选中「当前这一步」：停在人工节点就选它，否则选最后一条执行记录。
  // 只在用户还没选过时生效（点过左栏 / 画布节点就不再抢），不写进依赖：
  // 写进去的话「已经选中」也会重跑一次 effect，白白多一轮渲染。
  const userPicked = useRef(false);
  const onGatePicked = useCallback((id: string) => {
    flushSync(() => setSelectedId(id));
  }, []);
  useEffect(() => {
    if (userPicked.current || selectedId !== null) return;
    if (run === null) return;
    if (gate !== null) {
      onGatePicked(gate.gate);
      return;
    }
    const last = run.nodes[run.nodes.length - 1];
    if (last !== undefined) onGatePicked(last.node_id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gate, run]);

  const selectedRun = useMemo<WorkflowNodeRun | null>(() => {
    if (run === null || selectedId === null) return null;
    // 循环节点会多次执行：展示最后一条，和「当前这一步」的直觉一致
    for (let i = run.nodes.length - 1; i >= 0; i -= 1) {
      if (run.nodes[i].node_id === selectedId) return run.nodes[i];
    }
    return null;
  }, [run, selectedId]);

  const selectedDef = useMemo<WorkflowNode | null>(() => {
    if (run === null || selectedId === null) return null;
    return run.definition.nodes.find((node) => node.id === selectedId) ?? null;
  }, [run, selectedId]);

  const decide = useCallback(
    async (decision: 'approve' | 'reject') => {
      if (run === null) return;
      if (decision === 'approve' && gate !== null && gate.kind === 'input' && gateInput.trim() === '') {
        flash('err', tr('请填写提交内容', 'Please fill in the value'));
        return;
      }
      setBusy(true);
      try {
        const values: Record<string, unknown> = {};
        if (decision === 'approve' && gate !== null && gate.kind === 'input' && gateNode !== null) {
          values[gateNode.output_key] = gateInput;
        }
        const updated = await resumeRun(run.run_uid, decision, values);
        setRun(updated);
        setGateInput('');
        flash('ok', decision === 'approve' ? tr('已确认，继续执行', 'Approved; execution resumed') : tr('已拒绝，运行终止', 'Rejected; run terminated'));
      } catch (err) {
        flash('err', err instanceof ApiError ? err.message : (err as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [flash, gate, gateInput, gateNode, run, tr],
  );

  const stop = useCallback(async () => {
    if (run === null) return;
    setBusy(true);
    try {
      await stopWorkflowRun(run.run_uid);
      // 停止只让后端收枪；最终状态与节点结果以库里为准，重新拉一次
      await load(true);
      flash('ok', tr('已停止运行', 'Run stopped'));
    } catch (err) {
      flash('err', err instanceof ApiError ? err.message : (err as Error).message);
    } finally {
      setBusy(false);
    }
  }, [flash, load, run, tr]);

  const copyId = useCallback(async () => {
    if (run === null) return;
    try {
      await navigator.clipboard.writeText(run.run_uid);
      flash('ok', tr('已复制 run_id', 'run_id copied'));
    } catch {
      flash('err', tr('剪贴板不可用', 'Clipboard unavailable'));
    }
  }, [flash, run, tr]);

  /**
   * 页面外壳：三种状态（加载中 / 找不到 / 正常）都必须返回同一种根元素。
   *
   * 之前加载态、空态返回 main、正常态返回 Fragment，React 在不同状态之间切换会
   * 把整棵子树卸载重建：跑完事件一到达，选中节点、滚动位置全部丢回去。
   */
  if (loading || error !== null || run === null) {
    return (
      <main className="anim-fade flex flex-1 flex-col overflow-hidden">
        {loading ? (
          <div className="flex flex-1 items-center justify-center text-[12px] text-zinc-500">{tr('加载运行记录…', 'Loading run…')}</div>
        ) : (
          <>
            <div className="flex items-center gap-2 border-b border-zinc-800/60 px-6 py-3">
              <Link to="/workflows?tab=runs" className="inline-flex items-center gap-1 text-[11px] text-zinc-500 hover:text-zinc-300">
                <ArrowLeft size={12} aria-hidden />
                {tr('返回运行记录', 'Back to runs')}
              </Link>
            </div>
            <div className="p-6">
              <EmptyStateCard
                icon={AlertTriangle}
                title={tr('找不到这次运行', 'Run not found')}
                hint={error ?? tr('可能已被删除', 'It may have been deleted')}
              />
            </div>
          </>
        )}
      </main>
    );
  }

  const title = workflowName === '' ? tr('运行记录', 'Run') : workflowName;
  const waiting = run.status === 'waiting';

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      {/* 顶部条：只放身份与操作。状态、耗时、触发都在下面的信息带里，不在这里重复 */}
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-zinc-800/60 px-5 py-2.5">
        <Link
          to={`/workflows/${run.workflow_id}`}
          className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-zinc-500 transition-colors hover:bg-zinc-500/10 hover:text-zinc-200"
          aria-label={tr('返回工作流', 'Back to workflow')}
          title={tr('返回工作流', 'Back to workflow')}
        >
          <ArrowLeft size={13} aria-hidden />
        </Link>
        <div className="min-w-0">
          <h1 className="truncate text-[14px] font-semibold leading-5 text-zinc-100">{title}</h1>
          <div className="flex flex-wrap items-center gap-x-2 text-[10.5px] text-zinc-500">
            <button type="button" onClick={() => void copyId()} className="font-mono hover:text-zinc-300" title={tr('点击复制', 'Click to copy')}>
              {run.run_uid}
            </button>
            <span className="text-zinc-700">·</span>
            <span>v{run.workflow_version}</span>
            {run.workflow_status === 'draft' && (
              <>
                <span className="text-zinc-700">·</span>
                <DraftTag />
              </>
            )}
          </div>
        </div>
        <div className="ml-auto flex shrink-0 items-center gap-1.5">
          <RowActionIcon icon={Copy} label={tr('复制 run_id', 'Copy run_id')} onClick={() => void copyId()} />
          <RowActionIcon
            icon={RefreshCw}
            label={tr('刷新', 'Refresh')}
            className={cn(refreshing && 'animate-spin')}
            disabled={refreshing}
            onClick={() => void load(true)}
          />
          {run.status === 'running' && (
            <RowAction icon={Square} tone="danger" disabled={busy} onClick={() => void stop()}>
              {tr('停止运行', 'Stop')}
            </RowAction>
          )}
          {/* 未发布的画布没有可运行的正式版本，不给「再次运行」 */}
          {run.has_published && (
            <Button variant="primary" size="sm" className="h-7 px-3 text-[11.5px]" onClick={() => setRunOpen(true)} disabled={waiting}>
              <Play size={12} aria-hidden />
              {tr('再次运行', 'Run again')}
            </Button>
          )}
        </div>
      </header>

      {/* 顶部信息带：四张摘要卡，只回答「现在什么情况」，明细点开再看 */}
      <RunOverviewCards run={run} runningCount={runningNodes.size} gate={gate} />

      {run.error !== '' && (
        <div className="flex items-center gap-2 border-b border-danger/25 bg-danger/[0.06] px-5 py-1.5 text-[11.5px] text-danger">
          <AlertTriangle size={12} aria-hidden />
          <span className="truncate">{run.error}</span>
        </div>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[210px_minmax(0,1fr)] xl:grid-cols-[210px_minmax(0,1fr)_minmax(300px,26%)]">
        {/* ── 左栏：节点导航 ───────────────────────────────── */}        <nav className="flex min-h-0 min-w-0 flex-col border-b border-zinc-800/60 lg:border-b-0 lg:border-r" aria-label={tr('节点列表', 'Nodes')}>
          <div className="flex items-center justify-between px-3 py-2 text-[10px] uppercase tracking-wide text-text-faint">
            <span>{tr('执行流程', 'Execution')}</span>
            <span className="tabular-nums normal-case">
              {run.nodes.length}
              {railItems.length > run.nodes.length && ` / ${railItems.length}`}
            </span>
          </div>
          <ol className="min-h-0 flex-1 overflow-y-auto pb-2">
            {railItems.map((item) => {
              const id = item.type === 'run' ? item.run.node_id : item.node.id;
              const state = railState(item, runningNodes.has(id), gate?.gate ?? null);
              return (
                <li key={item.key}>
                  <button
                    type="button"
                    disabled={item.type === 'pending'}
                    onClick={() => {
                      userPicked.current = true;
                      setSelectedId(id);
                    }}
                    aria-current={id === selectedId ? 'true' : undefined}
                    className={cn(
                      'group relative flex w-full items-center gap-1.5 py-1.5 pl-2.5 pr-2 text-left transition-colors',
                      id === selectedId ? 'bg-accent/12' : 'hover:bg-zinc-500/[0.06]',
                      item.type === 'pending' && 'cursor-default opacity-45',
                    )}
                  >
                    {id === selectedId && <span className="absolute inset-y-0 left-0 w-[2px] bg-accent" aria-hidden />}
                    <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', state.dot, state.pulse && 'animate-pulse-dot')} aria-hidden />
                    <span className="min-w-0 flex-1 truncate text-[11.5px] text-text-muted">
                      {labelOfNode(run, id) || id}
                    </span>
                    {state.showDuration && item.type === 'run' && (
                      <span className="shrink-0 font-mono text-[9.5px] tabular-nums text-text-faint">
                        {formatDuration(item.run.duration_ms)}
                      </span>
                    )}
                  </button>
                </li>
              );
            })}
            {railItems.length === 0 && (
              <li className="px-3 py-6 text-center text-[11px] text-text-faint">{tr('还没有节点执行记录', 'No node has run yet')}</li>
            )}
          </ol>
        </nav>

        {/* ── 中栏：选中节点的详情 ─────────────────────────── */}
        <section className="min-h-0 min-w-0 overflow-y-auto border-b border-zinc-800/60 lg:border-b-0">
          {selectedId === null ? (
            <div className="flex h-full items-center justify-center px-6 text-center text-[11.5px] text-text-faint">
              {tr('从左侧选一个节点查看输入输出', 'Pick a node on the left to inspect it')}
            </div>
          ) : (
            <NodeDetail
              nodeId={selectedId}
              record={selectedRun}
              definition={selectedDef}
              running={runningNodes.has(selectedId)}
              gate={gate !== null && gate.gate === selectedId ? gate : null}
              gateNode={gateNode}
              runError={run.error}
              busy={busy}
              gateInput={gateInput}
              onGateInput={setGateInput}
              onApprove={() => void decide('approve')}
              onReject={() => void decide('reject')}
              kindTitleText={kindTitle}
            />
          )}
        </section>

        {/* ── 右栏：画布 + 运行输入输出 ─────────────────────── */}
        <aside className="flex min-h-0 min-w-0 flex-col gap-3 overflow-y-auto border-t border-zinc-800/60 p-3 xl:border-l xl:border-t-0">
          <ContextCard
            title={tr('画布', 'Canvas')}
            action={
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setCanvasZoom(true)}
                  className="rounded px-1 text-text-faint hover:bg-zinc-500/10 hover:text-text-muted"
                  aria-label={tr('放大画布', 'Enlarge canvas')}
                  title={tr('放大画布', 'Enlarge canvas')}
                >
                  <Maximize2 size={11} aria-hidden />
                </button>
                <button
                  type="button"
                  onClick={() => setCanvasOpen((current) => !current)}
                  className="rounded px-1 text-text-faint hover:bg-zinc-500/10 hover:text-text-muted"
                  aria-expanded={canvasOpen}
                  aria-label={canvasOpen ? tr('收起画布', 'Hide canvas') : tr('展开画布', 'Show canvas')}
                >
                  {canvasOpen ? <ChevronDown size={11} aria-hidden /> : <ChevronRight size={11} aria-hidden />}
                </button>
              </div>
            }
          >
            {canvasOpen ? (
              <>
                <div className="h-28 px-1 py-1">
                  <WorkflowGraphThumb
                    graph={{
                      nodes: run.definition.nodes.map((node) => ({ id: node.id, kind: node.kind, label: node.label, x: node.x, y: node.y })),
                      edges: run.definition.edges,
                    }}
                    statuses={statusMap(run, runningNodes)}
                    gateId={gate === null ? null : gate.gate}
                    selectedId={selectedId}
                    onSelect={setSelectedId}
                  />
                </div>
                <div className="flex flex-wrap items-center gap-x-2.5 gap-y-0.5 border-t border-zinc-800/50 px-3 py-1 text-[9.5px] text-text-faint">
                  <LegendDot className="bg-ok" label={tr('成功', 'ok')} />
                  <LegendDot className="bg-danger" label={tr('失败', 'failed')} />
                  <LegendDot className="bg-info" label={tr('运行中', 'running')} />
                  <LegendDot className="bg-warn" label={tr('等待人工', 'waiting')} />
                  <LegendDot className="bg-zinc-600" label={tr('未执行', 'not run')} />
                </div>
              </>
            ) : (
              <div className="px-3 py-3 text-[10.5px] text-text-faint">{tr('画布已收起', 'Canvas hidden')}</div>
            )}
          </ContextCard>

          <ContextCard title={tr('运行输入', 'Run input')}>
            <div className="px-2.5 py-2">
              <RunInputRows input={parseJsonText(run.input)} />
            </div>
          </ContextCard>

          <ContextCard title={tr('运行输出', 'Run output')}>
            <div className="px-2.5 py-2">
              <OutputBody value={run.output} />
            </div>
          </ContextCard>
        </aside>
      </div>

      {/* 画布放大：看节点名与分支关系；点节点直接定位到中栏详情 */}
      {/* 弹层只在打开时挂载：常驻挂一个隐藏的画布会白跑一套 React Flow */}
      {canvasZoom && (
      <Modal open onClose={() => setCanvasZoom(false)} title={tr('画布', 'Canvas')} size="xl">
        <div className="h-[60vh]">
          <WorkflowGraphThumb
            graph={{
              nodes: run.definition.nodes.map((node) => ({ id: node.id, kind: node.kind, label: node.label, x: node.x, y: node.y })),
              edges: run.definition.edges,
            }}
            statuses={statusMap(run, runningNodes)}
            gateId={gate === null ? null : gate.gate}
            selectedId={selectedId}
            onSelect={(nodeId) => {
              userPicked.current = true;
              setSelectedId(nodeId);
              setCanvasZoom(false);
            }}
          />
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-text-faint">
          <LegendDot className="bg-ok" label={tr('成功', 'ok')} />
          <LegendDot className="bg-danger" label={tr('失败', 'failed')} />
          <LegendDot className="bg-info" label={tr('运行中', 'running')} />
          <LegendDot className="bg-warn" label={tr('等待人工', 'waiting')} />
          <LegendDot className="bg-zinc-600" label={tr('未执行', 'not run')} />
          <span className="ml-auto">{tr('点节点定位到中栏详情', 'Click a node to inspect it')}</span>
        </div>
      </Modal>
      )}

      {runOpen && (
        <RunWorkflowDialog
          workflowId={run.workflow_id}
          workflowName={workflowName}
          definition={run.definition}
          onClose={() => setRunOpen(false)}
          onStarted={(values) => {
            // 弹窗只收集输入；再次运行的请求与跳转在这里补上
            if (run === null) return;
            setRunOpen(false);
            void (async () => {
              try {
                // 只发起不等执行，立刻跳到新一次运行的详情
                const next = await startWorkflowRun(run.workflow_id, values, 'published');
                navigate(`/workflows/runs/${next.run_uid}`);
              } catch (err) {
                flash('err', err instanceof ApiError ? err.message : (err as Error).message);
              }
            })();
          }}
        />
      )}

      {toast !== null && <Toast tone={toast.tone} text={toast.text} />}
    </main>
  );
}

/** 触发方式的中文口径：manual 是正式运行，debug 是画布上的单节点试跑。 */
function triggerText(trigger: string, tr: (zh: string, en: string) => string): string {
  if (trigger === 'manual') return tr('手动', 'Manual');
  if (trigger === 'debug') return tr('节点试跑', 'Node test');
  return trigger;
}

/** 从定义里找某个 node_id 对应的节点（导航上要拿它的 label / kind）。 */
function selectedDefOf(run: WorkflowRunDetail, nodeId: string): WorkflowNode | null {
  return run.definition.nodes.find((node) => node.id === nodeId) ?? null;
}

function labelOfNode(run: WorkflowRunDetail, nodeId: string): string {
  const node = selectedDefOf(run, nodeId);
  const label = node?.label?.trim() ?? '';
  return label !== '' ? label : nodeId;
}

/** node_id → 执行结果，给画布缩略图染色；运行中的节点靠事件补。 */
function statusMap(run: WorkflowRunDetail, running: Set<string>): Record<string, NodeRunStatus | 'running'> {
  const map: Record<string, NodeRunStatus | 'running'> = {};
  for (const node of run.nodes) map[node.node_id] = node.status;
  for (const nodeId of running) map[nodeId] = 'running';
  return map;
}

/** 导航左侧圆点与时长：一次算清，按钮里只做展示。 */
function railState(item: RailItem, isRunning: boolean, gateId: string | null): {
  dot: string;
  pulse: boolean;
  showDuration: boolean;
} {
  if (item.type === 'pending') return { dot: 'bg-zinc-600', pulse: false, showDuration: false };
  const status = item.run.status;
  if (gateId !== null && item.run.node_id === gateId) {
    return { dot: 'bg-warn', pulse: true, showDuration: false };
  }
  if (isRunning || status === 'running') return { dot: KIND_TONE.info, pulse: true, showDuration: false };
  if (status === 'failed') return { dot: KIND_TONE.danger, pulse: false, showDuration: true };
  return { dot: KIND_TONE.ok, pulse: false, showDuration: true };
}

/** 运行级状态在信息带里的文案与配色；口径与 RunStatusPill 一致。 */
const RUN_STATUS_TEXT: Record<RunStatus, { zh: string; en: string; tone: 'ok' | 'warn' | 'danger' | 'info' | 'neutral' }> = {
  running: { zh: '运行中', en: 'Running', tone: 'info' },
  waiting: { zh: '等待人工', en: 'Waiting', tone: 'warn' },
  succeeded: { zh: '成功', en: 'Succeeded', tone: 'ok' },
  failed: { zh: '失败', en: 'Failed', tone: 'danger' },
};

/**
 * 顶部信息带：四张摘要卡一行排开。
 *
 * 只放「现在什么情况」的结论数字：状态、耗时、进度、触发，不做展开。
 */
function RunOverviewCards({
  run,
  runningCount,
  gate,
}: {
  run: WorkflowRunDetail;
  runningCount: number;
  gate: WaitingGate | null;
}) {
  const { tr } = useI18n();
  const total = run.definition.nodes.length;
  // 运行中/排队的节点在明细里还没有记录，成功数只数真正落地的终态
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
  const status = RUN_STATUS_TEXT[run.status];

  return (
    <section className="grid grid-cols-2 gap-2.5 border-b border-zinc-800/60 px-4 py-3 lg:grid-cols-4" aria-label={tr('运行概况', 'Run overview')}>
      {/* 状态卡：唯一用强调色描边的卡，一眼先看到「现在卡在哪」 */}
      <div
        className={cn(
          'flex min-w-0 flex-col gap-0.5 rounded-xl border px-3 py-2',
          status.tone === 'warn' && 'border-warn/35 bg-warn/[0.07]',
          status.tone === 'danger' && 'border-danger/35 bg-danger/[0.06]',
          (status.tone === 'ok' || status.tone === 'info' || status.tone === 'neutral') &&
            'border-zinc-800/60 bg-zinc-900/40',
        )}
      >
        <div className="flex items-center justify-between gap-2 text-[10px] text-text-faint">
          <span>{tr('当前状态', 'Status')}</span>
          <StatusPill tone={status.tone} label={tr(status.zh, status.en)} pulse={status.tone === 'info' || status.tone === 'warn'} />
        </div>
        <div className="truncate text-[15px] font-semibold leading-6 text-text">
          {gate !== null
            ? tr(gate.kind === 'input' ? '等待人工填写' : '等待人工审批', gate.kind === 'input' ? 'Waiting for input' : 'Waiting for approval')
            : tr(status.zh, status.en)}
        </div>
        <div className="truncate text-[10px] leading-4 text-text-faint">
          {gate !== null
            ? tr(`停在「${labelOfNode(run, gate.gate)}」，处理后继续`, `Paused at “${labelOfNode(run, gate.gate)}”`)
            : run.finished_at === null
              ? tr('尚未结束', 'Not finished yet')
              : tr(`结束于 ${clockText(run.finished_at)}`, `Finished ${clockText(run.finished_at)}`)}
        </div>
      </div>

      {/* 耗时卡 */}
      <div className="flex min-w-0 flex-col gap-0.5 rounded-xl border border-zinc-800/60 bg-zinc-900/40 px-3 py-2">
        <div className="text-[10px] text-text-faint">{tr('耗时', 'Duration')}</div>
        <div className="font-mono text-[17px] font-semibold leading-6 tabular-nums text-text">
          {formatDuration(run.duration_ms)}
        </div>
        <div className="truncate text-[10px] leading-4 text-text-faint" title={fullDateTime(run.created_at)}>
          {clockText(run.created_at)} {tr('起算', 'start')}
        </div>
      </div>

      {/* 进度卡：比例条 + 计数 */}
      <div className="flex min-w-0 flex-col gap-0.5 rounded-xl border border-zinc-800/60 bg-zinc-900/40 px-3 py-2">
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
        <div className="mt-1 flex h-1.5 overflow-hidden rounded-full bg-zinc-800" aria-hidden>
          <span className="bg-ok" style={{ width: `${pct(ok)}%` }} />
          <span className="bg-info" style={{ width: `${pct(running)}%` }} />
          <span className="bg-warn" style={{ width: `${pct(waitingCount)}%` }} />
        </div>
      </div>

      {/* 触发卡 */}
      <div className="flex min-w-0 flex-col gap-0.5 rounded-xl border border-zinc-800/60 bg-zinc-900/40 px-3 py-2">
        <div className="text-[10px] text-text-faint">{tr('触发方式', 'Trigger')}</div>
        <div className="truncate text-[15px] font-semibold leading-6 text-text">{triggerText(run.trigger, tr)}</div>
        <div className="truncate text-[10px] leading-4 text-text-faint">{run.triggered_by || '—'}</div>
      </div>
    </section>
  );
}

function ContextCard({
  title,
  action,
  children,
}: {
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-lg border border-zinc-800/60 bg-zinc-900/30">
      <header className="flex items-center justify-between gap-2 border-b border-zinc-800/50 px-3 py-1.5">
        <h2 className="text-[11px] font-medium text-text-muted">{title}</h2>
        {action}
      </header>
      {children}
    </section>
  );
}

function LegendDot({ className, label }: { className: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className={cn('h-1.5 w-1.5 rounded-full', className)} aria-hidden />
      {label}
    </span>
  );
}

function OutputBody({ value }: { value: string }) {
  const { tr } = useI18n();
  const parsed = parseJsonText(value);
  if (parsed === null) return <div className="px-0.5 text-[11px] text-text-faint">{tr('空', 'Empty')}</div>;
  return (
    <CodeBlock maxHeightClassName="max-h-40">
      {typeof parsed === 'string' ? parsed : JSON.stringify(parsed, null, 2)}
    </CodeBlock>
  );
}

/**
 * 中栏：选中节点的详情。
 *
 * 从上到下：人工确认（有就最优先，用户一眼看到要做什么）→ 输入 → 输出 → 错误。
 * 后续节点不在这里列：左栏已经把整条流程按顺序摆好了。
 */
function NodeDetail({
  nodeId,
  record,
  definition,
  running,
  gate,
  gateNode,
  runError,
  busy,
  gateInput,
  onGateInput,
  onApprove,
  onReject,
  kindTitleText,
}: {
  nodeId: string;
  record: WorkflowNodeRun | null;
  definition: WorkflowNode | null;
  running: boolean;
  gate: WaitingGate | null;
  gateNode: WorkflowNode | null;
  /** 运行级错误：节点没有自己的错误时，它就是这次失败的真正原因 */
  runError: string;
  busy: boolean;
  gateInput: string;
  onGateInput(next: string): void;
  onApprove(): void;
  onReject(): void;
  kindTitleText(kind: NodeKind, catalog: null, zh: boolean): string;
}) {
  const { tr, locale } = useI18n();
  const kind: NodeKind = definition?.kind ?? record?.node_kind ?? 'agent';
  const Icon = iconOf(kind);
  const tone = toneOf(kind);
  const label = definition?.label?.trim() ?? '';
  const title = label !== '' ? label : nodeId;
  const parsedInput = parseJsonText(record?.input ?? '');
  const parsedOutput = parseJsonText(record?.output ?? '');
  const outputIsString = typeof parsedOutput === 'string';
  const outputText =
    parsedOutput === null ? '' : outputIsString ? parsedOutput : JSON.stringify(parsedOutput, null, 2);
  const inputCount =
    parsedInput !== null && typeof parsedInput === 'object' && !Array.isArray(parsedInput)
      ? Object.keys(parsedInput).length
      : null;
  const status = detailStatus(record, running, gate, tr);
  const outputFormat = outputIsString ? tr('文本', 'text') : tr('JSON', 'JSON');

  return (
    <div className="flex min-h-full flex-col">
      {/* 节点头：图标 + 名字 + node_id 标记 + 类型 + 状态，固定在滚动区顶部 */}
      <header className="sticky top-0 z-10 flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-zinc-800/60 bg-bg/95 px-4 py-2.5 backdrop-blur">
        <span className={cn('flex h-6 w-6 shrink-0 items-center justify-center rounded-md', tone.iconBox)}>
          <Icon size={12} className={tone.icon} aria-hidden />
        </span>
        <h2 className="truncate text-[13px] font-medium text-text">{title}</h2>
        <span className="shrink-0 rounded border border-border/70 px-1 font-mono text-[10px] leading-4 text-text-faint">{nodeId}</span>
        <span className="text-[10px] text-text-faint">{kindTitleText(kind, null, locale === 'zh-CN')}</span>
        {record !== null && record.attempt > 1 && (
          <span className="rounded bg-indigo-500/10 px-1 text-[10px] leading-4 text-indigo-400">{tr(`第 ${record.attempt} 轮`, `attempt ${record.attempt}`)}</span>
        )}
        {record !== null && record.route !== '' && (
          <span className="rounded border border-border/70 bg-zinc-500/10 px-1 font-mono text-[10px] leading-4 text-text-muted">{record.route}</span>
        )}
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          <StatusPill tone={status.tone} pulse={status.pulse} label={status.label} />
          {record !== null && record.status !== 'running' && (
            <span className="font-mono text-[10.5px] tabular-nums text-text-faint">{formatDuration(record.duration_ms)}</span>
          )}
        </span>
      </header>

      <div className="flex-1 space-y-3 px-4 py-3">
        {record === null && gate === null ? (
          <div className="rounded-lg border border-dashed border-border/70 px-4 py-8 text-center text-[11.5px] text-text-faint">
            {tr('这一步还没有执行', 'This step has not run yet')}
          </div>
        ) : (
          <>
            {gate !== null && (
              <GateInline
                gate={gate}
                gateNode={gateNode}
                busy={busy}
                value={gateInput}
                onChange={onGateInput}
                onApprove={onApprove}
                onReject={onReject}
              />
            )}

            <DetailBlock
              title={tr('输入', 'Input')}
              extra={inputCount === null ? undefined : tr(`${inputCount} 个变量`, `${inputCount} vars`)}
            >
              <RunInputRows input={parsedInput} />
            </DetailBlock>

            <DetailBlock
              title={tr('输出', 'Output')}
              extra={outputText === '' ? undefined : `${outputFormat} · ${tr(`${outputText.length} 字符`, `${outputText.length} chars`)}`}
              copyText={outputText}
            >
              {outputText === '' ? (
                <div className="rounded-lg border border-dashed border-border/70 px-3 py-3 text-[11px] text-text-faint">
                  {record !== null && record.status === 'running' ? tr('这一步还在执行，输出跑完即到…', 'Still running; output arrives when done…') : tr('（无输出）', '(no output)')}
                </div>
              ) : (
                <div className="overflow-hidden rounded-lg border border-border/60 bg-zinc-950">
                  <div className="flex items-center gap-1.5 border-b border-border/50 bg-zinc-500/[0.06] px-2.5 py-1 font-mono text-[9.5px] text-text-faint">
                    <span className="h-1.5 w-1.5 rounded-full bg-ok/70" aria-hidden />
                    {nodeId}.result
                  </div>
                  <CodeBlock maxHeightClassName="max-h-96" className="rounded-none border-0">
                    {outputText}
                  </CodeBlock>
                </div>
              )}
            </DetailBlock>

            {record !== null && record.error !== '' && (
              <DetailBlock title={tr('错误', 'Error')}>
                <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 font-mono text-[11px] leading-5 text-danger">
                  {record.error}
                </pre>
              </DetailBlock>
            )}

            {/* 节点自己没报错但整条运行失败了：把运行级错误挂在这里，否则这一页看不出为什么红 */}
            {runError !== '' && (record === null || record.error === '') && (
              <DetailBlock title={tr('运行错误', 'Run error')}>
                <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 font-mono text-[11px] leading-5 text-danger">
                  {runError}
                </pre>
              </DetailBlock>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/** 中栏分节标题：左边小标题，右边是数量/长度胶囊，可选复制按钮。 */
function DetailBlock({
  title,
  extra,
  copyText,
  children,
}: {
  title: string;
  extra?: string;
  copyText?: string;
  children: React.ReactNode;
}) {
  const { tr } = useI18n();
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    if (copyText === undefined) return;
    try {
      await navigator.clipboard.writeText(copyText);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // 剪贴板不可用时保持原样，用户可以直接选中文本
    }
  };

  return (
    <section>
      <div className="mb-1.5 flex items-center gap-1.5">
        <h3 className="text-[10.5px] font-medium uppercase tracking-wide text-text-faint">{title}</h3>
        {extra !== undefined && (
          <span className="rounded-full border border-border/60 bg-zinc-500/[0.06] px-1.5 font-mono text-[9.5px] leading-4 text-text-faint">
            {extra}
          </span>
        )}
        {copyText !== undefined && copyText !== '' && (
          <button
            type="button"
            onClick={() => void copy()}
            className="ml-auto rounded px-1.5 text-[9.5px] leading-4 text-text-faint transition-colors hover:bg-zinc-500/10 hover:text-text-muted"
          >
            {copied ? tr('已复制', 'Copied') : tr('复制', 'Copy')}
          </button>
        )}
      </div>
      {children}
    </section>
  );
}

/** 人工确认：跟节点绑死，不做成浮在页面顶部的独立卡片。 */
function GateInline({
  gate,
  gateNode,
  busy,
  value,
  onChange,
  onApprove,
  onReject,
}: {
  gate: WaitingGate;
  gateNode: WorkflowNode | null;
  busy: boolean;
  value: string;
  onChange(next: string): void;
  onApprove(): void;
  onReject(): void;
}) {
  const { tr } = useI18n();
  const isInput = gate.kind === 'input';
  return (
    <section className="rounded-lg border border-warn/35 bg-warn/[0.07] px-3 py-2.5">
      <div className="mb-1 flex items-center gap-1.5 text-[11.5px] font-medium text-warn">
        <ShieldCheck size={12} aria-hidden />
        {isInput ? tr('等待人工输入', 'Waiting for user input') : tr('等待人工审批', 'Waiting for approval')}
      </div>
      <p className="mb-2 whitespace-pre-wrap break-words text-[11.5px] leading-5 text-text">
        {gate.prompt || tr('（没有说明文案）', '(no prompt)')}
      </p>
      <div className="flex flex-wrap items-center gap-1.5">
        {isInput && (
          <>
            <span className="text-[10.5px] text-text-faint">
              {tr('提交字段：', 'Field: ')}
              <span className="font-mono">{gateNode?.output_key ?? '—'}</span>
            </span>
            <Input
              value={value}
              onChange={(event) => onChange(event.target.value)}
              placeholder={tr('填写后提交', 'Value to submit')}
              aria-label={tr('人工输入内容', 'User input value')}
              className="h-7 max-w-[16rem] text-[11.5px]"
              disabled={busy}
            />
          </>
        )}
        <Button variant="primary" size="sm" className="h-7 px-2.5 text-[11.5px]" onClick={onApprove} disabled={busy}>
          <Check size={12} aria-hidden />
          {busy ? tr('执行中…', 'Working…') : isInput ? tr('提交并继续', 'Submit & continue') : tr('批准并继续', 'Approve & continue')}
        </Button>
        <Button variant="dangerGhost" size="sm" className="h-7 px-2.5 text-[11.5px]" onClick={onReject} disabled={busy}>
          <X size={12} aria-hidden />
          {tr('拒绝', 'Reject')}
        </Button>
        <span className="text-[10px] text-text-faint">{tr('拒绝会把这次运行标记为失败', 'Rejecting marks the run as failed')}</span>
      </div>
    </section>
  );
}

/** 中栏节点状态口径：待人工优先，其次运行中 / 失败 / 成功 / 未执行。 */
function detailStatus(
  record: WorkflowNodeRun | null,
  running: boolean,
  gate: WaitingGate | null,
  tr: (zh: string, en: string) => string,
): { tone: 'ok' | 'danger' | 'info' | 'warn' | 'neutral'; label: string; pulse: boolean } {
  if (gate !== null) {
    return {
      tone: 'warn',
      label: gate.kind === 'input' ? tr('待填写', 'Waiting input') : tr('待审批', 'Waiting approval'),
      pulse: true,
    };
  }
  if (running || record?.status === 'running') return { tone: 'info', label: tr('运行中', 'Running'), pulse: true };
  if (record === null) return { tone: 'neutral', label: tr('未执行', 'Not run'), pulse: false };
  if (record.status === 'failed') return { tone: 'danger', label: tr('失败', 'Failed'), pulse: false };
  return { tone: 'ok', label: tr('成功', 'Succeeded'), pulse: false };
}

/** 只给时:分:秒，完整日期放在 title 里。 */
function clockText(iso: string): string {
  const full = fullDateTime(iso);
  return full.split(' ').slice(-1)[0] ?? full;
}
