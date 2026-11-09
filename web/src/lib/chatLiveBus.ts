import type {
  ApprovalPendingStreamEvent,
  AssistantDeltaStreamEvent,
  ToolStreamEvent,
} from '@/api/chat';

/** 审批续跑等非 send() SSE：把增量帧广播给当前 ChatThread。 */
export const CHAT_LIVE_EVENT = 'ongrid:chat-live';

export type ChatLiveDetail =
  | { type: 'busy'; busy: boolean }
  | { type: 'thinking_delta'; event: AssistantDeltaStreamEvent }
  | { type: 'assistant_delta'; event: AssistantDeltaStreamEvent }
  | { type: 'tool_start'; event: ToolStreamEvent }
  | { type: 'tool_end'; event: ToolStreamEvent }
  | { type: 'approval_pending'; event: ApprovalPendingStreamEvent }
  // 用户已对某条审批做出决定：会话据此收起「等待你确认」提示。
  | { type: 'approval_decided'; approvalId: string };

export function emitChatLive(detail: ChatLiveDetail): void {
  window.dispatchEvent(new CustomEvent<ChatLiveDetail>(CHAT_LIVE_EVENT, { detail }));
}
