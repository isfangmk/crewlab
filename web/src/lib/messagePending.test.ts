// 气泡 pending 收口：流式收尾帧被后端去重跳过，pending 只能在 done /
// 下一气泡开写时清——否则建议卡会被 MessageBubble 的 !message.pending 门永久压住。
import { describe, expect, it } from 'vitest';

import type { ChatMessage } from '@/api/chat';

import { clearAllPending, clearPendingExcept } from './messagePending';

function msg(id: string, pending?: boolean): ChatMessage {
  return { id, role: 'assistant', content: '正文', ...(pending === undefined ? {} : { pending }) };
}

describe('clearAllPending', () => {
  it('done 后清掉全部气泡的 pending', () => {
    const out = clearAllPending([msg('a', true), msg('b', true), msg('c', false)]);
    expect(out.map((m) => m.pending)).toEqual([false, false, false]);
  });

  it('原本未 pending 的气泡保持原对象', () => {
    const plain = msg('a');
    const out = clearAllPending([plain]);
    expect(out[0]).toBe(plain);
  });
});

describe('clearPendingExcept', () => {
  it('新气泡开写时收口其余气泡，自身标记不动', () => {
    const out = clearPendingExcept(
      [msg('a', true), msg('b', true), msg('c', false)],
      'b',
    );
    expect(out.map((m) => [m.id, m.pending])).toEqual([
      ['a', false],
      ['b', true],
      ['c', false],
    ]);
  });

  it('后续帧仍可更新活动气泡的 pending（自愈）', () => {
    let msgs = clearPendingExcept([msg('a', true), msg('b', true)], 'b');
    // 活动气泡的 pending 由帧序驱动：partial 帧置 true，收口帧置 false
    msgs = msgs.map((m) => (m.id === 'b' ? { ...m, pending: false } : m));
    expect(msgs.map((m) => m.pending)).toEqual([false, false]);
  });
});
