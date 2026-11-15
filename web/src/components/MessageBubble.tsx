import { Hint } from '@/components/ui/Tooltip';
import { useState, useEffect, useMemo } from 'react';
import { resolveToolStatus } from '@/lib/toolStatus';
import {
  AlertCircle,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Copy,
  GitBranch,
  Lightbulb,
  Loader2,
  MessageSquare,
  Pencil,
  RefreshCw,
  ShieldAlert,
  Wrench,
  X,
  XCircle,
} from 'lucide-react';
import { loadChatAttachment, type ChatMessage, type ImageAttachment, type ToolCallSummary } from '@/api/chat';
import { streamApproveApproval, rejectApproval, getApproval, type UserInputAnswer } from '@/api/approvals';
import { emitChatLive } from '@/lib/chatLiveBus';
import { cn } from '@/lib/cn';
import { isConfigDraftConfirmationMessage } from '@/lib/configDraftConfirmation';
import { parseFollowups } from '@/lib/followups';
import { useI18n } from '@/i18n/locale';
import { useTimeout } from '@/lib/useTimeout';
import { Button, Chip, ChoiceChip, CodeBlock, DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, Input, Label, RowActionIcon, StatusPill, Textarea } from '@/components/ui';
import { Modal } from '@/components/Modal';
import { RejectApprovalDialog } from '@/components/RejectApprovalDialog';
import { ThinkingPanel } from '@/components/ThinkingPanel';
import { SearchResults, collectToolSources, extractSources } from '@/components/SourcesBlock';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';import { InterpreterArtifacts, type InterpreterArtifact } from '@/components/InterpreterArtifacts';

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
  /** 建议问题可点：仅会话中最新一条回答且未点过为 true；其余整组禁用 */
  followupsActive?: boolean;
  /** 点击建议问题：填入输入框并立即发送 */
  onUseSuggestion?(text: string): void;
};

export function MessageBubble({
  message,
  sessionId,
  onConfirmConfigDraft,
  actionsDisabled,
  onRegenerate,
  onFork,
  onEditResend,
  followupsActive,
  onUseSuggestion,
}: Props) {
  if ((message.kind === 'tool_card' || message.role === 'tool') && message.tool_call) {
    return <ToolCallBlock call={fromSummary(message.tool_call)} onConfirmConfigDraft={onConfirmConfigDraft} />;
  }
  if (message.role === 'tool') return <ToolBubble message={message} onConfirmConfigDraft={onConfirmConfigDraft} />;
  if (message.role === 'user') {
    return (
      <UserBubble
        message={message}
        sessionId={sessionId}
        actionsDisabled={actionsDisabled}
        onEditResend={onEditResend}
        onRegenerate={onRegenerate}
        onFork={onFork}
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
      followupsActive={followupsActive}
      onUseSuggestion={onUseSuggestion}
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

// 原始错误收集：call.error 是短错误行，完整错误/堆栈在 result 的
// error/traceback/stack 字段里——失败工具要能单独展开看，不与结果混排。
function rawErrorOf(call: { error?: string; result?: unknown }): string | null {
  const parts: string[] = [];
  if (call.error && call.error.trim() !== '') parts.push(call.error);
  const result =
    call.result && typeof call.result === 'object' && !Array.isArray(call.result)
      ? (call.result as Record<string, unknown>)
      : undefined;
  if (result) {
    for (const key of ['error', 'traceback', 'stack']) {
      const value = result[key];
      if (typeof value === 'string' && value.trim() !== '') {
        if (!parts.includes(value)) parts.push(value);
      } else if (value && typeof value === 'object') {
        const text = JSON.stringify(value, null, 2);
        if (!parts.includes(text)) parts.push(text);
      }
    }
  }
  return parts.length > 0 ? parts.join('\n\n') : null;
}

function CopyButton({ text }: { text: string }) {
  const { tr } = useI18n();
  const [state, setState] = useState<'idle' | 'copied' | 'error'>('idle');
  const copyTimer = useTimeout();

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setState('copied');
    } catch {
      setState('error');
    }
    copyTimer(() => setState('idle'), 1500);
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
          className={cn('text-[10px]', state === 'error' ? 'text-danger' : 'text-zinc-500')}
        >
          {state === 'error' ? tr('复制失败', 'Copy failed') : tr('已复制', 'Copied')}
        </span>
      )}
    </>
  );
}

// 重新生成与分叉都以用户消息为锚点：前者原样重发这条提问，
// 后者把到此为止的历史复制到新会话，不带上助理的回答。
function UserBubble({ message, sessionId, actionsDisabled, onEditResend, onRegenerate, onFork }: Props) {
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

function AssistantBubble({ message, onConfirmConfigDraft, followupsActive, onUseSuggestion }: Props) {
  // Codex-style: no rounded card around assistant prose. Render markdown
  // flush against the column so headings/lists/code blocks read like a
  // document. Tool calls (when attached) appear as their own rows inside
  // the same column, matching the doc-card aesthetic.
  // 流式只渲染已到正文；底部 ThinkingIndicator 表示进行中，不在气泡内再叠三点。
  const { tr } = useI18n();
  // 建议问题块是界面元数据：剥离出正文，单独渲染成可点 chip 组
  const { body, questions } = useMemo(() => parseFollowups(message.content ?? ''), [message.content]);
  // 记住被点的那条并高亮「已提问」；点一次即发问，整组随即失效
  const [usedQuestion, setUsedQuestion] = useState<string | null>(null);
  // 本回答用到的检索结果统一收口到气泡底部一块，工具卡内与卡下都不重复列
  const sources = collectToolSources(message.tool_calls);
  const stats = message.stats ?? null;
  // 回合未收口（pending）时建议先随流渐进露出但禁点，写完才放开交互
  const followupsDisabled =
    usedQuestion !== null || followupsActive === false || onUseSuggestion === undefined || message.pending === true;
  const fmtTokens = (n: number) => n.toLocaleString('en-US');
  return (
    <div className="flex flex-col items-stretch gap-2">
      {body !== '' && (
        <div className="md-body text-zinc-100">
          {/* 图表由 run_python 的 chart() 产出物渲染，正文只承载文字 */}
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
        </div>
      )}
      {message.tool_calls?.map((tc, i) => (
        <ToolCallBlock
          key={`${tc.name}-${i}`}
          call={tc}
          showSearchResults={false}
          onConfirmConfigDraft={onConfirmConfigDraft}
        />
      ))}
      {sources.length > 0 && <SearchResults sources={sources} />}
      {questions.length > 0 && (
        // 建议组是可点入口而非消息气泡：独立面板与用户后续提问明显区分
        <div className="flex flex-col gap-2 rounded-lg border border-zinc-800/60 bg-zinc-900/40 px-3.5 py-2.5">
          <div className="flex items-center gap-2">
            <span className="grid h-6 w-6 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-indigo-500/25 via-indigo-500/10 to-transparent text-indigo-300 ring-1 ring-inset ring-indigo-500/20">
              <Lightbulb size={12} />
            </span>
            <span className="text-[11px] font-medium text-zinc-300">{tr('猜你想问', 'You may want to ask')}</span>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            {questions.map((q) => {
              const used = usedQuestion === q;
              return (
                <button
                  key={q}
                  type="button"
                  disabled={followupsDisabled}
                  onClick={() => {
                    setUsedQuestion(q);
                    onUseSuggestion?.(q);
                  }}
                  className={cn(
                    'inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-left text-[11.5px] leading-none transition-colors',
                    used
                      ? // 被点的那条：高亮 + 对勾 + 「已提问」，一眼看出选了哪个
                        'border-indigo-500/50 bg-indigo-500/15 text-indigo-200'
                      : 'border-zinc-800/60 bg-zinc-900/50 text-zinc-300 hover:border-indigo-500/40 hover:bg-indigo-500/10 hover:text-indigo-200',
                    !used &&
                      followupsDisabled &&
                      'cursor-not-allowed border-zinc-800/60 bg-zinc-900/40 text-zinc-600 opacity-60 hover:border-zinc-800/60 hover:bg-zinc-900/40 hover:text-zinc-600',
                  )}
                >
                  {used && <Check size={11} className="shrink-0" />}
                  <span>{q}</span>
                  {used && <span className="shrink-0 text-[10px] text-indigo-300/70">{tr('已提问', 'Asked')}</span>}
                </button>
              );
            })}
          </div>
        </div>
      )}
      {stats != null && Object.keys(stats).length > 0 ? (
        <div
          className="ml-auto flex items-center gap-1.5 text-[10.5px] text-zinc-500"
          title={
            stats?.total_tokens !== undefined
              ? `${tr('总计', 'total')} ${fmtTokens(stats.total_tokens)} tokens`
              : undefined
          }
        >
          {stats?.duration_ms !== undefined && (
            <span>{tr(`耗时 ${formatDuration(stats.duration_ms)}`, formatDuration(stats.duration_ms))}</span>
          )}
          {stats?.duration_ms !== undefined && (stats.prompt_tokens !== undefined || stats.completion_tokens !== undefined) && <span>·</span>}
          {stats?.prompt_tokens !== undefined && stats.completion_tokens !== undefined ? (
            <span>
              {tr(
                `输入 ${fmtTokens(stats.prompt_tokens)} / 输出 ${fmtTokens(stats.completion_tokens)} tokens`,
                `in ${fmtTokens(stats.prompt_tokens)} / out ${fmtTokens(stats.completion_tokens)} tokens`,
              )}
            </span>
          ) : stats?.prompt_tokens !== undefined ? (
            <span>{tr(`输入 ${fmtTokens(stats.prompt_tokens)} tokens`, `in ${fmtTokens(stats.prompt_tokens)} tokens`)}</span>
          ) : stats?.completion_tokens !== undefined ? (
            <span>{tr(`输出 ${fmtTokens(stats.completion_tokens)} tokens`, `out ${fmtTokens(stats.completion_tokens)} tokens`)}</span>
          ) : stats?.total_tokens !== undefined ? (
            <span>{fmtTokens(stats.total_tokens)} tokens</span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function ToolBubble({ message, onConfirmConfigDraft }: Props) {
  // History-reload path: the message persisted by the agent loop only
  // carries the tool name + JSON result string. We don't have args for
  // these (would need to join chat_tool_calls); show what we have.
  const result = message.content ? safeParse(message.content) : undefined;
  const rec =
    result && typeof result === 'object' && !Array.isArray(result)
      ? (result as Record<string, unknown>)
      : undefined;
  return (
    <ToolCallBlock
      onConfirmConfigDraft={onConfirmConfigDraft}
      call={{
        name: message.tool_name ?? 'tool',
        // 历史消息没有执行状态，仅从明确的结果字段恢复；错误/耗时同源恢复
        result,
        error: typeof rec?.error === 'string' ? rec.error : undefined,
        duration_ms: typeof rec?.duration_ms === 'number' ? rec.duration_ms : undefined,
      }}
    />
  );
}

/** 从 run_python 的返回结果里取产出物；非该工具或缺字段时返回空数组。 */
function interpreterArtifactsOf(call: { name: string; result?: unknown }): InterpreterArtifact[] {
  if (call.name !== 'run_python' || typeof call.result !== 'object' || call.result === null) return [];
  const artifacts = (call.result as { artifacts?: unknown }).artifacts;
  if (!Array.isArray(artifacts)) return [];
  return artifacts.filter(
    (item): item is InterpreterArtifact => typeof item === 'object' && item !== null,
  );
}

/**
 * 工具调用行：通用卡片保持折叠输入/输出的一致交互；
 * 解释器产出物与检索结果都渲染在卡片外的对话流里（与 run_python 口径一致）。
 */
function ToolCallBlock({
  call,
  onConfirmConfigDraft,
  showSearchResults = true,
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
  /** 气泡已聚合检索结果时置 false，避免同一批结果渲染两次。 */
  showSearchResults?: boolean;
}) {
  const artifacts = interpreterArtifactsOf(call);
  const searchResults = showSearchResults ? extractSources(call.name, call.result) : [];
  return (
    <div className="flex w-full flex-col gap-2">
      <ToolCallSummaryBlock
        call={call}
        onConfirmConfigDraft={onConfirmConfigDraft}
      />
      {artifacts.length > 0 && <InterpreterArtifacts artifacts={artifacts} />}
      <SearchResults sources={searchResults} />
    </div>
  );
}

function ToolCallSummaryBlock({
  call,
  onConfirmConfigDraft,
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
}) {
  const { tr } = useI18n();
  const [open, setOpen] = useState(false);
  const status = resolveToolStatus(call);
  const isPending = status === 'pending';
  const isError = status === 'error' || status === 'timeout' || !!call.error;
  const rawError = isError ? rawErrorOf(call) : null;
  const hint = argSummary(call.arguments);
  // 工具结果 status=pending_input 时，在会话内渲染澄清提问卡。
  const input = pendingInput(call.result);
  if (input) {
    return <UserInputCard approvalID={input.id} question={input.question} options={input.options} fields={input.fields} />;
  }
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
        isError ? 'border-danger/30' : 'border-zinc-800/60',
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
          <Chip dense className="font-sans">host#{call.device_id}</Chip>
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
      {/* 失败工具的错误预览：折叠态也露出一行，不用先展开才知道为什么失败 */}
      {rawError && (
        <div className="border-t border-danger/20 bg-danger/5 px-3 py-1.5">
          <p className="line-clamp-2 whitespace-pre-wrap break-words text-[11px] leading-4 text-danger">
            {rawError}
          </p>
        </div>
      )}
      {configDraft && (
        <ConfigDraftCard draft={configDraft} onConfirm={onConfirmConfigDraft} />
      )}
      {open && (
        <div className="border-t border-zinc-800/60 bg-zinc-950/40 px-3 py-2">
          {rawError && (
            <div className="mb-2">
              <div className="mb-1 text-[11px] uppercase tracking-wide text-danger">{tr('原始错误', 'Raw error')}</div>
              <CodeBlock maxHeightClassName="max-h-48">{rawError}</CodeBlock>
            </div>
          )}
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
          {!rawError && call.result === undefined && isPending && (
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
            <div className="space-y-1 text-[11px] leading-5 text-warn">
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
    return <Loader2 size={13} className="animate-spin text-info" />;
  }
  if (status === 'error' || status === 'timeout') {
    return <AlertCircle size={13} className="text-danger" />;
  }
  if (status === 'success') return <CheckCircle2 size={13} className="text-ok" />;
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

// --- 澄清提问卡 ---

/** ask_user 的表单项：key 用于回传，label/placeholder 展示给用户。 */
export type AskField = { key: string; label: string; placeholder?: string; required?: boolean };

export type AskDisplay = { question: string; options: string[]; fields: AskField[] };

/** 从 ask_user 实参/信封里整理提问规格；模型实参不可信，防御式清洗。 */
export function askDisplayFromArgs(raw: unknown): AskDisplay {
  const r = raw && typeof raw === 'object' && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
  const question = typeof r.question === 'string' ? r.question : '';
  const options = Array.isArray(r.options)
    ? r.options.filter((o): o is string => typeof o === 'string' && o !== '')
    : [];
  const fields: AskField[] = [];
  if (Array.isArray(r.fields)) {
    for (const f of r.fields) {
      if (f === null || typeof f !== 'object' || Array.isArray(f)) continue;
      const row = f as Record<string, unknown>;
      if (typeof row.key !== 'string' || row.key === '') continue;
      const field: AskField = {
        key: row.key,
        label: typeof row.label === 'string' && row.label !== '' ? row.label : row.key,
      };
      if (typeof row.placeholder === 'string' && row.placeholder !== '') field.placeholder = row.placeholder;
      if (row.required === true) field.required = true;
      fields.push(field);
    }
  }
  return { question, options, fields };
}

// pendingInput returns the ask metadata when a tool result is the
// ask_user "pending_input" envelope, else null.
function pendingInput(result: unknown): (AskDisplay & { id: string }) | null {
  if (result && typeof result === 'object') {
    const r = result as Record<string, unknown>;
    if (r.status === 'pending_input' && typeof r.approval_id === 'string') {
      return { id: r.approval_id, ...askDisplayFromArgs(r) };
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

  // 批准直接执行，不再二次确认弹窗；trustScope 决定本会话免审范围（缺省仅本次）。
  const approve = async (trustScope?: 'tool' | 'all') => {
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
        { trustScope },
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
        <ShieldAlert size={13} className="text-warn" />
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
              <Chip key={c} dense className="font-sans">{c}</Chip>
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
          <div className="flex items-center gap-2">
            {/* 主按钮 = 仅本次批准；箭头下拉 = 本会话免审范围（此工具 / 所有工具） */}
            <div className="flex items-center">
              <Button
                variant="primary"
                size="sm"
                type="button"
                className="rounded-r-none"
                title={tr('仅本次批准，下次调用仍会询问', 'Approve once; the next call will ask again')}
                onClick={() => void approve()}
              >
                <Check size={12} />
                {tr('批准并执行', 'Approve & run')}
              </Button>
              <DropdownMenu>
                <DropdownMenuTrigger
                  render={
                    <Button
                      variant="primary"
                      size="sm"
                      className="rounded-l-none border-l border-white/20 px-1"
                      aria-label={tr('批准选项', 'Approve options')}
                    />
                  }
                >
                  <ChevronDown size={12} aria-hidden />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem
                    title={tr('本会话内此工具的后续调用直接执行', 'Later calls of this tool run without asking in this conversation')}
                    onClick={() => void approve('tool')}
                  >
                    {tr('批准并在本会话中信任此工具', 'Approve and trust this tool in this conversation')}
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    title={tr('本会话内所有需确认工具直接执行；澄清提问仍会询问', 'All confirmation-gated tools run without asking in this conversation; agent questions are still asked')}
                    onClick={() => void approve('all')}
                  >
                    {tr('批准并在本会话中信任所有工具', 'Approve and trust all tools in this conversation')}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
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
        {state === 'error' && <div className="break-all text-danger">{errText}</div>}
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

// UserInputCard — 澄清提问卡：让用户选择选项或填写信息，
// 提交后答案随审批续跑回流智能体（ToolConfirmation.payload 通道）。
function UserInputCard({
  approvalID,
  question,
  options,
  fields,
}: {
  approvalID: string;
  question: string;
  options: string[];
  fields: AskField[];
}) {
  const { tr } = useI18n();
  const [state, setState] = useState<'loading' | 'idle' | 'busy' | 'done' | 'rejected' | 'error' | 'stale'>('loading');
  const [ask, setAsk] = useState<AskDisplay>({ question, options, fields });
  const [answered, setAnswered] = useState<UserInputAnswer | null>(null);
  const [errText, setErrText] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [text, setText] = useState('');

  // 与审批页权威状态对齐：mount 拉一次；idle 时短轮询，跨页提交后能切到已答态。
  useEffect(() => {
    let alive = true;
    let pollTimer: number | undefined;

    const applyPayload = (a: Awaited<ReturnType<typeof getApproval>>) => {
      const p = JSON.parse(a.payload) as { arguments?: unknown; answer?: unknown };
      if (p.arguments !== undefined && typeof p.arguments === 'object') {
        setAsk(askDisplayFromArgs(p.arguments));
      }
      if (p.answer && typeof p.answer === 'object' && !Array.isArray(p.answer)) {
        const ans = p.answer as Record<string, unknown>;
        setAnswered({
          selected: typeof ans.selected === 'string' ? ans.selected : null,
          values:
            ans.values && typeof ans.values === 'object' && !Array.isArray(ans.values)
              ? (ans.values as Record<string, string>)
              : {},
          text: typeof ans.text === 'string' ? ans.text : '',
        });
      }
    };

    const applyStatus = (a: Awaited<ReturnType<typeof getApproval>>, notify: boolean) => {
      if (a.status === 'executed') {
        setState('done');
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

  // 提交回答直接续跑，不再二次确认弹窗。
  const submit = async () => {
    // 必填校验：候选必选、必填字段非空、纯文本模式必填
    if (ask.options.length > 0 && selected === null) {
      setErrText(tr('请选择一个选项', 'Pick one option'));
      return;
    }
    for (const f of ask.fields) {
      if (f.required === true && (values[f.key] ?? '').trim() === '') {
        setErrText(tr(`请填写「${f.label}」`, `Fill in "${f.label}"`));
        return;
      }
    }
    if (ask.options.length === 0 && ask.fields.length === 0 && text.trim() === '') {
      setErrText(tr('请填写回答', 'Type your answer'));
      return;
    }
    setState('busy');
    setErrText('');
    emitChatLive({ type: 'approval_decided', approvalId: approvalID });
    emitChatLive({ type: 'busy', busy: true });
    const answer: UserInputAnswer = {
      selected: ask.options.length > 0 ? selected : null,
      values,
      text: text.trim(),
    };
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
        { answer },
      );
      if (a === null) {
        throw new Error('审批流未返回 approval_done');
      }
      setAnswered(answer);
      if (a.status === 'failed') {
        setState('error');
        setErrText(a.result ?? 'failed');
      } else {
        setState('done');
      }
      requestChatRefetch();
    } catch (e) {
      setState('error');
      setErrText((e as Error).message);
    } finally {
      emitChatLive({ type: 'busy', busy: false });
    }
  };

  const skip = async () => {
    setState('busy');
    emitChatLive({ type: 'approval_decided', approvalId: approvalID });
    try {
      await rejectApproval(approvalID, tr('用户跳过该问题', 'User skipped the question'));
      setState('rejected');
      requestChatRefetch();
    } catch (e) {
      setState('idle');
      setErrText((e as Error).message);
    }
  };

  const pill: { tone: 'warn' | 'info' | 'ok' | 'danger' | 'neutral'; label: string; pulse?: boolean } =
    state === 'done'
      ? { tone: 'ok', label: tr('已提交', 'Submitted') }
      : state === 'rejected'
        ? { tone: 'neutral', label: tr('已跳过', 'Skipped') }
        : state === 'error'
          ? { tone: 'danger', label: tr('提交失败', 'Failed') }
          : state === 'busy'
            ? { tone: 'info', label: tr('提交中', 'Submitting'), pulse: true }
            : state === 'stale'
              ? { tone: 'neutral', label: tr('已失效', 'Expired') }
              : state === 'loading'
                ? { tone: 'neutral', label: tr('加载中', 'Loading') }
                : { tone: 'warn', label: tr('待回答', 'Awaiting answer') };

  return (
    <div className="w-full overflow-hidden rounded-lg border border-zinc-800/60 bg-zinc-900/40 text-xs">
      <div className="flex items-center gap-2 border-b border-zinc-800/60 px-3 py-2">
        <MessageSquare size={13} className="text-info" />
        <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium text-zinc-200">
          {tr('需要你补充信息', 'Needs your input')}
        </span>
        <StatusPill tone={pill.tone} label={pill.label} pulse={pill.pulse} className="shrink-0" />
      </div>
      <div className="px-3 pb-2.5 pt-2">
        <p className="mb-2 text-[12.5px] leading-relaxed text-zinc-100">
          {ask.question || tr('(智能体向你提问)', '(the agent has a question)')}
        </p>
        {state === 'loading' && (
          <div className="flex items-center gap-1.5 text-zinc-500">
            <Loader2 size={12} className="animate-spin" />
            {tr('加载提问状态…', 'Loading…')}
          </div>
        )}
        {state === 'stale' && (
          <div className="text-zinc-500">
            {tr('该提问已失效或已处理，请前往「待确认」页查看。', 'This prompt is gone or already handled — see the Approvals page.')}
          </div>
        )}
        {state === 'idle' && (
          <div className="space-y-2">
            {ask.options.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5">
                {ask.options.map((o) => (
                  <ChoiceChip
                    key={o}
                    active={selected === o}
                    label={o}
                    onClick={() => {
                      setSelected(o);
                      setErrText('');
                    }}
                  />
                ))}
              </div>
            )}
            {ask.fields.map((f) => (
              <div key={f.key} className="space-y-1">
                <Label className="text-[11px] text-zinc-400">
                  {f.label}
                  {f.required === true && <span className="text-warn"> *</span>}
                </Label>
                <Input
                  value={values[f.key] ?? ''}
                  placeholder={f.placeholder ?? ''}
                  onChange={(e) => {
                    setValues((prev) => ({ ...prev, [f.key]: e.target.value }));
                    setErrText('');
                  }}
                />
              </div>
            ))}
            {ask.options.length === 0 && ask.fields.length === 0 && (
              <Textarea
                value={text}
                placeholder={tr('输入你的回答…', 'Type your answer…')}
                onChange={(e) => {
                  setText(e.target.value);
                  setErrText('');
                }}
              />
            )}
            {errText !== '' && <div className="break-all text-danger">{errText}</div>}
            <div className="flex gap-2">
              <Button variant="primary" size="sm" type="button" onClick={() => void submit()}>
                <Check size={12} />
                {tr('提交', 'Submit')}
              </Button>
              <Button variant="dangerGhost" size="sm" type="button" onClick={() => void skip()}>
                <X size={12} />
                {tr('跳过', 'Skip')}
              </Button>
            </div>
          </div>
        )}
        {state === 'busy' && (
          <div className="flex items-center gap-1.5 text-zinc-400">
            <Loader2 size={12} className="animate-spin" />
            {tr('提交中…', 'Submitting…')}
          </div>
        )}
        {state === 'rejected' && (
          <div className="text-zinc-500">{tr('已跳过，智能体会继续但没有你的回答', 'Skipped — the agent continues without your answer')}</div>
        )}
        {state === 'error' && <div className="break-all text-danger">{errText}</div>}
        {state === 'done' && (
          <div className="space-y-1 text-zinc-300">
            <div className="text-[11px] text-zinc-500">{tr('你的回答', 'Your answer')}</div>
            {answered === null ? (
              <div className="text-zinc-500">{tr('已提交', 'Submitted')}</div>
            ) : (
              <>
                {answered.selected !== null && <div>{tr('选择：', 'Picked: ')}{answered.selected}</div>}
                {Object.entries(answered.values).map(([k, v]) => (
                  <div key={k}>{k}：{v}</div>
                ))}
                {answered.text !== '' && <div>{answered.text}</div>}
              </>
            )}
          </div>
        )}
      </div>
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
