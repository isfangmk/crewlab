// 气泡 pending 收口：流式帧只带 partial:true（ADK 终包回放被后端去重跳过），
// 气泡收不到收尾帧，pending 不能指望帧序来清——只在「整轮 done」与
// 「下一个气泡开写」两个确定时刻收口，否则建议卡会被 !message.pending 门永久压住。
import type { ChatMessage } from '@/api/chat';

/** 整轮结束：done 之后没有任何消息还在写。 */
export function clearAllPending(messages: ChatMessage[]): ChatMessage[] {
  return messages.map((m) => (m.pending ? { ...m, pending: false } : m));
}

/** 新气泡开始写：除它之外的气泡均已写完。 */
export function clearPendingExcept(
  messages: ChatMessage[],
  activeId: string,
): ChatMessage[] {
  return messages.map((m) =>
    m.pending && m.id !== activeId ? { ...m, pending: false } : m,
  );
}
