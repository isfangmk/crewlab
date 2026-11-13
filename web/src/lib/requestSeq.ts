// 列表页请求竞态保护。
//
// 列表页改筛选会连续发请求，响应顺序不保证：先发的慢请求可能后到，
// 把新筛选的结果覆盖回旧数据。列表页共用同一套路数守卫：
//
//   const seq = useRequestSeq();
//   const load = useCallback(async () => {
//     const current = seq.next();
//     ...
//     const data = await fetchList(...);
//     if (seq.isStale(current)) return; // 已有更新的请求在途，丢弃这次结果
//     setItems(data.items);
//   }, [...]);

import { useCallback, useMemo, useRef } from 'react';

export function useRequestSeq(): { next(): number; isStale(seq: number): boolean } {
  const seq = useRef(0);

  const next = useCallback(() => {
    seq.current += 1;
    return seq.current;
  }, []);

  const isStale = useCallback((taken: number) => taken !== seq.current, []);

  // 返回值必须引用稳定：它常被列进 load 的依赖，若每次渲染都是新对象，
  // load 会被无限重建、防抖 effect 无限重跑，页面陷入循环请求。
  return useMemo(() => ({ next, isStale }), [next, isStale]);
}
