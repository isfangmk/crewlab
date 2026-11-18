import type { AssistantPage } from '@/lib/assistantPage';
import { ApiError, request } from './client';
import { readSseFrames, type SseFrame } from './sse';
import { getToken } from '@/store/auth';
import { errorText } from '@/lib/errors';

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

/** 回答统计（耗时 / token）；缺失键不显示，禁止假 0。 */
export type MessageStats = {
  duration_ms?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
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
  /** 运行结束的最后一条 assistant 气泡携带；历史回放走同一字段 */
  stats?: MessageStats | null;
  /** 插话打断时写入的半句回复；前端据此标注「已打断」，避免误读为模型出错 */
  interrupted?: boolean;
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
  // 「思考 / 联网搜索」开关（随会话持久化，换页面打开即同步）
  thinking_enabled?: boolean;
  web_search_enabled?: boolean;
  // 列表摘要：正文消息数（仅 user/assistant，不含工具卡）与最近一条正文（后端已截断）
  message_count?: number;
  // 对话轮数：用户提问次数（一问一答算一轮）
  turn_count?: number;
  // 工具 / 技能调用次数：来自观测的回合记账
  tool_call_count?: number;
  skill_call_count?: number;
  // 最后一条消息时间：发消息不会 touch 会话行，和 updated_at 不是一回事
  last_message_at?: string | null;
  last_message_preview?: string | null;
  // 运行信号：累计 tokens / 最近一回合状态（running/ok/error/stopped）/ 待审批数 / 待回答提问数
  total_tokens?: number | null;
  last_turn_status?: string | null;
  pending_approvals?: number;
  pending_inputs?: number;
  // 图片附件数、产出物个数与「存在未过期的只读分享」
  attachment_count?: number;
  artifact_count?: number;
  shared?: boolean;
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
  /** 本次运行耗时（毫秒）；停止路径可能缺失 */
  duration_ms?: number | null;
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

// 「思考 / 联网搜索」开关写回会话数据（每个会话各自记住）
export function updateSessionPrefs(
  sessionId: string,
  prefs: { thinkingEnabled?: boolean; webSearchEnabled?: boolean },
) {
  const body: { thinking_enabled?: boolean; web_search_enabled?: boolean } = {};
  if (typeof prefs.thinkingEnabled === 'boolean') body.thinking_enabled = prefs.thinkingEnabled;
  if (typeof prefs.webSearchEnabled === 'boolean') body.web_search_enabled = prefs.webSearchEnabled;
  return request<ChatSession>(
    'PATCH',
    `/chat/sessions/${encodeURIComponent(sessionId)}/prefs`,
    body,
  );
}

export function forkSession(sessionId: string, upToMessageId: string) {
  return request<ChatSession>(
    'POST',
    `/chat/sessions/${encodeURIComponent(sessionId)}/fork`,
    { up_to_message_id: upToMessageId },
  );
}

export type SessionExportFormat = 'markdown' | 'json';

// 会话导出：markdown 为文档文本；json 为结构化消息（与只读分享同出参形状）。
export async function exportSession(
  sessionId: string,
  format: SessionExportFormat,
): Promise<string> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/export?format=${format}`,
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
  // 「思考 / 联网搜索」开关初值（首页开关随建会话落库）
  thinkingEnabled?: boolean;
  webSearchEnabled?: boolean;
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
    thinking_enabled: boolean;
    web_search_enabled: boolean;
  } = {
    title: input.title,
    agent_id: input.agent_id,
    provider: input.provider,
    model: input.model,
    thinking_enabled: input.thinkingEnabled ?? false,
    web_search_enabled: input.webSearchEnabled ?? false,
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

// ---------------------------------------------------------------------------
// 排队消息（插话/消息队列）
// ---------------------------------------------------------------------------

/** 会话队列中的一条待发消息（摘要展示用，正文可能已被后端截断）。 */
export type QueueItem = {
  item_id: string;
  content: string;
  position: number;
  created_at?: string;
  /** 是否已标记为插话（出队时优先执行）。 */
  interrupt?: boolean;
};

/** 入队载荷：正文必填，其余为当前回合的模型/开关/附件配置。 */
export type QueueAddInput = {
  content: string;
  provider?: string;
  model?: string;
  thinking_enabled?: boolean;
  web_search_enabled?: boolean;
  attachment_ids?: string[];
  mentions?: Mention[];
};

/** 回答中把一条消息排进会话队列，不打断当前回合。 */
export function addQueueItem(sessionId: string | number, input: QueueAddInput) {
  return request<{ session_id: string; item_id: string; position: number }>(
    'POST',
    `/chat/sessions/${encodeURIComponent(String(sessionId))}/queue`,
    input,
  );
}

/** 拉取会话当前队列。 */
export function getQueue(sessionId: string | number, signal?: AbortSignal) {
  return request<{ items: QueueItem[]; total: number }>(
    'GET',
    `/chat/sessions/${encodeURIComponent(String(sessionId))}/queue`,
    undefined,
    { signal },
  );
}

/** 删除队列中的一条消息。 */
export function deleteQueueItem(sessionId: string | number, itemId: string) {
  return request<{ deleted: string }>(
    'DELETE',
    `/chat/sessions/${encodeURIComponent(String(sessionId))}/queue/${encodeURIComponent(itemId)}`,
  );
}

/** 原地修改队列项正文；位置不变，轮到它时按新正文执行。 */
export function updateQueueItem(sessionId: string | number, itemId: string, content: string) {
  return request<{ session_id: string; item_id: string; content: string }>(
    'PATCH',
    `/chat/sessions/${encodeURIComponent(String(sessionId))}/queue/${encodeURIComponent(itemId)}`,
    { content },
  );
}

/** 插话：标记该队列项优先，并打断当前回合让它立即执行。 */
export function interjectQueueItem(sessionId: string | number, itemId: string) {
  return request<{ session_id: string; item_id: string; interrupted: boolean }>(
    'POST',
    `/chat/sessions/${encodeURIComponent(String(sessionId))}/queue/${encodeURIComponent(itemId)}/interject`,
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
// tool_call_id 用于挂到同一条工具调用卡片上；kind=user_input 是澄清提问卡。
export type ApprovalPendingStreamEvent = {
  approval_id: string;
  tool_call_id?: string;
  kind?: string;
  tool_name?: string;
  command?: string;
  credentials?: string[];
  /** 工具实参原文：澄清提问卡的 question/options/fields 从这里取。 */
  arguments?: Record<string, unknown>;
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
  /** 帧流断线、准备第 N 次重连时回调（N 从 1 起）；UI 可据此显示"连接中断，正在恢复…"。 */
  onReconnecting?: (attempt: number) => void;
};

/** 单帧处理：打字机让出主线程 + 事件分发。 */
async function handleStreamFrame(frame: SseFrame, cbs: StreamCallbacks): Promise<void> {
  if (frame.event === 'assistant_delta' || frame.event === 'thinking_delta') {
    // 打字机按帧渲染：让出一帧再收下一帧，避免高频 delta 把主线程占满。
    // 隐藏/离屏页 rAF 不触发，必须用定时器兜底，否则读帧循环会饿死、流式永久卡住。
    await new Promise<void>((resolve) => {
      let settled = false;
      const finish = () => {
        if (!settled) {
          settled = true;
          resolve();
        }
      };
      requestAnimationFrame(() => finish());
      setTimeout(finish, 16);
    });
  }
  dispatchFrame(frame.event, frame.data, cbs);
}

/** 读取 SSE 响应体并按帧回调；审批续跑与聊天流共用。 */
export async function consumeSseResponse(res: Response, cbs: StreamCallbacks): Promise<void> {
  await readSseFrames(res, async (frame) => handleStreamFrame(frame, cbs));
}

function isTerminalFrame(frame: SseFrame): boolean {
  return frame.event === 'done' || frame.event === 'error';
}

// streamMessage opens an SSE connection to the agent loop and dispatches
// events as they arrive. The promise resolves once the server sends a
// `done` frame (or rejects on transport / parse error). Pass an
// AbortSignal to cancel mid-flight; the server will see a closed
// connection and the agent loop's per-tool timeouts will eventually
// release any in-flight tunnel calls.
/** 查会话当前进行中的回合；第二个标签页打开时据此 attach 续流。 */
export async function getActiveTurn(
  sessionId: string | number,
): Promise<{ turn_id: string | null }> {
  return request(
    'GET',
    `/chat/sessions/${encodeURIComponent(String(sessionId))}/active-turn`,
  );
}

/** 读取某回合的帧流（供 attach 用；返回原始 Response，帧解析交给调用方）。 */
export async function openTurnEventStream(
  sessionId: string | number,
  turnId: string,
  signal?: AbortSignal,
): Promise<Response> {
  const base = `/api/v1/chat/sessions/${encodeURIComponent(String(sessionId))}`;
  const headers: Record<string, string> = { Accept: 'text/event-stream' };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetch(`${base}/events?turn_id=${encodeURIComponent(turnId)}`, {
    headers,
    signal,
  });
}

/**
 * 发起流式回合：POST 只登记回合拿 turn_id，真正的帧走 GET .../events 读 Redis 帧流。
 * 帧不随 POST 返回，因此第二个标签页拿同一个 turn_id 也能补发+续推同一回合。
 * 签名与回调对调用方不变：拿到 done 帧才 resolve。
 */
export async function streamMessage(
  sessionId: string | number,
  content: string,
  cbs: StreamCallbacks,
  opts: SendOptions,
  signal?: AbortSignal,
): Promise<void> {
  const base = `/api/v1/chat/sessions/${encodeURIComponent(String(sessionId))}`;
  const headers: Record<string, string> = {
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

  // 1) 发起回合：服务端后台生产，立即返回 turn_id
  const startRes = await fetch(`${base}/messages/stream`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal,
  });
  if (!startRes.ok) {
    const detail = await startRes.text().catch(() => '');
    throw new Error(`发起对话失败 HTTP ${startRes.status}: ${detail.slice(0, 200)}`);
  }
  const started = (await startRes.json()) as { turn_id: string };
  if (!started.turn_id) throw new Error('发起对话未返回 turn_id');

  // 2) 订阅该回合帧流（与其他标签页共享，互不抢帧）
  await consumeTurnEvents(base, started.turn_id, headers, cbs, signal);
}

/**
 * GET events 帧流 + 断线续传。
 *
 * 服务端按 Redis Stream 存帧，支持 ?last_id 从断点补发；连接被中间设备掐断时
 * 本函数按退避重连，带上已消费帧的 last_id，既不丢帧也不重复分发终态。
 * 收到 done/error 立即停止，不再重连（终态是回合唯一收口）。
 */
async function consumeTurnEvents(
  base: string,
  turnId: string,
  headers: Record<string, string>,
  cbs: StreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const sseHeaders = { ...headers, Accept: 'text/event-stream' };
  let lastId = '';
  let attempt = 0;
  const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

  for (;;) {
    if (signal?.aborted) return;
    const url = new URL(`${base}/events`, window.location.origin);
    url.searchParams.set('turn_id', turnId);
    if (lastId) url.searchParams.set('last_id', lastId);

    let res: Response;
    try {
      res = await fetch(url.toString(), { headers: sseHeaders, signal });
    } catch (err) {
      // fetch 本身失败（网络抖动 / ECONNRESET）：退避重连，不当作回合错误。
      if (signal?.aborted) return;
      attempt += 1;
      cbs.onReconnecting?.(attempt);
      await sleep(Math.min(1000 * 2 ** (attempt - 1), 30_000));
      continue;
    }
    // 连上即重置退避：一次抖动后健康重连不应让后续退避一直翻倍。
    attempt = 0;
    let finished = false; // 收到 done/error：本回合唯一收口，停止重连

    try {
      await readSseFrames(res, async (frame) => {
        if (frame.id) lastId = frame.id;
        // 终态帧只分发一次：极端情况下断点重连可能重放上一轮的终态帧，
        // 已在 finished 状态时跳过，避免 done/error 回调触发两次。
        if (finished) return;
        await handleStreamFrame(frame, cbs);
        // 不改 readSseFrames 的返回值（false 会触发 reader.cancel，在某些运行环境下
        // 会挂住收尾）；终态由本函数消费后、流自然结束时统一收口。
        if (isTerminalFrame(frame)) finished = true;
      });
    } catch (err) {
      // 拿到响应后的错误（非 2xx / SSE 解析失败）：重试只会重复同一坏状态/坏帧，
      // 直接交给 onError 收口，不无限重连。
      if (signal?.aborted || finished) return;
      cbs.onError?.(err instanceof Error ? err : new Error(String(err)));
      return;
    }
    if (finished || signal?.aborted) return;

    // 连接正常结束但回合未终态（服务端空闲断连 / 中间设备超时）：带 last_id 续传。
    attempt += 1;
    cbs.onReconnecting?.(attempt);
    await sleep(Math.min(1000 * 2 ** (attempt - 1), 30_000));
  }
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
  const base = `/api/v1/chat/sessions/${encodeURIComponent(String(sessionId))}`;
  const startRes = await fetch(`${base}/messages/edit-resend`, {
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
  });
  if (!startRes.ok) {
    const detail = await startRes.text().catch(() => '');
    throw new Error(`编辑重发失败 HTTP ${startRes.status}: ${detail.slice(0, 200)}`);
  }
  const started = (await startRes.json()) as { turn_id: string };
  if (!started.turn_id) throw new Error('编辑重发未返回 turn_id');
  await consumeTurnEvents(base, started.turn_id, headers, cbs, signal);
}

function dispatchFrame(event: string, data: string, cbs: StreamCallbacks) {
  let payload: unknown;
  try {
    payload = JSON.parse(data);
  } catch (e) {
    throw new Error(`SSE JSON 非法: ${errorText(e)}`);
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
