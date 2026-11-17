import type {
  ApprovalPendingStreamEvent,
  AssistantDeltaStreamEvent,
  ToolStreamEvent,
} from '@/api/chat';

/** 审批续跑等非 send() SSE：把增量帧广播给当前 ChatThread。 */
export const CHAT_LIVE_EVENT = 'ongrid:chat-live';

export type ChatLiveDetail =
  | { type: 'busy'; busy: boolean }
  // 执行态（计划/排查）与聊天回合独立：带外启动，由执行锁按 uid 计数广播，
  // 避免被回合收口的 setSubmitting(false) 误清。
  | { type: 'exec'; busy: boolean }
  | { type: 'thinking_delta'; event: AssistantDeltaStreamEvent }
  | { type: 'assistant_delta'; event: AssistantDeltaStreamEvent }
  | { type: 'tool_start'; event: ToolStreamEvent }
  | { type: 'tool_end'; event: ToolStreamEvent }
  | { type: 'approval_pending'; event: ApprovalPendingStreamEvent }
  // 用户已对某条审批做出决定：会话据此收起「等待你确认」提示。
  | { type: 'approval_decided'; approvalId: string }
  // create_plan 的继任者 submit_task_graph 落库：右栏工作台据此重拉列表并切到新图。
  | { type: 'graph_created'; graphUid: string };

export function emitChatLive(detail: ChatLiveDetail): void {
  window.dispatchEvent(new CustomEvent<ChatLiveDetail>(CHAT_LIVE_EVENT, { detail }));
}

/** 任务图收尾汇总的正文增量：把事件流的 delta 转发进对话流逐字显示。 */
export function relaySummaryDelta(messageId: string, delta: string, partial: boolean): void {
  emitChatLive({
    type: 'assistant_delta',
    event: { message_id: messageId, delta, partial },
  });
}

// 执行锁：按 uid 计数，只要还有任一执行在跑就锁住输入框。
// 由执行卡按自身状态 acquire/release，不依赖终态事件时序（事件可能在订阅重建时错过）。
const execLocks = new Set<string>();

export function acquireExecLock(uid: string): void {
  if (execLocks.has(uid)) return;
  execLocks.add(uid);
  emitChatLive({ type: 'exec', busy: true });
}

export function releaseExecLock(uid: string): void {
  if (!execLocks.delete(uid)) return;
  emitChatLive({ type: 'exec', busy: execLocks.size > 0 });
}

/** 消息列表需要重拉（自动汇总等带外补写落库后广播）。 */
export const CHAT_REFETCH_EVENT = 'ongrid:chat-refetch';

export function requestChatRefetch(): void {
  window.dispatchEvent(new Event(CHAT_REFETCH_EVENT));
}
