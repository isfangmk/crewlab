// 执行视图的计时与心跳：运行中每秒跳「已运行」，收口变「用时」。
// 起点取事件到达时刻（worker_started / step_started），页面刷新兜底用 created_at；
// 心跳行展示「最近动作 N 秒前」，超 60 秒无动作视为疑似停滞。

import { useEffect, useRef, useState } from 'react';

export function fmtDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  return `${m}m${Math.round(s - m * 60)}s`;
}

/** 距今相对时间：「3 秒前」「2 分钟前」。 */
export function fmtAgo(fromMs: number | null, nowMs: number): string {
  if (fromMs === null) return '—';
  const s = Math.max(0, Math.round((nowMs - fromMs) / 1000));
  if (s < 60) return `${s} 秒前`;
  const m = Math.floor(s / 60);
  return `${m} 分钟前`;
}

/** 心跳停滞阈值：超过该秒数无日志事件视为疑似停滞。 */
export const STALE_AFTER_SECONDS = 60;

export function isStale(fromMs: number | null, nowMs: number): boolean {
  return fromMs !== null && nowMs - fromMs > STALE_AFTER_SECONDS * 1000;
}

/**
 * 每秒走一次的时钟：active 时驱动运行计时与心跳刷新。
 * 返回 Date.now() 派生的 tick 值（毫秒），组件据此计算已运行/相对时间。
 */
export function useTicker(active: boolean): number {
  const [tick, setTick] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setTick(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  return tick;
}

/** 活动时刻记录：log 事件到达即刷新，用于心跳与停滞判断。 */
export function useActivity() {
  const lastAt = useRef<number | null>(null);
  const touch = () => {
    lastAt.current = Date.now();
  };
  return { touch, get: () => lastAt.current };
}
