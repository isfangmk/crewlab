import { Hint } from '@/components/ui/Tooltip';
import { useState, useEffect } from 'react';
import { resolveToolStatus } from '@/lib/toolStatus';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  AlertCircle,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Copy,
  GitBranch,
  Loader2,
  Pencil,
  RefreshCw,
  ShieldAlert,
  Wrench,
  X,
  XCircle,
} from 'lucide-react';
import { loadChatAttachment, type ChatMessage, type ImageAttachment, type ToolCallSummary } from '@/api/chat';
import { streamApproveApproval, rejectApproval, getApproval } from '@/api/approvals';
import { emitChatLive } from '@/lib/chatLiveBus';
import { cn } from '@/lib/cn';
import { isConfigDraftConfirmationMessage } from '@/lib/configDraftConfirmation';
import { useI18n } from '@/i18n/locale';
import { Button, Chip, CodeBlock, RowActionIcon, StatusPill, Textarea } from '@/components/ui';
import { Modal } from '@/components/Modal';
import { RejectApprovalDialog } from '@/components/RejectApprovalDialog';
import { ThinkingPanel } from '@/components/ThinkingPanel';
import { SourcesSection, collectToolSources, extractSources } from '@/components/SourcesBlock';

export type ConfigDraftResult = {
  kind: 'config_draft';
	proposal?: { kind: 'proposal'; type: string; state: string; title: string; summary?: string; actions: { kind: string; label: string; enabled: boolean }[] };
  domain?: string;
  action?: string;
  summary?: string;
  target?: { id?: number; name?: string; type?: string; existing?: boolean };
  payload?: unknown;
  preview?: unknown;
  diff?: unknown;
  warnings?: string[];
  scope?: { type?: string; label?: string; reason?: string; change_hint?: string };
  confirmation_prompt?: string;
  rollback?: string;
  apply_tool?: string;
  draft_hash?: string;
};

type ConfirmConfigDraft = (draft: ConfigDraftResult) => boolean | void | Promise<boolean | void>;

type Props = {
  message: ChatMessage;
  sessionId?: string;
  onConfirmConfigDraft?: ConfirmConfigDraft;
  /** 回合进行中：消息操作（重发/重新生成/分叉）禁用。 */
  actionsDisabled?: boolean;
  onRegenerate?(message: ChatMessage): void;
  onFork?(message: ChatMessage): void;
  onEditResend?(message: ChatMessage, content: string): void;
};

export function MessageBubble({
  message,
  sessionId,
  onConfirmConfigDraft,
  actionsDisabled,
  onRegenerate,
  onFork,
  onEditResend,
}: Props) {
  if ((message.kind === 'tool_card' || message.role === 'tool') && message.tool_call) {
    return <ToolCallSummaryBlock call={fromSummary(message.tool_call)} onConfirmConfigDraft={onConfirmConfigDraft} />;
  }
  if (message.role === 'tool') return <ToolBubble message={message} onConfirmConfigDraft={onConfirmConfigDraft} />;
  if (message.role === 'user') {
    return (
      <UserBubble
        message={message}
        sessionId={sessionId}
        actionsDisabled={actionsDisabled}
        onEditResend={onEditResend}
      />
    );
  }
  // 仅渲染后端在「开启思考」时推送的思考链
  if (message.kind === 'thinking') {
    return <ThinkingPanel message={message} />;
  }
  // Tool-only assistant rows (empty content + has tool_calls) shouldn't
  // appear during streaming; on history reload they would, so suppress.
  if (
    message.role === 'assistant' &&
    (!message.content || message.content.length === 0) &&
    !message.pending
  ) {
    return null;
  }
  return (
    <AssistantBubble
      message={message}
      onConfirmConfigDraft={onConfirmConfigDraft}
      actionsDisabled={actionsDisabled}
      onRegenerate={onRegenerate}
      onFork={onFork}
    />
  );
}

// fromSummary maps the wire-level ToolCallSummary (server SSE shape) to
// the {arguments,result,...} shape the rich card already understands.
function fromSummary(tc: ToolCallSummary) {
  const args = tc.arguments ?? (tc.arguments_raw ? safeParse(tc.arguments_raw) : undefined);
  const result = tc.result ?? (tc.result_raw ? safeParse(tc.result_raw) : undefined);
  return {
    name: tc.name,
    device_id: tc.device_id,
    status: tc.status,
    duration_ms: tc.duration_ms,
    error: tc.error,
    arguments: args as Record<string, unknown> | undefined,
    result,
  };
}

function safeParse(s: string): unknown {
  try {
    return JSON.parse(s);
  } catch {
    return s;
  }
}

function CopyButton({ text }: { text: string }) {
  const { tr } = useI18n();
  const [state, setState] = useState<'idle' | 'copied' | 'error'>('idle');

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setState('copied');
    } catch {
      setState('error');
    }
    window.setTimeout(() => setState('idle'), 1500);
  };

  return (
    <>
      <RowActionIcon
        className="h-6 w-6"
        icon={state === 'copied' ? Check : Copy}
        tone={state === 'error' ? 'danger' : 'neutral'}
        label={
          state === 'copied'
            ? tr('已复制', 'Copied')
            : state === 'error'
              ? tr('复制失败', 'Copy failed')
              : tr('复制', 'Copy')
        }
        disabled={text === ''}
        onClick={() => void onCopy()}
      />
      {state !== 'idle' && (
        <span
          role="status"
          className={cn('text-[10px]', state === 'error' ? 'text-red-400' : 'text-zinc-500')}
        >
          {state === 'error' ? tr('复制失败', 'Copy failed') : tr('已复制', 'Copied')}
        </span>
      )}
    </>
  );
}

function UserBubble({ message, sessionId, actionsDisabled, onEditResend }: Props) {
  const { tr } = useI18n();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(message.content ?? '');
  const content = compactUserContent(message.content ?? '', tr);
  const canEdit = onEditResend !== undefined && !actionsDisabled;

  // Codex-style: small, compact zinc chip pinned right. No accent color
  // — keeps the visual weight on the assistant content below.
  return (
    <div className="group flex flex-col items-end gap-1">
      <div className="max-w-[78%] space-y-2 rounded-2xl rounded-br-md bg-zinc-800/80 px-3.5 py-2 text-[14px] leading-relaxed text-zinc-100 ring-1 ring-zinc-700/60">
        {!!message.attachments?.length && (
          <div className="grid max-w-md grid-cols-2 gap-2">
            {message.attachments.map((attachment, index) => (
              <ProtectedAttachmentImage key={`${attachment.id}-${index}`} sessionId={sessionId} attachment={attachment} />
            ))}
          </div>
        )}
        {content && <div>{content}</div>}
      </div>
      {editing ? (
        <div className="w-full max-w-[78%] space-y-2 rounded-xl border border-zinc-700/60 bg-zinc-900/60 p-2">
          <Textarea
            aria-label={tr('编辑消息', 'Edit message')}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={4}
            autoFocus
          />
          <div className="flex items-center justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              type="button"
              onClick={() => {
                setEditing(false);
                setDraft(message.content ?? '');
              }}
            >
              {tr('取消', 'Cancel')}
            </Button>
            <Button
              variant="primary"
              size="sm"
              type="button"
              disabled={draft.trim() === ''}
              onClick={() => {
                setEditing(false);
                onEditResend?.(message, draft);
              }}
            >
              {tr('保存并重发', 'Save & resend')}
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
          <CopyButton text={message.content ?? ''} />
          {canEdit && (
            <RowActionIcon
              className="h-6 w-6"
              icon={Pencil}
              label={tr('编辑并重发', 'Edit & resend')}
              onClick={() => {
                setDraft(message.content ?? '');
                setEditing(true);
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}

function ProtectedAttachmentImage({ sessionId, attachment }: { sessionId?: string; attachment: ImageAttachment }) {
  const [src, setSrc] = useState('');
  const [failed, setFailed] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  useEffect(() => {
    if (!sessionId) return;
    let active = true;
    let objectURL = '';
    setFailed(false);
    void loadChatAttachment(sessionId, attachment.id)
      .then((url) => {
        objectURL = url;
        if (active) setSrc(url);
        else URL.revokeObjectURL(url);
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
      if (objectURL) URL.revokeObjectURL(objectURL);
    };
  }, [sessionId, attachment.id]);
  if (failed) return <div className="flex h-24 w-24 items-center justify-center rounded-lg bg-zinc-700 px-2 text-center text-xs text-zinc-300">Image unavailable</div>;
  return src ? (
    <>
      <img
        src={src}
        alt={attachment.name}
        title="Click to preview"
        className="max-h-64 cursor-zoom-in rounded-lg object-contain"
        onClick={() => setPreviewOpen(true)}
      />
      <Modal open={previewOpen} onClose={() => setPreviewOpen(false)} title={attachment.name} size="xl">
        <div className="flex min-h-48 items-center justify-center">
          <img src={src} alt={`Preview ${attachment.name}`} className="max-h-[75vh] max-w-full rounded-lg object-contain" />
        </div>
      </Modal>
    </>
  ) : <div className="h-24 w-24 animate-pulse rounded-lg bg-zinc-700" />;
}

function compactUserContent(
  content: string,
  tr: (zh: string, en: string) => string,
): string {
  if (!isConfigDraftConfirmationMessage(content)) return content;
  return tr('确认创建这条告警规则', 'Confirm creating this alert rule');
}

function AssistantBubble({ message, actionsDisabled, onRegenerate, onFork, onConfirmConfigDraft }: Props) {
  const { tr } = useI18n();
  // Codex-style: no rounded card around assistant prose. Render markdown
  // flush against the column so headings/lists/code blocks read like a
  // document. Tool calls (when attached) appear as their own rows inside
  // the same column, matching the doc-card aesthetic.
  // 流式只渲染已到正文；底部 ThinkingIndicator 表示进行中，不在气泡内再叠三点。
  const body = message.content ?? '';
  // 本回答用到的来源统一收口到气泡底部一块，工具卡内不再重复列
  const sources = collectToolSources(message.tool_calls);
  return (
    <div className="group flex flex-col items-stretch gap-2">
      {body !== '' && (
        <div className="md-body text-zinc-100">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
        </div>
      )}
      {message.tool_calls?.map((tc, i) => (
        <ToolCallSummaryBlock
          key={`${tc.name}-${i}`}
          call={tc}
          showSources={false}
          onConfirmConfigDraft={onConfirmConfigDraft}
        />
      ))}
      {sources.length > 0 && <SourcesSection sources={sources} className="mt-1 border-l border-zinc-800/60 pl-3" />}
      {body !== '' && !message.pending && (
        <div className="flex items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
          <CopyButton text={body} />
          {onRegenerate !== undefined && (
            <RowActionIcon
              className="h-6 w-6"
              icon={RefreshCw}
              label={tr('重新生成', 'Regenerate')}
              disabled={actionsDisabled}
              onClick={() => onRegenerate(message)}
            />
          )}
          {onFork !== undefined && (
            <RowActionIcon
              className="h-6 w-6"
              icon={GitBranch}
              label={tr('从这条消息分叉', 'Fork from here')}
              disabled={actionsDisabled}
              onClick={() => onFork(message)}
            />
          )}
        </div>
      )}
    </div>
  );
}

function ToolBubble({ message, onConfirmConfigDraft }: Props) {
  // History-reload path: the message persisted by the agent loop only
  // carries the tool name + JSON result string. We don't have args for
  // these (would need to join chat_tool_calls); show what we have.
  const result = message.content ? safeParse(message.content) : undefined;
  return (
    <ToolCallSummaryBlock
      onConfirmConfigDraft={onConfirmConfigDraft}
      call={{
        name: message.tool_name ?? 'tool',
        // 历史消息没有执行状态，仅从明确的结果字段恢复。
        result,
      }}
    />
  );
}

function ToolCallSummaryBlock({
  call,
  onConfirmConfigDraft,
  showSources = true,
}: {
  call: {
    name: string;
    device_id?: number;
    status?: string;
    arguments?: Record<string, unknown> | unknown;
    result?: unknown;
    duration_ms?: number;
    error?: string;
  };
  onConfirmConfigDraft?: ConfirmConfigDraft;
  /** 气泡已聚合来源时置 false，避免同一批来源渲染两次。 */
  showSources?: boolean;
}) {
  const { tr } = useI18n();
  const [open, setOpen] = useState(false);
  const status = resolveToolStatus(call);
  const isPending = status === 'pending';
  const isError = status === 'error' || status === 'timeout' || !!call.error;
  const hint = argSummary(call.arguments);
  const sources = showSources ? extractSources(call.name, call.result) : [];
  // 工具结果 status=pending_approval 时，在会话内渲染批准/拒绝卡。
  const approval = pendingApproval(call.result);
  if (approval) {
    return <PendingApprovalCard approvalID={approval.id} kind={approval.kind} toolName={approval.toolName || call.name} command={approval.command || argCommandText(call.arguments)} />;
  }
  const configDraft = !isError ? asConfigDraft(call.result) : null;
  return (
    <div
      className={cn(
        'w-full overflow-hidden rounded-lg border bg-zinc-900/40 text-xs',
        isError ? 'border-red-500/30' : 'border-zinc-800/60',
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={tr(`工具调用 ${call.name}`, `Tool call ${call.name}`)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-zinc-300 hover:bg-zinc-800/40"
      >
        <StatusIcon status={status} />
        <Wrench size={12} className="text-zinc-500" />
        <span className="font-medium text-zinc-200">{call.name}</span>
        {hint && (
          <Hint content={hint}><span className="truncate text-[11px] text-zinc-500" >
            {hint}
          </span></Hint>
        )}
        {typeof call.device_id === 'number' && (
          <Chip dense className="font-mono">host#{call.device_id}</Chip>
        )}
        <span className="ml-auto flex items-center gap-2 text-[11px] text-zinc-500">
          {typeof call.duration_ms === 'number' && call.duration_ms > 0 && (
            <span>{formatDuration(call.duration_ms)}</span>
          )}
          {status === 'unknown' && <StatusPill tone="neutral" label={tr('状态未知', 'Status unknown')} />}
          {isPending && <StatusPill tone="info" pulse label={tr('运行中', 'Running')} />}
          {isError && <StatusPill tone="danger" label={tr('失败', 'Failed')} />}
          {open ? (
            <ChevronDown size={13} className="text-zinc-500" />
          ) : (
            <ChevronRight size={13} className="text-zinc-500" />
          )}
        </span>
      </button>
      {sources.length > 0 && (
        <div className="border-t border-zinc-800/60 bg-zinc-950/30 px-3 py-2">
          <SourcesSection sources={sources} />
        </div>
      )}
      {configDraft && (
        <ConfigDraftCard draft={configDraft} onConfirm={onConfirmConfigDraft} />
      )}
      {open && (
        <div className="border-t border-zinc-800/60 bg-zinc-950/40 px-3 py-2">
          {call.arguments !== undefined && (
            <div className="mb-2">
              <div className="mb-1 text-[11px] uppercase tracking-wide text-zinc-500">{tr('参数', 'Arguments')}</div>
              <CodeBlock maxHeightClassName="max-h-48">
                {typeof call.arguments === 'string' ? call.arguments : (JSON.stringify(call.arguments, null, 2) ?? '')}
              </CodeBlock>
            </div>
          )}
          {call.result !== undefined && (
            <div>
              <div className="mb-1 text-[11px] uppercase tracking-wide text-zinc-500">{tr('结果', 'Result')}</div>
              <CodeBlock>
                {typeof call.result === 'string' ? call.result : (JSON.stringify(call.result, null, 2) ?? '')}
              </CodeBlock>
            </div>
          )}
          {call.error && (
            <div className="mt-1 text-[11px] text-red-400">{call.error}</div>
          )}
          {!call.error && call.result === undefined && isPending && (
            <div className="text-[11px] text-zinc-500">{tr('等待结果…', 'Waiting for result…')}</div>
          )}
        </div>
      )}
    </div>
  );
}

function ConfigDraftCard({
  draft,
  onConfirm,
}: {
  draft: ConfigDraftResult;
  onConfirm?: ConfirmConfigDraft;
}) {
  const { tr } = useI18n();
  const [state, setState] = useState<'idle' | 'submitting' | 'confirmed' | 'cancelled'>('idle');
  const preview = previewSummary(draft.preview);
  const warnings = Array.isArray(draft.warnings) ? draft.warnings.filter(Boolean) : [];
  const payload = payloadSummary(draft.payload);
  const scope = scopeSummary(draft.scope, tr, !draft.confirmation_prompt);
  const disabled = state !== 'idle' || !onConfirm;
	const proposal = draft.proposal;

  return (
    <div className="border-t border-zinc-800/80 bg-zinc-950/30 px-3 py-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
			{proposal && <Chip dense>{tr('提案', 'Proposal')}</Chip>}
            <Chip dense>{domainLabel(draft.domain, tr)}</Chip>
            {draft.action && (
              <span className="text-[11px] uppercase text-zinc-500">{draft.action}</span>
            )}
            {draft.target?.name && (
              <span className="truncate text-[11px] text-zinc-500">{draft.target.name}</span>
            )}
          </div>
          <div className="text-[12.5px] font-medium text-zinc-100">
            {proposal?.title || draft.summary || tr('配置草案', 'Configuration draft')}
          </div>
          {scope && <div className="text-[11px] leading-5 text-zinc-300">{scope}</div>}
          {draft.confirmation_prompt && (
            <div className="text-[11px] leading-5 text-zinc-400">{draft.confirmation_prompt}</div>
          )}
          {payload && <div className="text-[11px] leading-5 text-zinc-400">{payload}</div>}
          {preview && <div className="text-[11px] leading-5 text-zinc-400">{preview}</div>}
          {warnings.length > 0 && (
            <div className="space-y-1 text-[11px] leading-5 text-amber-300">
              {warnings.slice(0, 3).map((w, i) => (
                <div key={`${w}-${i}`}>{w}</div>
              ))}
            </div>
          )}
          {draft.rollback && (
            <div className="text-[11px] leading-5 text-zinc-500">{draft.rollback}</div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            variant="primary"
            size="sm"
            disabled={disabled}
            onClick={async () => {
              if (!onConfirm) return;
              setState('submitting');
              try {
                const ok = await onConfirm(draft);
                setState(ok === false ? 'idle' : 'confirmed');
              } catch {
                setState('idle');
              }
            }}
          >
            <CheckCircle2 size={13} />
            {state === 'confirmed'
              ? tr('已确认', 'Confirmed')
              : state === 'submitting'
                ? tr('应用中', 'Applying')
                : tr('确认应用', 'Apply')}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={state !== 'idle'}
            onClick={() => setState('cancelled')}
          >
            <XCircle size={13} />
            {state === 'cancelled' ? tr('已取消', 'Cancelled') : tr('取消', 'Cancel')}
          </Button>
        </div>
      </div>
    </div>
  );
}

function asConfigDraft(result: unknown): ConfigDraftResult | null {
  const value = typeof result === 'string' ? safeParse(result) : result;
  if (!value || typeof value !== 'object') return null;
  const obj = value as Record<string, unknown>;
  if (obj.kind !== 'config_draft') return null;
  if (obj.domain !== 'alert_rule') return null;
  return obj as ConfigDraftResult;
}

function domainLabel(domain: string | undefined, tr: (zh: string, en: string) => string): string {
  switch (domain) {
    case 'alert_rule':
      return tr('告警规则', 'Alert rule');
    default:
      return tr('配置', 'Config');
  }
}

function previewSummary(preview: unknown): string {
  if (!preview || typeof preview !== 'object') return '';
  const p = preview as Record<string, unknown>;
  if (typeof p.skipped_reason === 'string' && p.skipped_reason) return p.skipped_reason;
  if (typeof p.fire_count === 'number') {
    const unit = typeof p.unit === 'string' && p.unit ? ` ${p.unit}` : '';
    return `Preview fire_count=${p.fire_count}${unit}`;
  }
  return '';
}

function scopeSummary(
  scope: ConfigDraftResult['scope'] | undefined,
  tr: (zh: string, en: string) => string,
  includeHint: boolean,
): string {
  if (!scope) return '';
  const label = typeof scope.label === 'string' ? scope.label.trim() : '';
  const type = typeof scope.type === 'string' ? scope.type.trim() : '';
  const scopeText = label || type;
  if (!scopeText) return '';
  const hint = typeof scope.change_hint === 'string' ? scope.change_hint.trim() : '';
  return includeHint && hint
    ? `${tr('范围：', 'Scope: ')}${scopeText} · ${hint}`
    : `${tr('范围：', 'Scope: ')}${scopeText}`;
}

function payloadSummary(payload: unknown): string {
  if (!payload || typeof payload !== 'object') return '';
  const obj = payload as Record<string, unknown>;
  const rows: string[] = [];
  for (const [key, value] of Object.entries(obj)) {
    if (rows.length >= 4) break;
    if (value === null || value === undefined || value === '') continue;
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      rows.push(`${key}: ${String(value)}`);
      continue;
    }
    if (typeof value === 'object') {
      const nested = Object.entries(value as Record<string, unknown>)
        .filter(([, v]) => v !== null && v !== undefined && v !== '')
        .slice(0, 4)
        .map(([k, v]) => `${k}: ${String(v)}`);
      if (nested.length > 0) rows.push(nested.join(' · '));
    }
  }
  return rows.join(' · ');
}

function StatusIcon({ status }: { status?: string }) {
  if (status === 'pending') {
    return <Loader2 size={13} className="animate-spin text-blue-400" />;
  }
  if (status === 'error' || status === 'timeout') {
    return <AlertCircle size={13} className="text-red-400" />;
  }
  if (status === 'success') return <CheckCircle2 size={13} className="text-emerald-400" />;
  return <AlertCircle size={13} className="text-zinc-500" />;
}

// argSummary picks a compact one-line preview from the arguments object.
// Most builtin skills have a single load-bearing field (query, host,
// path, ...) — show that. Falls back to the first scalar value.
function argSummary(args: unknown): string {
  if (!args || typeof args !== 'object') return '';
  const obj = args as Record<string, unknown>;
  const preferred = ['query', 'host', 'url', 'path', 'unit', 'expr', 'instance', 'device_id'];
  for (const k of preferred) {
    const v = obj[k];
    if (typeof v === 'string' && v) return truncate(v, 80);
    if (typeof v === 'number') return String(v);
  }
  for (const [, v] of Object.entries(obj)) {
    if (typeof v === 'string' && v) return truncate(v, 80);
    if (typeof v === 'number') return String(v);
  }
  return '';
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

// --- 行内审批卡 ---

// pendingApproval returns the approval metadata when a tool result is the
// approval "pending_approval" envelope, else null.
function pendingApproval(result: unknown): { id: string; kind: string; toolName: string; command: string } | null {
  if (result && typeof result === 'object') {
    const r = result as Record<string, unknown>;
    if (r.status === 'pending_approval' && typeof r.approval_id === 'string') {
      return {
        id: r.approval_id,
        kind: typeof r.kind === 'string' ? r.kind : 'cloud_bash',
        toolName: typeof r.tool_name === 'string' ? r.tool_name : '',
        command: typeof r.command === 'string' ? r.command : '',
      };
    }
  }
  return null;
}

function argCommandText(args: unknown): string {
  if (args && typeof args === 'object') {
    const c = (args as Record<string, unknown>).command;
    if (typeof c === 'string') return c;
  }
  return '';
}

const CHAT_REFETCH_EVENT = 'ongrid:chat-refetch';

function requestChatRefetch() {
  window.dispatchEvent(new Event(CHAT_REFETCH_EVENT));
}

// PendingApprovalCard renders the shared in-conversation approve/reject
// prompt. Approve runs the frozen tool candidate (the backend executor runs
// synchronously) and shows the result inline; reject discards it.
function PendingApprovalCard({ approvalID, kind, toolName, command }: { approvalID: string; kind: string; toolName: string; command: string }) {
  const { tr } = useI18n();
  const [state, setState] = useState<'loading' | 'idle' | 'busy' | 'done' | 'rejected' | 'error' | 'stale'>('loading');
  const [resultText, setResultText] = useState('');
  const [errText, setErrText] = useState('');
  const [cmd, setCmd] = useState(command);
  const [approvalKind, setApprovalKind] = useState(kind);
  const [approvedToolName, setApprovedToolName] = useState(toolName);
  const [creds, setCreds] = useState<string[]>([]);
  const [rejectOpen, setRejectOpen] = useState(false);
  const isHostBash = approvalKind === 'host_bash';

  // 与审批页权威状态对齐：mount 拉一次；idle 时短轮询，跨页批准后能切到已执行。
  useEffect(() => {
    let alive = true;
    let pollTimer: number | undefined;

    const applyPayload = (a: Awaited<ReturnType<typeof getApproval>>) => {
      setApprovalKind(a.kind || approvalKind);
      const p = JSON.parse(a.payload) as {
        command?: string;
        credentials?: string[];
        tool_name?: string;
        summary?: string;
        arguments?: Record<string, unknown>;
      };
      // 卡片正文只展示工具实参（ADK hint 不进 UI）
      if (p.arguments === undefined || typeof p.arguments !== 'object') {
        throw new Error(`审批 payload 缺少 arguments: ${approvalID}`);
      }
      setCmd(JSON.stringify({ tool: p.tool_name ?? approvedToolName, arguments: p.arguments }, null, 2));
      if (p.tool_name) setApprovedToolName(p.tool_name);
      if (Array.isArray(p.credentials)) setCreds(p.credentials.filter(Boolean));
    };

    const applyStatus = (a: Awaited<ReturnType<typeof getApproval>>, notify: boolean) => {
      if (a.status === 'executed') {
        setState('done');
        setResultText(a.result ?? '');
        if (notify) requestChatRefetch();
        return 'terminal';
      }
      if (a.status === 'rejected') {
        setState('rejected');
        if (notify) requestChatRefetch();
        return 'terminal';
      }
      if (a.status === 'failed') {
        setState('error');
        setErrText(a.result ?? 'failed');
        if (notify) requestChatRefetch();
        return 'terminal';
      }
      if (a.status === 'approved') {
        setState('busy');
        return 'busy';
      }
      setState('idle');
      return 'idle';
    };

    const sync = (notifyOnTerminal: boolean) => {
      getApproval(approvalID)
        .then((a) => {
          if (!alive) return;
          applyPayload(a);
          const phase = applyStatus(a, notifyOnTerminal);
          if (phase === 'idle' || phase === 'busy') {
            schedulePoll();
          }
        })
        .catch(() => {
          // 404 或不可达：不再展示死按钮
          if (alive) setState('stale');
        });
    };

    const schedulePoll = () => {
      if (pollTimer !== undefined) window.clearTimeout(pollTimer);
      pollTimer = window.setTimeout(() => {
        if (!alive) return;
        if (document.visibilityState !== 'visible') {
          schedulePoll();
          return;
        }
        sync(true);
      }, 2000);
    };

    const onVisibility = () => {
      if (document.visibilityState === 'visible') sync(true);
    };

    sync(false);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      alive = false;
      if (pollTimer !== undefined) window.clearTimeout(pollTimer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [approvalID]);

  // 批准直接执行，不再二次确认弹窗。
  const approve = async () => {
    setState('busy');
    emitChatLive({ type: 'approval_decided', approvalId: approvalID });
    emitChatLive({ type: 'busy', busy: true });
    try {
      // 走 SSE 续跑：正文/思考增量交给 ChatThread 打字机渲染
      const a = await streamApproveApproval(
        approvalID,
        '',
        {
          onThinkingDelta: (event) => emitChatLive({ type: 'thinking_delta', event }),
          onAssistantDelta: (event) => emitChatLive({ type: 'assistant_delta', event }),
          onToolStart: (event) => emitChatLive({ type: 'tool_start', event }),
          onToolEnd: (event) => emitChatLive({ type: 'tool_end', event }),
          onApprovalPending: (event) => emitChatLive({ type: 'approval_pending', event }),
        },
      );
      if (a === null) {
        throw new Error('审批流未返回 approval_done');
      }
      if (a.status === 'failed') {
        setState('error');
        setErrText(a.result ?? 'failed');
      } else {
        setState('done');
        setResultText(a.result ?? '');
      }
      requestChatRefetch();
    } catch (e) {
      setState('error');
      setErrText((e as Error).message);
    } finally {
      emitChatLive({ type: 'busy', busy: false });
    }
  };

  const rejectWithReason = async (reason: string) => {
    setState('busy');
    emitChatLive({ type: 'approval_decided', approvalId: approvalID });
    try {
      await rejectApproval(approvalID, reason);
      setState('rejected');
      requestChatRefetch();
    } catch (e) {
      setState('idle');
      throw e;
    }
  };

  const pill: { tone: 'warn' | 'info' | 'ok' | 'danger' | 'neutral'; label: string; pulse?: boolean } =
    state === 'done'
      ? { tone: 'ok', label: tr('已执行', 'Executed') }
      : state === 'rejected'
        ? { tone: 'neutral', label: tr('已拒绝', 'Rejected') }
        : state === 'error'
          ? { tone: 'danger', label: tr('执行失败', 'Failed') }
          : state === 'busy'
            ? { tone: 'info', label: tr('执行中', 'Running'), pulse: true }
            : state === 'stale'
              ? { tone: 'neutral', label: tr('已失效', 'Expired') }
              : state === 'loading'
                ? { tone: 'neutral', label: tr('加载中', 'Loading') }
                : { tone: 'warn', label: tr('待确认', 'Pending') };

  return (
    <div className="w-full overflow-hidden rounded-lg border border-zinc-800/60 bg-zinc-900/40 text-xs">
      <div className="flex items-center gap-2 border-b border-zinc-800/60 px-3 py-2">
        <ShieldAlert size={13} className="text-amber-500/90" />
        <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium text-zinc-200">
          {isHostBash
            ? tr('需要你确认才能在边端主机执行', 'Needs your approval to run on the edge host')
            : approvalKind === 'cloud_bash'
              ? tr('需要你确认才能在云端执行', 'Needs your approval to run in the cloud')
              : tr(`需要你确认才能执行 ${approvedToolName}`, `Needs your approval to run ${approvedToolName}`)}
        </span>
        <StatusPill tone={pill.tone} label={pill.label} pulse={pill.pulse} className="shrink-0" />
      </div>
      <div className="px-3 pb-2.5 pt-2">
        <CodeBlock maxHeightClassName="max-h-40" className="mb-2">
          {cmd || tr('(待执行操作)', '(proposed action)')}
        </CodeBlock>
        {creds.length > 0 && (
          <div className="mb-2 flex flex-wrap items-center gap-1 text-[11px] text-zinc-400">
            <span>{tr('将注入凭证：', 'Injects credentials: ')}</span>
            {creds.map((c) => (
              <Chip key={c} dense className="font-mono">{c}</Chip>
            ))}
          </div>
        )}
        {state === 'loading' && (
          <div className="flex items-center gap-1.5 text-zinc-500">
            <Loader2 size={12} className="animate-spin" />
            {tr('加载审批状态…', 'Loading approval status…')}
          </div>
        )}
        {state === 'stale' && (
          <div className="text-zinc-500">
            {tr('该审批已失效或已处理，请前往「待确认」页查看。', 'This approval is gone or already handled — see the Approvals page.')}
          </div>
        )}
        {state === 'idle' && (
          <div className="flex gap-2">
            <Button variant="primary" size="sm"
              type="button"
              onClick={() => void approve()}
            >
              <Check size={12} />
              {tr('批准并执行', 'Approve & run')}
            </Button>
            <Button variant="dangerGhost" size="sm"
              type="button"
              onClick={() => setRejectOpen(true)}
            >
              <X size={12} />
              {tr('拒绝', 'Reject')}
            </Button>
          </div>
        )}
        {state === 'busy' && <div className="flex items-center gap-1.5 text-zinc-400"><Loader2 size={12} className="animate-spin" />{tr('执行中…', 'Running…')}</div>}
        {state === 'rejected' && <div className="text-zinc-500">{tr('已拒绝，未执行', 'Rejected — not run')}</div>}
        {state === 'error' && <div className="break-all text-red-400">{errText}</div>}
        {state === 'done' && (
          <CodeBlock maxHeightClassName="max-h-56">{prettyResult(resultText)}</CodeBlock>
        )}
      </div>
      <RejectApprovalDialog
        open={rejectOpen}
        subject={approvedToolName}
        onClose={() => setRejectOpen(false)}
        onConfirm={rejectWithReason}
      />
    </div>
  );
}

function prettyResult(s: string): string {
  if (!s) return '';
  try {
    const o = JSON.parse(s) as Record<string, unknown>;
    const parts: string[] = [];
    if (o.stdout) parts.push(String(o.stdout));
    if (o.stderr) parts.push(`[stderr] ${String(o.stderr)}`);
    if (typeof o.exit_code === 'number') parts.push(`[exit ${o.exit_code}]`);
    return parts.join('\n') || s;
  } catch {
    return s;
  }
}
