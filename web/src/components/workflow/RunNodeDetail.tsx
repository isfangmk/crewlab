// 中栏：选中节点的详情。
//
// 从上到下：人工确认（有就最优先，用户一眼看到要做什么）→ 输入 → 输出 → 错误。
// 后续节点不在这里列：左栏已经把整条流程按顺序摆好了。

import { useState, type ReactNode } from 'react';
import { Check, ShieldCheck, X } from 'lucide-react';
import type {
  NodeKind,
  WaitingGate,
  WorkflowNode,
  WorkflowNodeRun,
} from '@/api/workflows';
import { Button, CodeBlock, Input, StatusPill } from '@/components/ui';
import { formatDuration } from '@/components/workflow/RunStatusPill';
import { RunInputRows } from '@/components/workflow/RunInputRows';
import { iconOf, kindTitle, toneOf } from '@/lib/workflowMeta';
import { cn } from '@/lib/cn';
import { useI18n } from '@/i18n/locale';

type Props = {
  nodeId: string;
  record: WorkflowNodeRun | null;
  definition: WorkflowNode | null;
  running: boolean;
  gate: WaitingGate | null;
  gateNode: WorkflowNode | null;
  /** 运行级错误：节点没有自己的错误时，它就是这次失败的真正原因。 */
  runError: string;
  busy: boolean;
  gateInput: string;
  onGateInput(next: string): void;
  onApprove(): void;
  onReject(): void;
};

export function RunNodeDetail({
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
}: Props) {
  const { tr, locale } = useI18n();
  const kind: NodeKind = definition?.kind ?? record?.node_kind ?? 'agent';
  const Icon = iconOf(kind);
  const tone = toneOf(kind);
  const label = definition?.label?.trim() ?? '';
  const title = label !== '' ? label : nodeId;
  const status = detailStatus(record, running, gate, tr);

  return (
    <div className="flex min-h-full flex-col">
      {/* 节点头：图标 + 名字 + node_id + 类型 + 状态，固定在滚动区顶部 */}
      <header className="sticky top-0 z-10 flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-zinc-800/60 bg-bg/95 px-4 py-2.5 backdrop-blur">
        <span className={cn('flex h-6 w-6 shrink-0 items-center justify-center rounded-md', tone.iconBox)}>
          <Icon size={12} className={tone.icon} aria-hidden />
        </span>
        <h2 className="truncate text-[13px] font-medium text-text">{title}</h2>
        <span className="shrink-0 rounded border border-border/70 px-1 font-mono text-[10px] leading-4 text-text-faint">
          {nodeId}
        </span>
        <span className="text-[10px] text-text-faint">{kindTitle(kind, null, locale === 'zh-CN')}</span>
        {record !== null && record.attempt > 1 && (
          <span className="rounded bg-indigo-500/10 px-1 text-[10px] leading-4 text-indigo-400">
            {tr(`第 ${record.attempt} 轮`, `attempt ${record.attempt}`)}
          </span>
        )}
        {record !== null && record.route !== '' && (
          <span className="rounded border border-border/70 bg-zinc-500/10 px-1 font-mono text-[10px] leading-4 text-text-muted">
            {record.route}
          </span>
        )}
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          <StatusPill tone={status.tone} pulse={status.pulse} label={status.label} />
          {record !== null && record.status !== 'running' && (
            <span className="font-mono text-[10.5px] tabular-nums text-text-faint">
              {formatDuration(record.duration_ms)}
            </span>
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
              extra={inputCountLabel(record?.input ?? '', tr)}
            >
              <RunInputRows input={parseInput(record?.input ?? '')} />
            </DetailBlock>

            <DetailBlock
              title={tr('输出', 'Output')}
              extra={outputExtra(record?.output ?? '', tr)}
              copyText={outputText(record?.output ?? '')}
            >
              <OutputBox value={record?.output ?? ''} running={record?.status === 'running'} />
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
          {busy
            ? tr('执行中…', 'Working…')
            : isInput
              ? tr('提交并继续', 'Submit & continue')
              : tr('批准并继续', 'Approve & continue')}
        </Button>
        <Button variant="dangerGhost" size="sm" className="h-7 px-2.5 text-[11.5px]" onClick={onReject} disabled={busy}>
          <X size={12} aria-hidden />
          {tr('拒绝', 'Reject')}
        </Button>
        <span className="text-[10px] text-text-faint">
          {tr('拒绝会把这次运行标记为失败', 'Rejecting marks the run as failed')}
        </span>
      </div>
    </section>
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
  children: ReactNode;
}) {
  const { tr } = useI18n();
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    if (copyText === undefined || copyText === '') return;
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

function OutputBox({ value, running }: { value: string; running: boolean }) {
  const { tr } = useI18n();
  const text = outputText(value);
  if (text === '') {
    return (
      <div className="rounded-lg border border-dashed border-border/70 px-3 py-3 text-[11px] text-text-faint">
        {running
          ? tr('这一步还在执行，输出跑完即到…', 'Still running; output arrives when done…')
          : tr('（无输出）', '(no output)')}
      </div>
    );
  }
  return (
    <div className="overflow-hidden rounded-lg border border-border/60 bg-zinc-950">
      <div className="flex items-center gap-1.5 border-b border-border/50 bg-zinc-500/[0.06] px-2.5 py-1 font-mono text-[9.5px] text-text-faint">
        <span className="h-1.5 w-1.5 rounded-full bg-ok/70" aria-hidden />
        result
      </div>
      <CodeBlock maxHeightClassName="max-h-96" className="rounded-none border-0">
        {text}
      </CodeBlock>
    </div>
  );
}

/** 节点输出文本：JSON 缩进展示，纯文本原样。 */
function outputText(raw: string): string {
  if (raw === '') return '';
  try {
    const parsed: unknown = JSON.parse(raw);
    return typeof parsed === 'string' ? parsed : JSON.stringify(parsed, null, 2);
  } catch {
    return raw;
  }
}

function inputCountLabel(
  raw: string,
  tr: (zh: string, en: string) => string,
): string | undefined {
  const parsed = parseInput(raw);
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) return undefined;
  const count = Object.keys(parsed).length;
  return tr(`${count} 个变量`, `${count} vars`);
}

function outputExtra(
  raw: string,
  tr: (zh: string, en: string) => string,
): string | undefined {
  const text = outputText(raw);
  if (text === '') return undefined;
  return tr(`${text.length} 字符`, `${text.length} chars`);
}

function parseInput(raw: string): unknown {
  if (raw === '') return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
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
  if (running || record?.status === 'running') {
    return { tone: 'info', label: tr('运行中', 'Running'), pulse: true };
  }
  if (record === null) return { tone: 'neutral', label: tr('未执行', 'Not run'), pulse: false };
  if (record.status === 'failed') return { tone: 'danger', label: tr('失败', 'Failed'), pulse: false };
  return { tone: 'ok', label: tr('成功', 'Succeeded'), pulse: false };
}
