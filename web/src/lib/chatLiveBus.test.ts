// 执行锁计数语义：多执行卡并存时，任一在跑就锁输入框，全部释放才解锁。
// 通过监听 CHAT_LIVE_EVENT 断言广播的 busy 值，不发真实网络请求。
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  CHAT_LIVE_EVENT,
  acquireExecLock,
  releaseExecLock,
  type ChatLiveDetail,
} from '@/lib/chatLiveBus';

function captured(): ChatLiveDetail[] {
  const seen: ChatLiveDetail[] = [];
  const onEvt = (e: Event) => seen.push((e as CustomEvent<ChatLiveDetail>).detail);
  window.addEventListener(CHAT_LIVE_EVENT, onEvt);
  return seen;
}

describe('执行锁计数', () => {
  beforeEach(() => {
    // 每个用例前清空可能残留的锁，保证互不干扰
    releaseExecLock('a');
    releaseExecLock('b');
  });

  it('首个执行申领→锁，释放→解锁', () => {
    const seen = captured();
    acquireExecLock('a');
    expect(seen.at(-1)).toEqual({ type: 'exec', busy: true });
    releaseExecLock('a');
    expect(seen.at(-1)).toEqual({ type: 'exec', busy: false });
  });

  it('两路执行并存：释放一路仍锁，全部释放才解锁', () => {
    const seen = captured();
    acquireExecLock('a');
    acquireExecLock('b');
    releaseExecLock('a');
    expect(seen.at(-1)).toEqual({ type: 'exec', busy: true });
    releaseExecLock('b');
    expect(seen.at(-1)).toEqual({ type: 'exec', busy: false });
  });

  it('重复申领同一 uid 不重复广播，释放不存在的 uid 无副作用', () => {
    const seen = captured();
    acquireExecLock('a');
    acquireExecLock('a');
    expect(seen.filter((d) => d.type === 'exec').length).toBe(1);
    releaseExecLock('ghost');
    expect(seen.at(-1)).toEqual({ type: 'exec', busy: true });
  });
});
