import type { AssistantPage } from '@/lib/assistantPage';
import { ApiError, request } from './client';
import { getToken } from '@/store/auth';

export type ChatRole = 'user' | 'assistant' | 'tool' | 'system';

export type ImageAttachment = {
  id: string;
  name: string;
  mime_type: string;
  size: number;
  created_at?: string;
};

export type ToolCallSummary = {
  id?: string; // server tool_call_id (UUID); absent on history-replay rows
  name: string;
  device_id?: number;
  status: 'pending' | 'success' | 'error' | 'timeout';
  duration_ms?: number;
  error?: string;
  // SSE / 历史回放的工具参数与结果；*_raw 表示服务端未能解析为 JSON。
  arguments?: unknown;
  arguments_raw?: string;
  result?: unknown;
  result_raw?: string;
};

export type ChatMessage = {
  // 服务端消息 id 为 UUID；乐观渲染阶段客户端会先用短 id（如 user-<ts>）。
  id: string;
  role: ChatRole;
  content?: string;
  attachments?: ImageAttachment[];
  tool_call_id?: string;
  tool_name?: string;
  created_at?: string;
  tool_calls?: ToolCallSummary[];
  pending?: boolean;
  // tool_card / tool_intent / thinking（reasoning，与正文分离）
  kind?: 'tool_card' | 'tool_intent' | 'message' | 'thinking';
  tool_call?: ToolCallSummary;
};

export type ChatSession = {
  // 引用了页面快照的会话；没有引用时后端返回 null
  page_context?: AssistantPage | null;
  id: string;
  title: string;
  agent_id?: string | null;
  provider?: string | null;
  model?: string | null;
  created_at?: string;
  updated_at?: string;
  pinned?: boolean;
  archived?: boolean;
  // 列表摘要：正文消息数（仅 user/assistant，不含工具卡）与最近一条正文（后端已截断）
  message_count?: number;
  tool_call_count?: number;
  // 最后一条消息时间：发消息不会 touch 会话行，和 updated_at 不是一回事
  last_message_at?: string | null;
  last_message_preview?: string | null;
  // 运行信号：累计 tokens / 最近一回合状态（running/ok/error/stopped）/ 待审批数
  total_tokens?: number | null;
  last_turn_status?: string | null;
  pending_approvals?: number;
};

export type AssistantMessage = {
  id: string;
  content: string;
  created_at: string;
};

export type Usage = {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
};

export type PostMessageResponse = {
  session_id: string;
  assistant_message: AssistantMessage;
  tool_calls: ToolCallSummary[];
  usage: Usage;
  iterations: number;
};

export type SessionSortBy =
  | 'title'
  | 'model'
  | 'updated_at'
  | 'last_message_at'
  | 'agent_id'
  | 'created_at';
export type SessionSortDir = 'asc' | 'desc';

export function listSessions(params?: {
  q?: string;
  include_archived?: boolean;
  agent_id?: string;
  model?: string;
  updated_from?: string;
  updated_to?: string;
  sort_by?: SessionSortBy;
  sort_dir?: SessionSortDir;
  limit?: number;
  offset?: number;
}) {
  const sp = new URLSearchParams();
  if (params?.q != null && params.q !== '') {
    sp.set('q', params.q);
  }
  if (params?.include_archived === true) {
    sp.set('include_archived', 'true');
  }
  if (params?.agent_id != null && params.agent_id !== '') {
    sp.set('agent_id', params.agent_id);
  }
  if (params?.model != null && params.model !== '') {
    sp.set('model', params.model);
  }
  if (params?.updated_from != null && params.updated_from !== '') {
    sp.set('updated_from', params.updated_from);
  }
  if (params?.updated_to != null && params.updated_to !== '') {
    sp.set('updated_to', params.updated_to);
  }
  if (params?.sort_by != null) {
    sp.set('sort_by', params.sort_by);
  }
  if (params?.sort_dir != null) {
    sp.set('sort_dir', params.sort_dir);
  }
  if (params?.limit != null) {
    sp.set('limit', String(params.limit));
  }
  if (params?.offset != null) {
    sp.set('offset', String(params.offset));
  }
  const qs = sp.toString();
  return request<{ items: ChatSession[]; total: number }>(
    'GET',
    `/chat/sessions${qs ? `?${qs}` : ''}`,
  );
}

// 会话列表的模型筛选项：只返回会话里真实出现过的模型，避免选到查不到结果的值。
export function listSessionModels() {
  return request<{ items: string[] }>('GET', '/chat/sessions/models');
}

export function batchDeleteSessions(ids: string[]) {
  if (ids.length === 0) {
    return Promise.reject(new ApiError('ids 不能为空', 422));
  }
  return request<{ deleted: number }>('POST', '/chat/sessions/batch-delete', { ids });
}

export function getSession(sessionId: string) {
  if (sessionId === '') {
    return Promise.reject(new ApiError('sessionId 不能为空', 400));
  }
  return request<ChatSession>('GET', `/chat/sessions/${encodeURIComponent(sessionId)}`);
}

export function updateSessionFlags(
  sessionId: string,
  flags: { pinned?: boolean; archived?: boolean },
) {
  return request<ChatSession>(
    'PATCH',
    `/chat/sessions/${encodeURIComponent(sessionId)}/flags`,
    flags,
  );
}

export function forkSession(sessionId: string, upToMessageId: string) {
  return request<ChatSession>(
    'POST',
    `/chat/sessions/${encodeURIComponent(sessionId)}/fork`,
    { up_to_message_id: upToMessageId },
  );
}

export async function exportSessionMarkdown(sessionId: string): Promise<string> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/export`,
    { headers },
  );
  if (!res.ok) {
    throw new Error(`导出失败 status=${res.status}`);
  }
  return res.text();
}

export function createSessionShare(sessionId: string, expiresInHours: number) {
  return request<{ token: string; expires_at: string; session_id: string }>(
    'POST',
    `/chat/sessions/${encodeURIComponent(sessionId)}/share`,
    { expires_in_hours: expiresInHours },
  );
}

export function createSession(input: {
  page_context?: AssistantPage;
  title: string;
  agent_id: string;
  provider: string;
  model: string;
}) {
  if (!input.provider || !input.model) {
    throw new Error('创建会话必须指定 provider 与 model');
  }
  if (!input.agent_id) {
    throw new Error('创建会话必须指定 agent_id');
  }
  // 后端 CreateSessionRequest 是 extra="forbid"：
  // 只发它接受的字段，多发一个就 422。
  const body: {
    title: string;
    agent_id: string;
    provider: string;
    model: string;
    page_context?: AssistantPage;
  } = {
    title: input.title,
    agent_id: input.agent_id,
    provider: input.provider,
    model: input.model,
  };
  if (input.page_context) body.page_context = input.page_context;
  return request<ChatSession>('POST', '/chat/sessions', body);
}

export function renameSession(sessionId: string, title: string) {
  return request<void>(
    'PATCH',
    `/chat/sessions/${encodeURIComponent(sessionId)}`,
    { title },
  );
}

export function updateSessionModel(sessionId: string, provider: string, model: string) {
  return request<void>('PUT', `/chat/sessions/${encodeURIComponent(sessionId)}/model`, { provider, model });
}

export function updateSessionAgent(sessionId: string, agentId: string) {
  if (agentId === '') {
    throw new Error('更新会话助理必须指定 agent_id');
  }
  return request<void>('PATCH', `/chat/sessions/${encodeURIComponent(sessionId)}/agent`, {
    agent_id: agentId,
  });
}

// stopSession interrupts the session's in-flight turn server-side (the SPA
// calls this on Esc). Needed because the turn is detached from the request
// ctx (so a refresh doesn't kill it), so closing the stream alone no longer
// cancels it — this is the explicit stop signal.
export function stopSession(sessionId: string | number) {
  return request<{ stopped: boolean }>(
    'POST',
    `/chat/sessions/${encodeURIComponent(String(sessionId))}/stop`,
  );
}

export function getMessages(sessionId: string | number, signal?: AbortSignal) {
  return request<{ items: ChatMessage[]; total: number }>(
    'GET',
    `/chat/sessions/${encodeURIComponent(String(sessionId))}/messages`, undefined, { signal },
  );
}

export async function uploadChatAttachment(sessionId: string | number, file: File): Promise<ImageAttachment> {
  const form = new FormData();
  form.append('file', file);
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`/api/v1/chat/sessions/${encodeURIComponent(String(sessionId))}/attachments`, {
    method: 'POST',
    headers,
    body: form,
  });
  if (!response.ok) {
    throw new ApiError(await response.text(), response.status);
  }
  return response.json() as Promise<ImageAttachment>;
}

export async function loadChatAttachment(sessionId: string | number, attachmentId: string): Promise<string> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`/api/v1/chat/sessions/${encodeURIComponent(String(sessionId))}/attachments/${encodeURIComponent(attachmentId)}`, { headers });
  if (!response.ok) {
    throw new ApiError(await response.text(), response.status);
  }
  return URL.createObjectURL(await response.blob());
}

// @ 提及：输入框选择后随消息提交，后端注入只读上下文（不调工具）。
// 类型：知识库文档 file、MCP Server server。
export type MentionType = 'file' | 'server';

export type Mention = {
  type: MentionType;
  id: string;
  label: string;
};

export type MentionItem = {
  type: MentionType;
  id: string;
  label: string;
  subtitle?: string;
};

export type SendOptions = {
  attachmentIds: string[];
  mentions?: Mention[];
  provider?: string;
  model?: string;
  /** 本回合是否开放 web_search */
  webSearchEnabled: boolean;
  /** 本回合是否开启模型思考 */
  thinkingEnabled: boolean;
  // UI language ('en-US' | 'zh-CN') so the agent answers in it. The
  // personas are Chinese, so without this English-mode users get Chinese.
  locale?: string;
  /** 与乐观用户气泡同一 id，历史拉取时不换 key */
  clientMessageId?: string;
};

// searchMentions powers the @-popover. The popover debounces calls so
// network volume stays low; the server caps results internally.
export function searchMentions(params: { q: string; type?: MentionType; limit?: number }) {
  const qs = new URLSearchParams();
  if (params.q) qs.set('q', params.q);
  if (params.type) qs.set('type', params.type);
  if (params.limit) qs.set('limit', String(params.limit));
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  return request<{ items: MentionItem[] }>('GET', `/aiops/mentions/search${suffix}`);
}

export type LLMProvider = {
  id: string;
  label: string;
  models: string[];
  model?: string;
};

export type ModelSelection = { provider: string; model: string };

export type ModelCatalog = {
  providers: LLMProvider[];
  default: ModelSelection;
};

export function listModels() {
  return request<ModelCatalog>('GET', '/aiops/models');
}

// 硬删除会话及其全部消息。
export function deleteSession(sessionId: string | number) {
  return request<void>('DELETE', `/chat/sessions/${encodeURIComponent(String(sessionId))}`);
}

// ---------------------------------------------------------------------------
// SSE streaming
// ---------------------------------------------------------------------------

export type ToolStreamEvent = {
  tool_call_id: string;
  name: string;
  device_id?: number;
  status: 'pending' | 'success' | 'error' | 'timeout';
  started_at: string;
  ended_at?: string;
  duration_ms: number;
  error?: string;
  arguments?: unknown;
  arguments_raw?: string;
  result?: unknown;
  result_raw?: string;
};

// 工具等待人工审批时的 SSE 帧；前端据此渲染行内批准/拒绝卡。
// tool_call_id 用于挂到同一条工具调用卡片上。
export type ApprovalPendingStreamEvent = {
  approval_id: string;
  tool_call_id?: string;
  kind?: string;
  tool_name?: string;
  command?: string;
  credentials?: string[];
};

// Only a rejection before runtime dispatch guarantees that resubmission is safe.
export class RejectedChatTurnError extends ApiError {}

export type AssistantDeltaStreamEvent = {
  message_id: string;
  delta: string;
  partial?: boolean;
};

export type UserMessageStreamEvent = {
  message_id: string;
  content: string;
};

export type StreamCallbacks = {
  onUserMessage?: (e: UserMessageStreamEvent) => void;
  onAssistantDelta?: (e: AssistantDeltaStreamEvent) => void;
  onThinkingDelta?: (e: AssistantDeltaStreamEvent) => void;
  onToolStart?: (e: ToolStreamEvent) => void;
  onToolEnd?: (e: ToolStreamEvent) => void;
  onApprovalPending?: (e: ApprovalPendingStreamEvent) => void;
  onDone?: (reply: PostMessageResponse) => void;
  onApprovalDone?: (payload: unknown) => void;
  onError?: (err: Error) => void;
};

/** 读取 SSE 响应体并按帧回调；审批续跑与聊天流共用。 */
export async function consumeSseResponse(res: Response, cbs: StreamCallbacks): Promise<void> {
  if (!res.ok || !res.body) {
    let parsed: unknown = null;
    try {
      parsed = await res.json();
    } catch {
      parsed = null;
    }
    let msg = `HTTP ${res.status}`;
    let code: string | undefined;
    if (parsed && typeof parsed === 'object') {
      const obj = parsed as Record<string, unknown>;
      if (typeof obj.error === 'string') msg = obj.error;
      if (typeof obj.code === 'string') code = obj.code;
    }
    throw new ApiError(msg, res.status, code, parsed);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n').replace(/\r/g, '\n');

    let sep: number;
    while ((sep = buf.indexOf('\n\n')) >= 0) {
      const frame = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      const isDelta =
        frame.includes('event: assistant_delta') || frame.includes('event: thinking_delta');
      dispatchFrame(frame, cbs);
      if (isDelta) {
        await new Promise<void>((resolve) => {
          requestAnimationFrame(() => resolve());
        });
      }
    }
  }
  if (buf.trim()) dispatchFrame(buf, cbs);
}

// streamMessage opens an SSE connection to the agent loop and dispatches
// events as they arrive. The promise resolves once the server sends a
// `done` frame (or rejects on transport / parse error). Pass an
// AbortSignal to cancel mid-flight; the server will see a closed
// connection and the agent loop's per-tool timeouts will eventually
// release any in-flight tunnel calls.
export async function streamMessage(
  sessionId: string | number,
  content: string,
  cbs: StreamCallbacks,
  opts: SendOptions,
  signal?: AbortSignal,
): Promise<void> {
  const url = `/api/v1/chat/sessions/${encodeURIComponent(String(sessionId))}/messages/stream`;
  const headers: Record<string, string> = {
    Accept: 'text/event-stream',
    'Content-Type': 'application/json',
  };
  const token = getToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const body: Record<string, unknown> = {
    content,
    thinking_enabled: opts.thinkingEnabled,
    web_search_enabled: opts.webSearchEnabled,
    attachment_ids: opts.attachmentIds,
  };
  if (opts.provider) body.provider = opts.provider;
  if (opts.model) body.model = opts.model;
  if (opts.mentions && opts.mentions.length > 0) body.mentions = opts.mentions;
  if (opts.locale) body.locale = opts.locale;
  if (opts.clientMessageId) body.client_message_id = opts.clientMessageId;

  const res = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal,
  });

  await consumeSseResponse(res, cbs);
}

/** 编辑用户消息并截断后续后重发。 */
export async function streamEditResend(
  sessionId: string | number,
  messageId: string,
  content: string,
  cbs: StreamCallbacks,
  opts: Pick<SendOptions, 'thinkingEnabled' | 'webSearchEnabled' | 'attachmentIds'>,
  signal?: AbortSignal,
): Promise<void> {
  const headers: Record<string, string> = {
    Accept: 'text/event-stream',
    'Content-Type': 'application/json',
  };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(
    `/api/v1/chat/sessions/${encodeURIComponent(String(sessionId))}/messages/edit-resend`,
    {
      method: 'POST',
      headers,
      body: JSON.stringify({
        message_id: messageId,
        content,
        thinking_enabled: opts.thinkingEnabled,
        web_search_enabled: opts.webSearchEnabled,
        attachment_ids: opts.attachmentIds ?? [],
      }),
      signal,
    },
  );
  await consumeSseResponse(res, cbs);
}

function dispatchFrame(raw: string, cbs: StreamCallbacks) {
  let event = 'message';
  const dataLines: string[] = [];
  for (const line of raw.split('\n')) {
    if (!line || line.startsWith(':')) continue;
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return;
  let payload: unknown;
  try {
    payload = JSON.parse(dataLines.join('\n'));
  } catch (e) {
    throw new Error(`SSE JSON 非法: ${e instanceof Error ? e.message : String(e)}`);
  }
  switch (event) {
    case 'user_message':
      cbs.onUserMessage?.(payload as UserMessageStreamEvent);
      break;
    case 'assistant_delta':
      cbs.onAssistantDelta?.(payload as AssistantDeltaStreamEvent);
      break;
    case 'thinking_delta':
      cbs.onThinkingDelta?.(payload as AssistantDeltaStreamEvent);
      break;
    case 'tool_start':
      cbs.onToolStart?.(payload as ToolStreamEvent);
      break;
    case 'tool_end':
      cbs.onToolEnd?.(payload as ToolStreamEvent);
      break;
    case 'approval_pending':
      cbs.onApprovalPending?.(payload as ApprovalPendingStreamEvent);
      break;
    case 'done':
      cbs.onDone?.(payload as PostMessageResponse);
      break;
    case 'approval_done':
      cbs.onApprovalDone?.(payload);
      break;
    case 'error': {
      const obj = payload as { error?: string; code?: string; turn_rejected?: boolean };
      if (typeof obj.error !== 'string' || obj.error === '') {
        throw new Error('SSE error 帧缺少 error 字段');
      }
      const StreamError = obj.turn_rejected === true ? RejectedChatTurnError : ApiError;
      cbs.onError?.(new StreamError(obj.error, 200, obj.code));
      break;
    }
    default:
      throw new Error(`未知 SSE 事件: ${event}`);
  }
}
