// useNotifications — 在登录态下挂载一个全局通知连接（每标签页一条）。
//
// 把后端通知翻译成既有 window 事件（chatLiveBus），各页面复用原有处理逻辑：
//   chat_refetch / approval_decided / graph_finished → requestChatRefetch + emitChatLive
//   graph_created → emitChatLive(graph_created)，工作台据此切到新图
//   knowledge_sync_done / eval_run_done → 对应页面的 refetch 事件
//
// 连接本身只建一条，与本页路由无关；uid 由后端从登录态解析，客户端无法伪造。

import { useEffect } from 'react';
import { subscribeNotifications, type NotificationKind } from '@/api/notifications';
import { useAuth } from '@/store/auth';
import { emitChatLive, requestChatRefetch } from '@/lib/chatLiveBus';

export const KNOWLEDGE_SYNC_EVENT = 'ongrid:knowledge-sync-done';
export const EVAL_RUN_EVENT = 'ongrid:eval-run-done';
/** 会话队列变更：payload { session_id, action, total }，ChatThread 据此刷新队列条。 */
export const CHAT_QUEUE_CHANGED_EVENT = 'chat_queue_changed';

export function useNotifications(): void {
  const token = useAuth((s) => s.token);
  useEffect(() => {
    if (!token) return;
    const stop = subscribeNotifications((kind: NotificationKind, payload) => {
      switch (kind) {
        case 'chat_refetch':
        case 'graph_finished':
          requestChatRefetch();
          if (kind === 'graph_finished' && typeof payload.graph_uid === 'string') {
            emitChatLive({ type: 'graph_created', graphUid: payload.graph_uid });
          }
          break;
        case 'approval_decided':
          if (typeof payload.approval_id === 'string') {
            emitChatLive({ type: 'approval_decided', approvalId: payload.approval_id });
          }
          requestChatRefetch();
          break;
        case 'graph_created':
          if (typeof payload.graph_uid === 'string') {
            emitChatLive({ type: 'graph_created', graphUid: payload.graph_uid });
          }
          break;
        case 'knowledge_sync_done':
          window.dispatchEvent(new Event(KNOWLEDGE_SYNC_EVENT));
          break;
        case 'eval_run_done':
          window.dispatchEvent(new Event(EVAL_RUN_EVENT));
          break;
        case 'queue_changed':
          window.dispatchEvent(new CustomEvent(CHAT_QUEUE_CHANGED_EVENT, { detail: payload }));
          break;
      }
    });
    return stop;
  }, [token]);
}
