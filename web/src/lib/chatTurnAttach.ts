// 第二标签页 attach 进行中回合：连该回合的事件流，把帧转成 chatLiveBus 事件，
// 让已打开的 ChatThread 复用同一套增量渲染逻辑（onLive），无需在页面里重复构造 callbacks。
//
// 为什么独立模块：chat.ts 只做纯 API，chatLiveBus 做浏览器内广播；这里把两者单向串起来，
// 避免 chat.ts ↔ chatLiveBus 的循环依赖（chatLiveBus 已 import type chat.ts）。

import {
  getActiveTurn,
  openTurnEventStream,
  type ToolStreamEvent,
  type ApprovalPendingStreamEvent,
} from '@/api/chat';
import { readSseFrames } from '@/api/sse';
import { emitChatLive, requestChatRefetch } from '@/lib/chatLiveBus';

/** 打开会话时调用：若有进行中回合，attach 并续流；返回是否真的 attach 上。 */
export async function attachActiveTurn(
  sessionId: string | number,
  signal?: AbortSignal,
): Promise<boolean> {
  let turnId: string | null;
  try {
    turnId = (await getActiveTurn(sessionId)).turn_id;
  } catch {
    // 查询失败不阻塞页面：最坏退回轮询补齐已落库内容
    return false;
  }
  if (!turnId) return false;

  const res = await openTurnEventStream(sessionId, turnId, signal);
  await readSseFrames(res, (frame) => {
    switch (frame.event) {
      case 'assistant_delta':
      case 'thinking_delta': {
        const d = JSON.parse(frame.data) as {
          message_id: string;
          delta: string;
          partial: boolean;
        };
        emitChatLive({
          type: frame.event,
          event: { message_id: d.message_id, delta: d.delta, partial: d.partial },
        });
        break;
      }
      case 'tool_start':
      case 'tool_end': {
        const d = JSON.parse(frame.data) as ToolStreamEvent;
        emitChatLive({ type: frame.event, event: d });
        break;
      }
      case 'approval_pending': {
        const d = JSON.parse(frame.data) as ApprovalPendingStreamEvent;
        emitChatLive({ type: 'approval_pending', event: d });
        break;
      }
      case 'done':
      case 'error':
        // 回合收口：与 send() 的 onDone 对齐，先清流式残留 pending（放开建议卡），
        // 再让页面 refetch 一次与落库最终态对齐（不重复追加 delta）
        emitChatLive({ type: 'turn_done' });
        requestChatRefetch();
        return false; // 停止读取，释放连接
    }
    return true;
  });
  return true;
}
