// 运行详情：一次运行的完整诊断视图。
//
// 左栏是按执行顺序排列的节点时间线（输出/错误可展开），右栏是画布快照（节点按结果染色）、
// 运行输入与输出。status=waiting 时顶部出现人工确认卡片，确认后原地刷新。

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Play,
  RefreshCw,
  ShieldCheck,
  X,
} from 'lucide-react';
import { ApiError } from '@/api/client';
import {
  getRun,
  getWorkflow,
  parseJsonText,
  resumeRun,
  type NodeRunStatus,
  type WaitingGate,
  type WorkflowNode,
  type WorkflowNodeRun,
  type WorkflowRunDetail,
} from '@/api/workflows';
import { RunWorkflowDialog } from '@/components/workflow/RunWorkflowDialog';
import { RunStatusPill, formatDuration } from '@/components/workflow/RunStatusPill';
import { WorkflowGraphThumb } from '@/components/workflow/WorkflowGraphThumb';
import {
  Button,
  CodeBlock,
  DetailStat,
  DetailStatGrid,
  EmptyStateCard,
  Input,
  PageHeader,
  RowAction,
  StatusPill,
  Toast,
  type ToastTone,
} from '@/components/ui';
import { cn } from '@/lib/cn';
import { fullDateTime } from '@/lib/format';
import { iconOf, kindTitle, toneOf } from '@/lib/workflowMeta';
import { useI18n } from '@/i18n/locale';

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
        setRun(detail);
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

  useEffect(() => {
    void load();
  }, [load]);

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

  const statuses = useMemo(() => {
    const map: Record<string, NodeRunStatus> = {};
    for (const node of run?.nodes ?? []) {
      // 循环节点会有多条记录：任一失败就算失败，否则成功。
      if (map[node.node_id] === 'failed') continue;
      map[node.node_id] = node.status;
    }
    return map;
  }, [run?.nodes]);

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

  const copyId = useCallback(async () => {
    if (run === null) return;
    try {
      await navigator.clipboard.writeText(run.run_uid);
      flash('ok', tr('已复制 run_id', 'run_id copied'));
    } catch {
      flash('err', tr('剪贴板不可用', 'Clipboard unavailable'));
    }
  }, [flash, run, tr]);

  if (loading) {
    return <main className="flex flex-1 items-center justify-center text-[12px] text-zinc-500">{tr('加载运行记录…', 'Loading run…')}</main>;
  }

  if (error !== null || run === null) {
    return (
      <main className="flex flex-1 flex-col overflow-hidden">
        <PageHeader
          title={tr('运行记录', 'Run')}
          leading={
            <Link to="/workflows?tab=runs" className="inline-flex items-center gap-1 text-[11px] text-zinc-500 hover:text-zinc-300">
              <ArrowLeft size={12} aria-hidden />
              {tr('返回运行记录', 'Back to runs')}
            </Link>
          }
        />
        <div className="p-6">
          <EmptyStateCard
            icon={AlertTriangle}
            title={tr('找不到这次运行', 'Run not found')}
            hint={error ?? tr('可能已被删除', 'It may have been deleted')}
          />
        </div>
      </main>
    );
  }

  const executed = run.nodes.length;

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={workflowName === '' ? tr('运行记录', 'Run') : workflowName}
        subtitle={
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="font-mono text-[10.5px] text-zinc-500">{run.run_uid}</span>
            <span className="text-zinc-700">·</span>
            <span>v{run.workflow_version}</span>
            <span className="text-zinc-700">·</span>
            <span className="truncate">{run.triggered_by || '—'}</span>
            <span className="text-zinc-700">·</span>
            <span className="tabular-nums">{fullDateTime(run.created_at)}</span>
          </span>
        }
        leading={
          <Link to={`/workflows/${run.workflow_id}`} className="inline-flex items-center gap-1 text-[11px] text-zinc-500 hover:text-zinc-300">
            <ArrowLeft size={12} aria-hidden />
            {tr('返回工作流', 'Back to workflow')}
          </Link>
        }
        actions={
          <>
            <RowAction icon={Copy} onClick={() => void copyId()}>
              {tr('复制 run_id', 'Copy run_id')}
            </RowAction>
            <RowAction
              icon={RefreshCw}
              iconClassName={cn(refreshing && 'animate-spin')}
              disabled={refreshing}
              onClick={() => void load(true)}
            >
              {tr('刷新', 'Refresh')}
            </RowAction>
            <Button variant="primary" size="sm" className="h-7 px-3 text-[11.5px]" onClick={() => setRunOpen(true)} disabled={run.status === 'waiting'}>
              <Play size={12} aria-hidden />
              {tr('再次运行', 'Run again')}
            </Button>
          </>
        }
      />

      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-6 py-4">
        <div className="flex flex-wrap items-center gap-2">
          <RunStatusPill status={run.status} />
          {run.status === 'waiting' && (
            <span className="text-[11px] text-warn">{tr('运行停在人工节点，确认后继续', 'Paused at a human gate')}</span>
          )}
          {run.error !== '' && <span className="text-[11px] text-danger">{tr('运行失败，见下方错误输出', 'Run failed; see the error below')}</span>}
        </div>

        <DetailStatGrid>
          <DetailStat label={tr('耗时', 'Duration')} value={formatDuration(run.duration_ms)} />
          <DetailStat label={tr('已执行节点', 'Nodes run')} value={`${executed}`} />
          <DetailStat label={tr('触发方式', 'Trigger')} value={triggerText(run.trigger, tr)} />
          <DetailStat label={tr('开始时间', 'Started')} value={fullDateTime(run.created_at)} />
          <DetailStat label={tr('结束时间', 'Finished')} value={run.finished_at === null ? '—' : fullDateTime(run.finished_at)} />
        </DetailStatGrid>

        {gate !== null && (
          <GateCard
            gate={gate}
            gateNode={gateNode}
            busy={busy}
            value={gateInput}
            onChange={setGateInput}
            onApprove={() => void decide('approve')}
            onReject={() => void decide('reject')}
          />
        )}

        <div className="grid min-h-0 grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_360px]">
          <section className="min-w-0 rounded-xl border border-zinc-800/60 bg-zinc-900/30">
            <header className="flex items-center justify-between border-b border-zinc-800/60 px-4 py-2.5">
              <h2 className="text-[12.5px] font-medium text-zinc-100">{tr('执行时间线', 'Execution timeline')}</h2>
              <span className="text-[10.5px] tabular-nums text-zinc-500">{tr(`${executed} 条节点记录`, `${executed} node records`)}</span>
            </header>
            {run.nodes.length === 0 ? (
              <div className="px-4 py-10 text-center text-[11.5px] text-zinc-500">
                {tr('还没有节点执行记录', 'No node has run yet')}
              </div>
            ) : (
              <ol className="divide-y divide-zinc-800/40">
                {run.nodes.map((node, index) => (
                  <NodeRunRow
                    key={node.id}
                    index={index + 1}
                    node={node}
                    definition={run.definition}
                    last={index === run.nodes.length - 1}
                  />
                ))}
              </ol>
            )}
          </section>

          <div className="flex min-w-0 flex-col gap-3">
            <section className="overflow-hidden rounded-xl border border-zinc-800/60 bg-zinc-900/30">
              <header className="border-b border-zinc-800/60 px-4 py-2.5">
                <h2 className="text-[12.5px] font-medium text-zinc-100">{tr('画布快照', 'Canvas snapshot')}</h2>
                <p className="mt-0.5 text-[10.5px] text-zinc-500">
                  {tr('这次运行使用的定义；绿=成功 红=失败 灰=未执行', 'Definition used for this run; green=ok red=failed grey=not run')}
                </p>
              </header>
              <div className="h-44 bg-zinc-950/40">
                <WorkflowGraphThumb
                  graph={{
                    nodes: run.definition.nodes.map((node) => ({ id: node.id, kind: node.kind, label: node.label })),
                    edges: run.definition.edges,
                  }}
                  statuses={statuses}
                />
              </div>
            </section>

            <IoCard title={tr('运行输入', 'Run input')} value={run.input} />
            <IoCard title={tr('运行输出', 'Run output')} value={run.output} />
            {run.error !== '' && (
              <section className="overflow-hidden rounded-xl border border-red-500/40 bg-red-500/5">
                <header className="border-b border-red-500/30 px-4 py-2.5 text-[12.5px] font-medium text-red-200">
                  {tr('错误', 'Error')}
                </header>
                <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words px-4 py-3 font-mono text-[11px] leading-5 text-red-300">
                  {run.error}
                </pre>
              </section>
            )}
          </div>
        </div>
      </div>

      {runOpen && (
        <RunWorkflowDialog
          workflowId={run.workflow_id}
          workflowName={workflowName}
          definition={run.definition}
          onClose={() => setRunOpen(false)}
          onStarted={(next) => {
            setRunOpen(false);
            navigate(`/workflows/runs/${next.run_uid}`);
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

function IoCard({ title, value }: { title: string; value: string }) {
  const { tr } = useI18n();
  const parsed = parseJsonText(value);
  return (
    <section className="overflow-hidden rounded-xl border border-zinc-800/60 bg-zinc-900/30">
      <header className="flex items-center justify-between border-b border-zinc-800/60 px-4 py-2.5">
        <h2 className="text-[12.5px] font-medium text-zinc-100">{title}</h2>
      </header>
      <div className="px-3 py-2.5">
        {parsed === null ? (
          <div className="text-[11px] text-zinc-600">{tr('空', 'Empty')}</div>
        ) : (
          <CodeBlock>{typeof parsed === 'string' ? parsed : JSON.stringify(parsed, null, 2)}</CodeBlock>
        )}
      </div>
    </section>
  );
}

function GateCard({
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
    <section className="rounded-xl border border-amber-500/40 bg-amber-500/[0.06] px-4 py-3">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-amber-500/15 text-amber-300">
          <ShieldCheck size={13} aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[12px] font-medium text-amber-200">
              {isInput ? tr('等待人工输入', 'Waiting for user input') : tr('等待人工审批', 'Waiting for approval')}
            </span>
            <span className="rounded border border-amber-500/30 px-1 font-mono text-[10px] leading-4 text-amber-300/90">{gate.gate}</span>
          </div>
          <p className="mt-1 whitespace-pre-wrap break-words text-[11.5px] leading-5 text-amber-100/90">{gate.prompt || tr('（没有说明文案）', '(no prompt)')}</p>
          {isInput && (
            <div className="mt-2 max-w-md space-y-1">
              <div className="text-[10.5px] text-amber-200/80">
                {tr('提交字段：', 'Field: ')}
                <span className="font-mono">{gateNode?.output_key ?? '—'}</span>
              </div>
              <Input
                value={value}
                onChange={(event) => onChange(event.target.value)}
                placeholder={tr('填写后提交', 'Value to submit')}
                aria-label={tr('人工输入内容', 'User input value')}
                className="h-8 border-amber-500/30 bg-zinc-950/40 text-[11.5px]"
                disabled={busy}
              />
            </div>
          )}
          <div className="mt-2.5 flex items-center gap-2">
            <Button variant="primary" size="sm" className="h-7 px-3 text-[11.5px]" onClick={onApprove} disabled={busy}>
              <Check size={12} aria-hidden />
              {busy ? tr('执行中…', 'Working…') : isInput ? tr('提交并继续', 'Submit & continue') : tr('批准并继续', 'Approve & continue')}
            </Button>
            <Button variant="dangerGhost" size="sm" className="h-7 px-3 text-[11.5px]" onClick={onReject} disabled={busy}>
              <X size={12} aria-hidden />
              {tr('拒绝', 'Reject')}
            </Button>
            <span className="text-[10.5px] text-amber-200/60">
              {tr('拒绝会把这次运行标记为失败', 'Rejecting marks the run as failed')}
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}

function NodeRunRow({
  index,
  node,
  definition,
  last,
}: {
  index: number;
  node: WorkflowNodeRun;
  definition: WorkflowRunDetail['definition'];
  last: boolean;
}) {
  const { tr, locale } = useI18n();
  const [open, setOpen] = useState(false);
  const definitionNode = definition.nodes.find((item) => item.id === node.node_id) ?? null;
  const kind = definitionNode?.kind ?? node.node_kind;
  const Icon = iconOf(kind);
  const tone = toneOf(kind);
  const label = definitionNode?.label.trim() ?? '';
  const title = label !== '' ? label : node.node_id;
  const parsedOutput = parseJsonText(node.output);
  const outputText = parsedOutput === null ? '' : typeof parsedOutput === 'string' ? parsedOutput : JSON.stringify(parsedOutput, null, 2);

  return (
    <li className="relative px-4 py-3">
      {!last && <span className="absolute left-[26px] top-[34px] h-[calc(100%-20px)] w-px bg-zinc-800/70" aria-hidden />}
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-zinc-700/80 bg-zinc-950 font-mono text-[9px] tabular-nums text-zinc-500">
          {String(index).padStart(2, '0')}
        </span>
        <span className={cn('mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md', tone.iconBox)}>
          <Icon size={12} className={tone.icon} aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="truncate text-[12px] font-medium text-zinc-200">{title}</span>
            <span className="font-mono text-[10px] text-zinc-600">{node.node_id}</span>
            <span className="text-[10px] text-zinc-600">{kindTitle(kind, null, locale === 'zh-CN')}</span>
            <span className="ml-auto flex items-center gap-1.5">
              {node.attempt > 1 && (
                <span className="rounded bg-indigo-500/10 px-1 text-[10px] leading-4 text-indigo-300">{tr(`第 ${node.attempt} 轮`, `attempt ${node.attempt}`)}</span>
              )}
              {node.route !== '' && (
                <span className="rounded border border-zinc-700/70 bg-zinc-800/40 px-1 font-mono text-[10px] leading-4 text-zinc-400">{node.route}</span>
              )}
              <StatusPill
                tone={node.status === 'succeeded' ? 'ok' : 'danger'}
                label={node.status === 'succeeded' ? tr('成功', 'Succeeded') : tr('失败', 'Failed')}
              />
              <span className="font-mono text-[10.5px] tabular-nums text-zinc-500">{formatDuration(node.duration_ms)}</span>
            </span>
          </div>

          {node.error !== '' && (
            <pre className="mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md border border-red-500/30 bg-red-500/5 px-2.5 py-1.5 font-mono text-[10.5px] leading-5 text-red-300">
              {node.error}
            </pre>
          )}

          {outputText !== '' && (
            <div className="mt-1.5">
              <button
                type="button"
                onClick={() => setOpen((current) => !current)}
                aria-expanded={open}
                className="inline-flex items-center gap-1 rounded px-1 text-[10.5px] text-zinc-500 transition-colors hover:bg-zinc-800/60 hover:text-zinc-300"
              >
                {open ? <ChevronDown size={11} aria-hidden /> : <ChevronRight size={11} aria-hidden />}
                {open ? tr('收起输出', 'Hide output') : tr('展开输出', 'Show output')}
                <span className="ml-1 font-mono text-[9.5px] text-zinc-600">{outputText.length} chars</span>
              </button>
              {open && (
                <div className="mt-1">
                  <CodeBlock>{outputText}</CodeBlock>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </li>
  );
}
