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

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowLeft,
  Copy,
  Play,
  RefreshCw,
  Square,
} from 'lucide-react';
import { ApiError } from '@/api/client';
import {
  getRun,
  parseJsonText,
  resumeRun,
  startWorkflowRun,
  stopWorkflowRun,
  subscribeRunEvents,
  type NodeKind,
  type NodeRunStatus,
  type WaitingGate,
  type WorkflowNode,
  type WorkflowNodeRun,
  type WorkflowRunDetail,
} from '@/api/workflows';
import { RunWorkflowDialog } from '@/components/workflow/RunWorkflowDialog';
import { RunContextPanel } from '@/components/workflow/RunContextPanel';
import { RunNodeDetail } from '@/components/workflow/RunNodeDetail';
import { buildRailItems, RunNodeRail, type RailItem } from '@/components/workflow/RunNodeRail';
import { RunOverviewCards } from '@/components/workflow/RunOverviewCards';
import { DraftTag } from '@/components/workflow/RunStatusPill';
import { Button, EmptyStateCard, RowAction, RowActionIcon, Toast, type ToastTone } from '@/components/ui';
import { cn } from '@/lib/cn';
import { useI18n } from '@/i18n/locale';

const TOAST_DURATION_MS = 4000;

/**
 * 按事件把节点结果写进时间线。
 *
 * 节点记录要等运行整体结束才落库，运行中库里查不到它们；
 * 但事件本身带着输入、状态、耗时与输出，直接用它渲染，记录就会随运行逐步长出来。
 */
function markNode(
  nodes: WorkflowNodeRun[],
  event: {
    node_id: string;
    node_kind: NodeKind;
    status: NodeRunStatus | 'running';
    duration_ms: number;
    attempt?: number;
    input?: Record<string, unknown>;
    error?: string;
    route?: string;
    output?: unknown;
  },
): WorkflowNodeRun[] {
  // 输入照后端给的变量结构原样保留，时间线按变量逐行展示
  const patch: Partial<WorkflowNodeRun> = {
    node_kind: event.node_kind,
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

  // 循环节点会多次执行：按节点 id 与轮次定位，避免把多轮压成一行
  const attempt = event.attempt ?? 1;
  const index = nodes.findIndex(
    (node) => node.node_id === event.node_id && node.attempt === attempt,
  );
  if (index >= 0) {
    const next = [...nodes];
    next[index] = { ...next[index], ...patch };
    return next;
  }
  const created: WorkflowNodeRun = {
    id: -1,
    node_id: event.node_id,
    node_kind: event.node_kind,
    attempt,
    status: event.status,
    input: '',
    output: '',
    error: '',
    duration_ms: event.duration_ms,
    route: '',
    created_at: new Date().toISOString(),
    ...patch,
  };
  return [...nodes, created];
}

/** node_id → 画布里的显示名。 */
function labelOfNode(run: WorkflowRunDetail, nodeId: string): string {
  const node = run.definition.nodes.find((item) => item.id === nodeId);
  const label = node?.label?.trim() ?? '';
  return label !== '' ? label : nodeId;
}

/** node_id → 执行结果，给画布缩略图染色；运行中的节点靠事件补。 */
function buildStatuses(
  run: WorkflowRunDetail,
  running: Set<string>,
): Record<string, NodeRunStatus | 'running'> {
  const map: Record<string, NodeRunStatus | 'running'> = {};
  for (const node of run.nodes) map[node.node_id] = node.status;
  for (const nodeId of running) map[nodeId] = 'running';
  return map;
}

export default function WorkflowRunPage() {
  const { runUid } = useParams();
  const navigate = useNavigate();
  const { tr } = useI18n();
  const [run, setRun] = useState<WorkflowRunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ tone: ToastTone; text: string } | null>(null);
  const [runOpen, setRunOpen] = useState(false);
  const [gateInput, setGateInput] = useState('');
  // 正在跑的节点：明细里只存终态，运行中要单独记一份用于显示
  const [runningNodes, setRunningNodes] = useState<Set<string>>(new Set());
  // 用户明确选中的节点；没选过时跟随「当前这一步」
  const [pickedId, setPickedId] = useState<string | null>(null);

  const uid = runUid ?? '';
  // toast 定时器要在组件卸载时清掉，否则会对已卸载的组件 setState
  const toastTimer = useRef<number | null>(null);
  const flash = useCallback((tone: ToastTone, text: string) => {
    setToast({ tone, text });
    if (toastTimer.current !== null) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), TOAST_DURATION_MS);
  }, []);
  useEffect(
    () => () => {
      if (toastTimer.current !== null) window.clearTimeout(toastTimer.current);
    },
    [],
  );

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

  // uid 变化（点「再次运行」跳到新的一次运行）必须重新加载：
  // 路由复用同一个组件实例，只依赖 [] 会让页面停留在上一次运行的数据上。
  useEffect(() => {
    setPickedId(null);
    setRunningNodes(new Set());
    void loadRef.current();
  }, [uid]);

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
                      nodes: markNode(current.nodes, { ...event, status: 'running', duration_ms: 0 }),
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
            } else if (event.event === 'run_waiting' || event.event === 'run_finished') {
              // 收尾：拉一次权威结果对齐耗时、输出与人工节点信息
              void loadRef.current(true);
            }
          },
          controller.signal,
        );
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

  const railItems = useMemo<RailItem[]>(
    () => (run === null ? [] : buildRailItems(run.nodes, run.definition.nodes)),
    [run],
  );

  // 当前展示的节点：用户选过就用用户选的，没选就停在人工节点，
  // 否则落到最后一条执行记录。用派生状态而不是 effect + setState，
  // 避免多一轮渲染导致「门」这种关键内容晚一拍才出现。
  const selectedId = useMemo<string | null>(() => {
    if (pickedId !== null) return pickedId;
    if (run === null) return null;
    if (gate !== null) return gate.gate;
    const last = run.nodes[run.nodes.length - 1];
    return last?.node_id ?? null;
  }, [gate, pickedId, run]);

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
        flash(
          'ok',
          decision === 'approve'
            ? tr('已确认，继续执行', 'Approved; execution resumed')
            : tr('已拒绝，运行终止', 'Rejected; run terminated'),
        );
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
      const result = await stopWorkflowRun(run.run_uid);
      if (!result.stopped) {
        flash('err', tr('这次运行已经结束，无法停止', 'This run has already finished'));
        return;
      }
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

  const selectNode = useCallback((nodeId: string) => {
    setPickedId(nodeId);
  }, []);

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
          <div className="flex flex-1 items-center justify-center text-[12px] text-zinc-500">
            {tr('加载运行记录…', 'Loading run…')}
          </div>
        ) : (
          <>
            <div className="flex items-center gap-2 border-b border-zinc-800/60 px-6 py-3">
              <Link
                to="/workflows?tab=runs"
                className="inline-flex items-center gap-1 text-[11px] text-zinc-500 hover:text-zinc-300"
              >
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

  const title = run.workflow_name === '' ? tr('运行记录', 'Run') : run.workflow_name;
  const waiting = run.status === 'waiting';

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      {/* 顶部条：只放身份与操作。状态、耗时、触发都在下面的信息带里，不在这里重复 */}
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-zinc-800/60 px-5 py-2.5">
        {/* 返回运行记录列表：这里是分页列表的上级，不该跳去工作流画布 */}
        <Link
          to="/workflows?tab=runs"
          className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-zinc-500 transition-colors hover:bg-zinc-500/10 hover:text-zinc-200"
          aria-label={tr('返回运行记录', 'Back to runs')}
          title={tr('返回运行记录', 'Back to runs')}
        >
          <ArrowLeft size={13} aria-hidden />
        </Link>
        <div className="min-w-0">
          {/* 标题只是这次运行的归属工作流，不做进编辑器的入口：
              要改画布请从工作流列表进去，那边才是编辑路径。 */}
          <h1 className="truncate text-[14px] font-semibold leading-5 text-zinc-100">{title}</h1>
          <div className="flex flex-wrap items-center gap-x-2 text-[10.5px] text-zinc-500">
            <button
              type="button"
              onClick={() => void copyId()}
              className="font-mono hover:text-zinc-300"
              title={tr('点击复制', 'Click to copy')}
            >
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
            <Button
              variant="primary"
              size="sm"
              className="h-7 px-3 text-[11.5px]"
              onClick={() => setRunOpen(true)}
              disabled={waiting}
            >
              <Play size={12} aria-hidden />
              {tr('再次运行', 'Run again')}
            </Button>
          )}
        </div>
      </header>

      {/* 顶部信息带：四张摘要卡，只回答「现在什么情况」，明细点开再看 */}
      <RunOverviewCards
        run={run}
        runningCount={runningNodes.size}
        gate={gate}
        gateLabel={gate === null ? '' : labelOfNode(run, gate.gate)}
      />

      {run.error !== '' && (
        <div className="flex items-center gap-2 border-b border-danger/25 bg-danger/[0.06] px-5 py-1.5 text-[11.5px] text-danger">
          <AlertTriangle size={12} aria-hidden />
          <span className="truncate">{run.error}</span>
        </div>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[210px_minmax(0,1fr)] xl:grid-cols-[210px_minmax(0,1fr)_minmax(300px,26%)]">
        <RunNodeRail
          items={railItems}
          labelOf={(nodeId) => labelOfNode(run, nodeId)}
          selectedId={selectedId}
          gateId={gate?.gate ?? null}
          runningIds={runningNodes}
          onSelect={selectNode}
        />

        {/* 中栏：选中节点的详情 */}
        <section className="min-h-0 min-w-0 overflow-y-auto border-b border-zinc-800/60 lg:border-b-0">
          {selectedId === null ? (
            <div className="flex h-full items-center justify-center px-6 text-center text-[11.5px] text-text-faint">
              {tr('从左侧选一个节点查看输入输出', 'Pick a node on the left to inspect it')}
            </div>
          ) : (
            <RunNodeDetail
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
            />
          )}
        </section>

        {/* 右栏：画布 + 运行输入输出 */}
        <RunContextPanel
          run={run}
          statuses={buildStatuses(run, runningNodes)}
          gate={gate}
          selectedId={selectedId}
          onSelect={selectNode}
        />
      </div>

      {runOpen && (
        <RunWorkflowDialog
          workflowId={run.workflow_id}
          workflowName={run.workflow_name}
          definition={run.definition}
          onClose={() => setRunOpen(false)}
          onStarted={(values) => {
            // 弹窗只收集输入；再次运行的请求与跳转在这里补上
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
