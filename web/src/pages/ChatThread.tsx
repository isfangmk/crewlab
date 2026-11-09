import { useAssistantPage } from '@/lib/assistantPage';

import { useCallback, useEffect, useRef, useState } from 'react';

import { useLocation, useParams } from 'react-router-dom';

import { ChatInput, type ModelSelection, type SubmitPayload } from '@/components/ChatInput';

import {

  MessageBubble,

  type ConfigDraftResult,

} from '@/components/MessageBubble';

import { AgentBadge } from '@/components/AgentBadge';

import { PageHeader } from '@/components/ui';

import {

  getMessages,

  listModels,

  stopSession,

  streamMessage,

  updateSessionModel,

  updateSessionAgent,

  uploadChatAttachment,

  type ChatMessage,

  type LLMProvider,

  type Mention,

  type ImageAttachment,

} from '@/api/chat';

import { listAgents, type AgentSummary } from '@/api/agents';

import { listApprovals, type Approval } from '@/api/approvals';
import { CHAT_LIVE_EVENT, type ChatLiveDetail } from '@/lib/chatLiveBus';

import { ApiError } from '@/api/client';

import { invalidateChatSessions, useChatSessions } from '@/store/chatSessions';

import { useModelSelection } from '@/store/modelSelection';

import { useChatPrefs } from '@/store/chatPrefs';

import { usePermissions } from '@/store/me';

import { useI18n } from '@/i18n/locale';

import { buildConfigDraftConfirmMessage, configDraftApplyTool } from '@/lib/configDraftConfirmation';

import {

  getChatTurnController,

  subscribeChatTurns,

  registerChatTurnController,

  unregisterChatTurnController,

} from '@/lib/chatTurnRegistry';



type LocationState = {
  initialPrompt?: string;
  initialAttachments?: File[];
  thinkingEnabled?: boolean;
  webSearchEnabled?: boolean;
} | null;



export default function ChatThreadPage() {

  const { tr, locale } = useI18n();

  const { isViewer } = usePermissions();

  const { sessionId = '' } = useParams<{ sessionId: string }>();

  const location = useLocation();

  const initialPrompt = (location.state as LocationState)?.initialPrompt;

  const initialAttachments = (location.state as LocationState)?.initialAttachments;



  const sessions = useChatSessions((s) => s.sessions);

  const sessionMeta = sessions.find((s) => String(s.id) === String(sessionId));

  const sessionTitle = sessionMeta?.title;

  const sessionAgentID = sessionMeta?.agent_id;



  const [messages, setMessages] = useState<ChatMessage[]>([]);

  const [submitting, setSubmitting] = useState(false);

  const [error, setError] = useState<string | null>(null);

  const [loading, setLoading] = useState(true);

  const scrollerRef = useRef<HTMLDivElement | null>(null);

  const sentInitialRef = useRef(false);

  // abortRef holds the in-flight turn's AbortController so Esc can cancel the

  // stream client-side (instant UI) alongside the server-side stop call.

  const abortRef = useRef<AbortController | null>(null);

  const localStreams = useRef(new WeakSet<AbortController>());

  // React Router reuses this page between /chat/:sessionId routes. Keep the

  // visible session explicit so callbacks from an older stream cannot mutate it.

  const activeSessionRef = useRef(sessionId);

  // stickToBottomRef tracks whether new messages should auto-scroll the

  // viewport down. Default true (fresh thread starts at bottom); flips

  // to false when the user manually scrolls up to read older context,

  // back to true when they scroll back to within 120px of the bottom.

  //

  // Why a ref instead of computing distance inside the auto-scroll

  // effect: when a new message lands, React appends it to the DOM

  // BEFORE the effect runs, so el.scrollHeight has already grown. The

  // post-update distance always reads as large for any reasonably

  // tall new bubble (assistant content, RCA table, etc.) → the

  // distance ≤ 80 guard incorrectly concludes "user scrolled up" and

  // bails. The ref captures the user's intent at scroll time, not at

  // measurement time.

  const stickToBottomRef = useRef(true);



  // Per-session model selection + provider catalog. The catalog is

  // fetched once on mount; the selection persists in component state

  // for the lifetime of the open chat thread (the SPA spec calls this

  // "session-level state"). Empty providers → ChatInput hides the

  // selector entirely.

  const [providers, setProviders] = useState<LLMProvider[]>([]);

  // 会话已绑定模型则用之；未绑定则用模型目录默认值。

  const [catalogDefault, setCatalogDefault] = useState<ModelSelection | null>(null);

  const persistedSessionModel = sessionMeta?.provider && sessionMeta?.model

    ? { provider: sessionMeta.provider, model: sessionMeta.model }

    : null;

  const [pendingSessionModel, setPendingSessionModel] = useState<ModelSelection | null>(null);

  const selectedModel = pendingSessionModel ?? persistedSessionModel ?? catalogDefault;

  // Web-search toggle is per-thread (not per-message): once a user

  // enables it for a topic, every follow-up turn until they disable it

  // also exposes the skill. Defaults ON because SearXNG (default provider)

  // is zero-key zero-quota inside our compose stack — no cost to expose.

  const webSearchEnabled = useChatPrefs((s) => s.webSearchEnabled);

  const setWebSearchEnabled = useChatPrefs((s) => s.setWebSearchEnabled);

  const thinkingEnabled = useChatPrefs((s) => s.thinkingEnabled);

  const setThinkingEnabled = useChatPrefs((s) => s.setThinkingEnabled);

  const thinkingEnabledRef = useRef(thinkingEnabled);

  const webSearchEnabledRef = useRef(webSearchEnabled);

  useEffect(() => { thinkingEnabledRef.current = thinkingEnabled; }, [thinkingEnabled]);

  useEffect(() => { webSearchEnabledRef.current = webSearchEnabled; }, [webSearchEnabled]);

  const [agents, setAgents] = useState<AgentSummary[]>([]);

  useEffect(() => {
    const st = location.state as LocationState;
    if (typeof st?.thinkingEnabled === 'boolean') {
      setThinkingEnabled(st.thinkingEnabled);
      thinkingEnabledRef.current = st.thinkingEnabled;
    }
    if (typeof st?.webSearchEnabled === 'boolean') {
      setWebSearchEnabled(st.webSearchEnabled);
      webSearchEnabledRef.current = st.webSearchEnabled;
    }
    // 只在进入会话时吃一次 location.state
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);



  useEffect(() => {

    let cancelled = false;

    listModels()

      .then((cat) => {

        if (cancelled) return;

        setProviders(cat.providers ?? []);

        if (cat.default && cat.default.provider) {

          setCatalogDefault({ provider: cat.default.provider, model: cat.default.model || '' });

        }

      })

      .catch(() => {

        if (!cancelled) setProviders([]);

      });

    return () => {

      cancelled = true;

    };

  }, []);



  useEffect(() => {

    setPendingSessionModel(null);

  }, [sessionId]);



  useEffect(() => {

    if (!pendingSessionModel || !persistedSessionModel) return;

    if (pendingSessionModel.provider === persistedSessionModel.provider && pendingSessionModel.model === persistedSessionModel.model) {

      setPendingSessionModel(null);

    }

  }, [pendingSessionModel, persistedSessionModel]);



  
  useEffect(() => {
    let cancelled = false;
    listAgents()
      .then((r) => {
        if (cancelled) return;
        if (!Array.isArray(r.items)) {
          throw new Error('助理列表缺少 items');
        }
        setAgents(r.items);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSessionAgentChange(agentId: string) {
    if (submitting) {
      throw new Error('回合进行中不可切换助理');
    }
    if (agentId === '') {
      throw new Error('agent_id 不能为空');
    }
    setError(null);
    try {
      await updateSessionAgent(sessionId, agentId);
      invalidateChatSessions();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      throw e;
    }
  }

  async function handleSessionModelChange(selection: ModelSelection) {

    setPendingSessionModel(selection);

    try {

      await updateSessionModel(sessionId, selection.provider, selection.model);

      // 同步全局偏好，下次新建会话沿用本次选择

      useModelSelection.getState().setSelected(selection);

      invalidateChatSessions();

    } catch (err) {

      setPendingSessionModel(null);

      setError((err as Error).message || tr('保存会话模型失败', 'Failed to save session model'));

    }

  }



  // Initial load + idle refresh.

  // The session is shared with the IM bridge (Feishu / DingTalk), so

  // turns can arrive from a separate user agent while the web page is

  // open. We re-fetch every 5s — but only when the tab is visible and

  // no local SSE stream is in flight, so the poll never clobbers an

  // in-progress reply being streamed into `messages` from `send()`.

  const submittingRef = useRef(false);

  useEffect(() => { submittingRef.current = submitting; }, [submitting]);



  useEffect(() => {

    activeSessionRef.current = sessionId;

    const controller = getChatTurnController(sessionId);

    abortRef.current = controller;

    const sessionSubmitting = controller !== null;

    submittingRef.current = sessionSubmitting;

    setSubmitting(sessionSubmitting);

    setError(null);

    setMessages([]);

    setLoading(true);

    sentInitialRef.current = false;

    stickToBottomRef.current = true;

  }, [sessionId]);



  useEffect(() => {

    if (!sessionId) return;

    let cancelled = false;



    // The poll always replaces the message list wholesale, which means

    // every 5s React sees a brand-new array reference even when the

    // content hasn't changed. That re-triggers the auto-scroll effect

    // below (which yanked users back to the bottom while they were

    // reading — perceived as "scrolling makes content jump / duplicate").

    // Fingerprint the response and skip setMessages when nothing

    // actually changed.

    const fingerprintRef = { current: '' };

    // Other views own their SSE callbacks. Keep polling their messages and pending approvals.

    const ownsStream = () => {

      const controller = getChatTurnController(sessionId);

      return controller !== null && localStreams.current.has(controller);

    };

    let nextRequest = 0;

    let lastApplied = 0;

    const refetch = (initial: boolean) => {

      const request = ++nextRequest;

      if (initial) setLoading(true);

      Promise.all([

        getMessages(sessionId),

        // 刷新后 SSE 帧已丢：从本会话仍 pending 的审批重建行内确认卡；非管理员 403 → []。

        // (the inbox is admin-gated; the card just won't reappear for them).

        // Non-admins 403 here → 空列表（收件箱是管理员专属，卡片只是不再出现）。
        // 其他错误（500 / 网络故障）必须显式冒泡，不能伪装成「没有待审批」。
        listApprovals('pending').catch((err: unknown) => {
          if (err instanceof ApiError && err.status === 403) return { items: [] as Approval[] };
          throw err;
        }),

      ])

        .then(([r, ap]) => {

          if (cancelled || request < lastApplied || ownsStream()) return;

          lastApplied = request;

          const items = (r.items ?? []).map((m) => {

            if (m.kind === 'tool_card' || m.role === 'tool') {

              if (!m.tool_call) {

                throw new Error(`历史工具卡缺少 tool_call: ${m.id}`);

              }

              return { ...m, kind: 'tool_card' as const, role: 'tool' as const, tool_call: m.tool_call };

            }

            if (m.kind === 'tool_intent') {

              return { ...m, role: 'assistant' as const, kind: 'tool_intent' as const };

            }

            return m;

          });

          const pending = (ap.items ?? []).filter((a) => a.session_id === sessionId);

          // 工具卡已带 pending_approval 时不再追加 inbox 卡，避免重复
          const seenApprovalIds = new Set(
            items
              .map((m) => {
                const result = m.tool_call?.result;
                if (!result || typeof result !== 'object') return '';
                const id = (result as { approval_id?: unknown }).approval_id;
                return typeof id === 'string' ? id : '';
              })
              .filter((id) => id !== ''),
          );
          const orphanApprovals = pending.filter((a) => !seenApprovalIds.has(a.id));
          const merged = orphanApprovals.length
            ? [...items, ...orphanApprovals.map(approvalCardMessage)]
            : items;

          // 指纹含工具卡 status / result.status，避免只更新中间工具卡时漏刷

          const last = items[items.length - 1];

          const toolFp = items

            .filter((m) => m.kind === 'tool_card' || m.role === 'tool')

            .map((m) => {

              const tc = m.tool_call;

              const resultStatus =

                tc?.result && typeof tc.result === 'object'

                  ? String((tc.result as { status?: unknown }).status ?? '')

                  : '';

              return `${m.id}:${tc?.status ?? ''}:${resultStatus}`;

            })

            .join(',');

          const fp = `${items.length}|${last?.id ?? ''}|${last?.content?.length ?? 0}|${pending

            .map((a) => a.id)

            .join(',')}|${toolFp}`;

          if (!initial && fp === fingerprintRef.current) return;

          fingerprintRef.current = fp;

          setMessages(merged);

        })

        .catch((err: unknown) => {

          if (cancelled || request < lastApplied || !initial || ownsStream()) return;

          // 历史消息读取失败必须显式暴露，不能把会话显示成「空」。

          setMessages([]);

          setError(err instanceof Error ? err.message : '消息读取失败');

        })

        .finally(() => {

          if (!cancelled && initial) setLoading(false);

        });

    };



    refetch(true);

    const tick = () => {

      if (cancelled) return;

      if (document.visibilityState !== 'visible') return;

      if (ownsStream()) return;

      refetch(false);

    };

    const unsubscribe = subscribeChatTurns(id => {

      if (id !== sessionId) return;

      const controller = getChatTurnController(id);

      abortRef.current = controller;

      submittingRef.current = controller !== null;

      setSubmitting(controller !== null);

      if (!controller) refetch(false);

    });

    const intervalID = window.setInterval(tick, 5000);

    // 审批卡本地/跨页决策后立刻拉齐续跑消息

    const onChatRefetch = () => {

      if (cancelled) return;

      refetch(false);

    };

    window.addEventListener('ongrid:chat-refetch', onChatRefetch);

    return () => {

      cancelled = true;

      unsubscribe();

      window.clearInterval(intervalID);

      window.removeEventListener('ongrid:chat-refetch', onChatRefetch);

    };

  }, [sessionId]);

  // 审批 SSE 续跑：把增量帧接到当前会话消息列表，恢复打字机
  useEffect(() => {
    const onLive = (ev: Event) => {
      const detail = (ev as CustomEvent<ChatLiveDetail>).detail;
      if (!detail) return;
      if (detail.type === 'busy') {
        submittingRef.current = detail.busy;
        setSubmitting(detail.busy);
        return;
      }
      if (detail.type === 'thinking_delta') {
        if (!thinkingEnabledRef.current) return;
        const e = detail.event;
        if (!e.message_id || typeof e.delta !== 'string') return;
        setMessages((prev) => {
          const idx = prev.findIndex((m) => m.id === e.message_id);
          if (idx >= 0) {
            const next = prev.slice();
            const cur = next[idx];
            next[idx] = {
              ...cur,
              content: `${cur.content ?? ''}${e.delta}`,
              pending: e.partial === true,
              kind: 'thinking',
            };
            return next;
          }
          return [
            ...prev,
            {
              id: e.message_id,
              role: 'assistant',
              content: e.delta,
              pending: e.partial === true,
              kind: 'thinking',
            },
          ];
        });
        return;
      }
      if (detail.type === 'assistant_delta') {
        const e = detail.event;
        if (!e.message_id || typeof e.delta !== 'string') return;
        setMessages((prev) => {
          const closed = prev.map((m) =>
            m.kind === 'thinking' && m.pending ? { ...m, pending: false } : m,
          );
          const idx = closed.findIndex((m) => m.id === e.message_id);
          if (idx >= 0) {
            const next = closed.slice();
            const cur = next[idx];
            next[idx] = {
              ...cur,
              content: `${cur.content ?? ''}${e.delta}`,
              pending: e.partial === true,
            };
            return next;
          }
          return [
            ...closed,
            {
              id: e.message_id,
              role: 'assistant',
              content: e.delta,
              pending: e.partial === true,
            },
          ];
        });
        return;
      }
      if (detail.type === 'tool_start') {
        const t = detail.event;
        const card: ChatMessage = {
          id: `tool-card-${t.tool_call_id}`,
          role: 'tool',
          kind: 'tool_card',
          tool_call: {
            id: t.tool_call_id,
            name: t.name,
            device_id: t.device_id,
            status: 'pending',
            arguments: t.arguments,
            arguments_raw: t.arguments_raw,
          },
        };
        setMessages((prev) => {
          if (prev.some((m) => m.id === card.id)) return prev;
          return [...prev, card];
        });
        return;
      }
      if (detail.type === 'tool_end') {
        const t = detail.event;
        const targetId = `tool-card-${t.tool_call_id}`;
        setMessages((prev) =>
          prev.map((m) =>
            m.id === targetId
              ? {
                  ...m,
                  tool_call: {
                    id: t.tool_call_id,
                    name: t.name,
                    device_id: t.device_id,
                    status: t.status,
                    duration_ms: t.duration_ms,
                    error: t.error,
                    arguments: m.tool_call?.arguments ?? t.arguments,
                    arguments_raw: m.tool_call?.arguments_raw ?? t.arguments_raw,
                    result: t.result,
                    result_raw: t.result_raw,
                  },
                }
              : m,
          ),
        );
      }
    };
    window.addEventListener(CHAT_LIVE_EVENT, onLive);
    return () => window.removeEventListener(CHAT_LIVE_EVENT, onLive);
  }, []);

  // If we got here from Home with an initialPrompt and the session is

  // still empty (Home only creates the session — it doesn't post the

  // first message), drive the agent loop ourselves so the user sees

  // tool cards as they happen.

  useEffect(() => {

    if (loading) return;

    if ((!initialPrompt && !initialAttachments?.length) || !sessionId || sentInitialRef.current) return;

    if (messages.length > 0) {

      // Session already has messages (e.g. user navigated back); skip.

      sentInitialRef.current = true;

      return;

    }

    sentInitialRef.current = true;

    const st = location.state as LocationState;
    void send(initialPrompt ?? '', [], {
      attachments: initialAttachments,
      thinkingEnabled: typeof st?.thinkingEnabled === 'boolean' ? st.thinkingEnabled : thinkingEnabledRef.current,
      webSearchEnabled: typeof st?.webSearchEnabled === 'boolean' ? st.webSearchEnabled : webSearchEnabledRef.current,
    });

    // send 每次渲染都是新引用，放进依赖会导致 effect 反复触发；
    // sentInitialRef 已保证只发一次，因此这里只依赖触发源。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, initialPrompt, initialAttachments, sessionId, messages.length]);



  // Auto-scroll to bottom — but ONLY if the user hasn't scrolled up.

  // Driven by stickToBottomRef (see its definition above for why we

  // can't measure distance post-update). The onScroll listener wired

  // on the scroller flips the ref when the user takes manual control.

  useEffect(() => {

    const el = scrollerRef.current;

    if (!el) return;

    if (stickToBottomRef.current) {

      el.scrollTop = el.scrollHeight;

    }

  }, [messages]);



  // onScrollerScroll updates stickToBottomRef from the user's actual

  // position. 120px slack tolerates "almost at bottom" without losing

  // sticky mode (user clicking just above the latest message to copy

  // text, transient layout shifts as a tool card lands). useCallback

  // so React doesn't reattach the listener on every render.

  const onScrollerScroll = useCallback(() => {

    const el = scrollerRef.current;

    if (!el) return;

    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;

    stickToBottomRef.current = distance <= 120;

  }, []);



  async function send(

    content: string,

    mentions: Mention[],

    opts: {
      expectedTool?: string;
      displayContent?: string;
      attachments?: File[];
      thinkingEnabled?: boolean;
      webSearchEnabled?: boolean;
    } = {},

  ): Promise<boolean> {

    if (!sessionId || getChatTurnController(sessionId) || (!content.trim() && !opts.attachments?.length)) return false;

    const turnSessionID = sessionId;

    setError(null);

    setSubmitting(true);

    const ac = new AbortController();

    abortRef.current = ac;

    localStreams.current.add(ac);

    registerChatTurnController(turnSessionID, ac);



    let uploadedAttachments: ImageAttachment[] = [];

    try {

      uploadedAttachments = await Promise.all((opts.attachments ?? []).map((file) => uploadChatAttachment(turnSessionID, file)));

    } catch (uploadError) {

      setError((uploadError as Error).message || tr('图片上传失败', 'Image upload failed'));

      setSubmitting(false);

      unregisterChatTurnController(turnSessionID, ac);

      abortRef.current = null;

      return false;

    }

    let expectedToolSeen = !opts.expectedTool;

    let expectedToolFailed = false;



    // Optimistic user bubble; tool cards and final assistant bubble are

    // appended as the SSE stream delivers them. Tool-only assistant

    // turns (content === '' && pending_tool_calls > 0) are intentionally

    // skipped so the UI doesn't fill up with N "思考中" placeholders —

    // a single global indicator at the bottom covers in-flight state.

    const userMessageId = crypto.randomUUID();

    setMessages((prev) => [

      ...prev,

      { id: userMessageId, role: 'user', content: opts.displayContent ?? content, attachments: uploadedAttachments },

    ]);



    try {

      await streamMessage(

        sessionId,

        content,

        {

          onUserMessage: (e) => {

            if (activeSessionRef.current !== turnSessionID) return;

            if (!e.message_id) {

              throw new Error('user_message 缺少 message_id');

            }

            // id 已与落库一致时无需替换；不一致则原地换 key，避免整表闪烁

            setMessages((prev) =>

              prev.map((m) => (m.id === userMessageId ? { ...m, id: e.message_id } : m)),

            );

          },




          onThinkingDelta: (e) => {

            if (activeSessionRef.current !== turnSessionID) return;

            // 本回合未开思考：不入消息列表，避免出现思考气泡

            if (!thinkingEnabledRef.current) return;

            if (!e.message_id) {

              throw new Error('thinking_delta 缺少 message_id');

            }

            if (typeof e.delta !== 'string') {

              throw new Error('thinking_delta.delta 必须是字符串');

            }

            setMessages((prev) => {

              const idx = prev.findIndex((m) => m.id === e.message_id);

              if (idx >= 0) {

                const next = prev.slice();

                const cur = next[idx];

                next[idx] = {

                  ...cur,

                  content: `${cur.content ?? ''}${e.delta}`,

                  pending: e.partial === true,

                  kind: 'thinking',

                };

                return next;

              }

              return [

                ...prev,

                {

                  id: e.message_id,

                  role: 'assistant',

                  content: e.delta,

                  pending: e.partial === true,

                  kind: 'thinking',

                },

              ];

            });

          },

          onAssistantDelta: (e) => {

            if (activeSessionRef.current !== turnSessionID) return;

            if (!e.message_id) {

              throw new Error('assistant_delta 缺少 message_id');

            }

            if (typeof e.delta !== 'string') {

              throw new Error('assistant_delta.delta 必须是字符串');

            }

            // 最优打字机：直接 append SSE 增量，不做前端假动画
            // 正文到达后结束思考段 pending，便于 UI 收起

            setMessages((prev) => {

              const closed = prev.map((m) =>
                m.kind === 'thinking' && m.pending ? { ...m, pending: false } : m,
              );

              const idx = closed.findIndex((m) => m.id === e.message_id);

              if (idx >= 0) {

                const next = closed.slice();

                const cur = next[idx];

                next[idx] = {

                  ...cur,

                  content: `${cur.content ?? ''}${e.delta}`,

                  pending: e.partial === true,

                };

                return next;

              }

              return [

                ...closed,

                {

                  id: e.message_id,

                  role: 'assistant',

                  content: e.delta,

                  pending: e.partial === true,

                },

              ];

            });

          },

          onToolStart: (t) => {

            if (activeSessionRef.current !== turnSessionID) return;

            const card: ChatMessage = {

              id: toolCardId(t.tool_call_id),

              role: 'tool',

              kind: 'tool_card',

              tool_call: {

                id: t.tool_call_id,

                name: t.name,

                device_id: t.device_id,

                status: 'pending',

                arguments: t.arguments,

                arguments_raw: t.arguments_raw,

              },

            };

            setMessages((prev) => [...prev, card]);

          },

          onToolEnd: (t) => {

            if (activeSessionRef.current !== turnSessionID) return;

            if (opts.expectedTool && t.name === opts.expectedTool) {

              expectedToolSeen = true;

              expectedToolFailed = !!t.error || t.status === 'error' || t.status === 'timeout';

            }

            const targetId = toolCardId(t.tool_call_id);

            setMessages((prev) =>

              prev.map((m) =>

                m.id === targetId

                  ? {

                      ...m,

                      tool_call: {

                        id: t.tool_call_id,

                        name: t.name,

                        device_id: t.device_id,

                        status: t.status,

                        duration_ms: t.duration_ms,

                        error: t.error,

                        // Preserve args from tool_start; merge in result.

                        arguments: m.tool_call?.arguments ?? t.arguments,

                        arguments_raw: m.tool_call?.arguments_raw ?? t.arguments_raw,

                        result: t.result,

                        result_raw: t.result_raw,

                      },

                    }

                  : m,

              ),

            );

          },

          onApprovalPending: (a) => {

            if (activeSessionRef.current !== turnSessionID) return;

            // 工具等待人工确认：把 pending_approval 写回对应 tool_call_id 的流式卡片。





            const blob = {

              status: 'pending_approval',

              approval_id: a.approval_id,

              kind: a.kind ?? 'cloud_bash',

              tool_name: a.tool_name,

              command: a.command,

              credentials: a.credentials,

            };

            const args = a.command ? { command: a.command } : undefined;

            const toolName = a.tool_name ?? a.kind ?? 'cloud_bash';

            setMessages((prev) => {

              const targetId = a.tool_call_id ? toolCardId(a.tool_call_id) : '';

              const idx = targetId ? prev.findIndex((m) => m.id === targetId) : -1;

              if (idx >= 0) {

                const next = [...prev];

                const m = next[idx];

                next[idx] = {

                  ...m,

                  tool_call: {

                    id: a.tool_call_id,

                    name: m.tool_call?.name ?? toolName,

                    status: 'pending',

                    arguments: m.tool_call?.arguments ?? args,

                    result: blob,

                  },

                };

                return next;

              }

              // 没有可合并的工具卡时，追加独立审批卡。

              return [

                ...prev,

                {

                  id: `approval-${a.approval_id}`,

                  role: 'tool',

                  kind: 'tool_card',

                  tool_call: {

                    id: a.tool_call_id,

                    name: toolName,

                    status: 'pending',

                    arguments: args,

                    result: blob,

                  },

                },

              ];

            });

          },

          onDone: () => {

            if (activeSessionRef.current !== turnSessionID) return;

            setMessages((prev) =>
              prev.map((m) => (m.kind === 'thinking' ? { ...m, pending: false } : m)),
            );

            invalidateChatSessions();

          },

          onError: (err) => {

            if (activeSessionRef.current !== turnSessionID) return;

            throw err;

          },

        },

        {

          mentions,

          provider: selectedModel?.provider,

          model: selectedModel?.model,

          attachmentIds: uploadedAttachments.map((attachment) => attachment.id),

          webSearchEnabled: opts.webSearchEnabled ?? webSearchEnabledRef.current,

          thinkingEnabled: opts.thinkingEnabled ?? thinkingEnabledRef.current,

          locale,

          clientMessageId: userMessageId,

        },

        ac.signal,

      );

      return expectedToolSeen && !expectedToolFailed;

    } catch (err) {

      // Esc / stop aborts the fetch — that's an intentional stop, not an

      // error. Leave the partial conversation as-is (the server persisted it);

      // the history poll reconciles the final state.

      if (ac.signal.aborted || (err as Error).name === 'AbortError') {

        return false;

      }

      if (activeSessionRef.current !== turnSessionID) return false;

      const msg = (err as Error).message || tr('请求失败', 'Request failed');

      setError(msg);

      setMessages((prev) => [

        ...prev,

        {

          id: `optimistic-error-${Date.now()}`,

          role: 'assistant',

          content: tr('抱歉，处理消息时出错了。', "Sorry, something went wrong handling that message."),

          pending: false,

        },

      ]);

      return false;

    } finally {

      unregisterChatTurnController(turnSessionID, ac);

      if (activeSessionRef.current === turnSessionID) {

        submittingRef.current = false;

        setSubmitting(false);

      }

      if (abortRef.current === ac) abortRef.current = null;

    }

  }



  // Esc interrupts the in-flight turn. The turn is detached from the request

  // ctx server-side (so a refresh doesn't kill it), so we must explicitly tell

  // the server to stop (stopSession) AND abort the local stream for an instant

  // UI. Only acts while a turn is running; ignored otherwise so Esc keeps its

  // normal behavior (closing menus, blurring inputs).

  useEffect(() => {

    function onKey(e: KeyboardEvent) {

      if (e.defaultPrevented || (e.target instanceof Element && e.target.closest('[data-placement="assistant"]'))) return;

      if (e.key !== 'Escape' || !submittingRef.current || !sessionId) return;

      void stopSession(sessionId).catch(() => {});

      abortRef.current?.abort();

    }

    window.addEventListener('keydown', onKey);

    return () => window.removeEventListener('keydown', onKey);

  }, [sessionId]);



  function confirmConfigDraft(draft: ConfigDraftResult): Promise<boolean> {

    if (submitting) return Promise.resolve(false);

    const applyTool = configDraftApplyTool(draft);

    const content = buildConfigDraftConfirmMessage(draft, tr);

    return send(content, [], {

      expectedTool: applyTool,

      displayContent: tr('确认创建这条告警规则', 'Confirm creating this alert rule'),

    });

  }



  function ThinkingIndicator() {

    return (

      <div className="flex items-center gap-2 text-[11px] text-zinc-600">

        <span className="inline-flex gap-0.5">

          <span className="inline-block h-1 w-1 animate-pulse-dot rounded-full bg-zinc-500" style={{ animationDelay: '0s' }} />

          <span className="inline-block h-1 w-1 animate-pulse-dot rounded-full bg-zinc-500" style={{ animationDelay: '0.2s' }} />

          <span className="inline-block h-1 w-1 animate-pulse-dot rounded-full bg-zinc-500" style={{ animationDelay: '0.4s' }} />

        </span>

        <span>{tr('正在分析…', 'Analyzing…')}</span>

        <span className="text-zinc-700">·</span>

        <kbd className="rounded border border-zinc-700 px-1 text-[10px] text-zinc-500">Esc</kbd>

        <span className="text-zinc-600">{tr('停止', 'stop')}</span>

      </div>

    );

  }



  // toolCardId picks a synthetic message id for a streaming tool card.

  // Backend ids are UUIDs; we prefix the tool_call_id to keep cards

  // distinct from real message rows even when both happen to be UUID.

  function toolCardId(toolCallId: string): string {

    return `tool-card-${toolCallId}`;

  }



  // approvalCardMessage rebuilds a pending tool approval (from the

  // inbox) into the same synthetic tool card the live SSE path renders, so a

  // user who refreshed mid-wait still sees approve/reject. PendingApprovalCard

  // self-reconciles via getApproval, so a since-decided one resolves cleanly.

  function approvalCardMessage(a: Approval): ChatMessage {

    let command = '';

    let toolName = a.kind || 'cloud_bash';

    let credentials: string[] = [];

    try {

      const p = JSON.parse(a.payload) as { command?: string; credentials?: string[]; tool_name?: string; summary?: string };

      command = p.command ?? p.summary ?? '';

      toolName = p.tool_name ?? toolName;

      credentials = Array.isArray(p.credentials) ? p.credentials.filter(Boolean) : [];

    } catch {

      /* payload not JSON — card recovers command via getApproval on mount */

    }

    return {

      id: `approval-${a.id}`,

      role: 'tool',

      kind: 'tool_card',

      tool_call: {

        id: a.id,

        name: toolName,

        status: 'pending',

        arguments: command ? { command } : undefined,

        result: { status: 'pending_approval', approval_id: a.id, kind: a.kind, tool_name: toolName, command, credentials },

      },

    };

  }



  // True while an approval card is on screen awaiting the user's

  // decision (its tool card still carries the synthetic pending_approval

  // blob). Once approved/rejected the blocked tool returns and tool_end

  // replaces the blob, so this flips back to false and the normal thinking

  // indicator resumes for the continuation.

  const awaitingApproval =

    submitting &&

    messages.some(

      (m) =>

        m.kind === 'tool_card' &&

        m.tool_call?.result != null &&

        typeof m.tool_call.result === 'object' &&

        (m.tool_call.result as { status?: string }).status === 'pending_approval',

    );


  useAssistantPage('完整会话', 'Conversation', {

    session_id: sessionId,

    agent: sessionAgentID,

    message_count: messages.length,

    generating: submitting,

  });



  return (

    <main className="flex flex-1 flex-col overflow-hidden">

        <PageHeader

          className="px-6 py-3"

          title={

            <span className="flex min-w-0 items-center gap-2 text-sm font-medium text-zinc-100">

              <span className="truncate">{sessionTitle || tr('会话', 'Session')}</span>

              <AgentBadge agentId={sessionAgentID} size="sm" />

              <span className="text-[11px] font-normal text-zinc-600">#{sessionId}</span>

            </span>

          }

        />



        <div ref={scrollerRef} onScroll={onScrollerScroll} className="flex-1 overflow-y-auto">

          <div className="mx-auto flex w-full max-w-3xl flex-col gap-5 px-6 py-8">

            {loading ? (

              <div className="text-center text-sm text-zinc-500">{tr('加载中…', 'Loading…')}</div>

            ) : messages.length === 0 ? (

              <div className="text-center text-sm text-zinc-500">{tr('这是一个新的会话，发条消息试试。', "This is a new session — try sending a message.")}</div>

            ) : (

              messages.map((m) => (

                <MessageBubble

                  key={m.id}

                  message={m}

                  sessionId={sessionId}

                  onConfirmConfigDraft={isViewer ? undefined : confirmConfigDraft}

                />

              ))

            )}

            {submitting &&

              (awaitingApproval ? (

                // 回合在等人工确认，显示「等待确认」而不是思考中。

                <div className="flex items-center gap-2 text-[11px] text-zinc-600">

                  <span>{tr('等待你确认…', 'Waiting for your confirmation…')}</span>

                </div>

              ) : (

                <ThinkingIndicator />

              ))}

            {error && (

              <div

                role="alert"

                className="self-center rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300"

              >

                {error}

              </div>

            )}

          </div>

        </div>



        <div className="bg-zinc-950/80 px-6 py-4">

          <div className="mx-auto w-full max-w-3xl space-y-2">

            {isViewer && (

              <div

                role="status"

                className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-[11px] text-amber-200"

              >

                {tr(

                  '只读账号：可以提问，但 AI 只能调只读工具（不会改任何东西）',

                  'Viewer account: you can ask questions, but the AI only runs read-only tools (no side effects).',

                )}

              </div>

            )}

            <ChatInput

              onSubmit={(p: SubmitPayload) => void send(p.text, p.mentions, { attachments: p.attachments })}

              allowAttachments

              disabled={submitting}

              autoFocus

              placeholder={tr('继续聊…  Shift+Enter 换行', 'Continue the conversation… Shift+Enter for newline')}

              providers={providers}

              selectedModel={selectedModel}

              onModelChange={(model) => void handleSessionModelChange(model)}

              webSearchEnabled={webSearchEnabled}

              onWebSearchToggle={setWebSearchEnabled}

              thinkingEnabled={thinkingEnabled}

              onThinkingToggle={setThinkingEnabled}

              agents={agents}

              selectedAgentId={sessionAgentID ?? null}

              onAgentChange={(id) => void handleSessionAgentChange(id)}

            />

          </div>

        </div>

      </main>

  );

}
