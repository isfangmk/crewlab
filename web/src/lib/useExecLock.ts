// 执行锁 hook：执行卡按自身 running 状态申领/释放输入框锁。
// 不依赖终态事件时序——订阅 effect 在状态切换重建时可能错过 *_finished，
// 而「状态 → busy」的派生在 active 翻转的那一刻必然成立。
import { useEffect } from 'react';
import { acquireExecLock, releaseExecLock } from '@/lib/chatLiveBus';

export function useExecLock(uid: string | null | undefined, active: boolean): void {
  useEffect(() => {
    if (!uid || !active) return;
    acquireExecLock(uid);
    return () => releaseExecLock(uid);
  }, [uid, active]);
}
