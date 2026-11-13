// 定时器的组件级清理：卸载时自动取消，避免对已卸载组件 setState。
//
// 页面各自手写 setTimeout 又各管清理，6 处漏了 3 处；统一从这里拿。

import { useCallback, useEffect, useRef } from 'react';

/** 返回一个自动清理的 setTimeout：组件卸载或引用变化时取消上一次。 */
export function useTimeout(): (fn: () => void, ms: number) => void {
  const timer = useRef<number | null>(null);

  const set = useCallback((fn: () => void, ms: number) => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(fn, ms);
  }, []);

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  return set;
}
