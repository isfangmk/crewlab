// 全局通知流：每个标签页一个 SSE 连接，收后端状态变更信号。
//
// 替代原来各页面各自 5 秒轮询：后端在消息落库/审批决定/任务图收尾/
// 外部源同步完成/评测跑完时发通知，这里收到后 emit 到现有 window 总线，
// 各页面复用既有的 onChatRefetch / onLive 处理逻辑，无需各自轮询。
//
// 不用 EventSource（带不上 Authorization），沿用 fetch + readSseFrames。

import { getToken } from '@/store/auth';
import { readSseFrames } from '@/api/sse';

export type NotificationKind =
  | 'chat_refetch'
  | 'approval_decided'
  | 'approval_created'
  | 'graph_created'
  | 'graph_finished'
  | 'knowledge_sync_done'
  | 'eval_run_done'
  | 'queue_changed';

export type NotificationHandler = (kind: NotificationKind, payload: Record<string, unknown>) => void;

/** 订阅当前用户的全局通知流；返回取消函数。断线自动重连（指数退避，上限 30s）。 */
export function subscribeNotifications(onNotification: NotificationHandler): () => void {
  const controller = new AbortController();
  let stopped = false;
  let lastId = '';
  let retryMs = 1000;

  const connect = async () => {
    while (!stopped) {
      try {
        const token = getToken();
        if (!token) return; // 未登录：等下次调用（登录后重挂）
        const url = new URL('/api/v1/events', window.location.origin);
        if (lastId) url.searchParams.set('last_id', lastId);
        const res = await fetch(url.toString(), {
          headers: {
            Accept: 'text/event-stream',
            Authorization: `Bearer ${token}`,
          },
          signal: controller.signal,
        });
        if (!res.ok || !res.body) throw new Error(`notifications HTTP ${res.status}`);
        retryMs = 1000; // 连上即重置退避
        await readSseFrames(res, (frame) => {
          if (frame.event === 'message') return;
          if (frame.id) lastId = frame.id;
          let payload: Record<string, unknown> = {};
          try {
            payload = JSON.parse(frame.data) as Record<string, unknown>;
          } catch {
            payload = {};
          }
          onNotification(frame.event as NotificationKind, payload);
        });
      } catch {
        if (stopped) return;
      }
      if (stopped) return;
      // 连接被服务端关闭/出错：退避重连
      await new Promise((r) => setTimeout(r, retryMs));
      retryMs = Math.min(retryMs * 2, 30_000);
    }
  };

  void connect();
  return () => {
    stopped = true;
    controller.abort();
  };
}
