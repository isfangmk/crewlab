import '@testing-library/jest-dom';
import { afterAll, afterEach, beforeAll } from 'vitest';
import { server } from './msw-server';
import { DEFAULT_SYSTEM_TIMEZONE, useSystemTimezone } from '@/store/systemTimezone';

// 组件渲染时间戳时需要系统时区已加载（store 未加载会显式抛错）。
// 测试环境用固定时区直接置位，避免每个用例都去拉 observability.timezone。
useSystemTimezone.getState().setLabel(DEFAULT_SYSTEM_TIMEZONE);

// jsdom lacks PointerEvent; Base UI uses it for keyboard-activated clicks.
if (!window.PointerEvent) {
  class TestPointerEvent extends MouseEvent {
    readonly pointerId: number;
    readonly pointerType: string;
    readonly isPrimary: boolean;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 0;
      this.pointerType = init.pointerType ?? '';
      this.isPrimary = init.isPrimary ?? false;
    }
  }
  Object.defineProperty(window, 'PointerEvent', { value: TestPointerEvent, configurable: true });
}

// Node 22+ 自带的实验性 webstorage 全局会盖掉 jsdom 的 localStorage——
// 没有 --localstorage-file 时它的 getItem 是 undefined，导致任何
// 走 useI18n()/getLocale() 的组件测试在 render 时抛
// "localStorage.getItem is not a function"。这里换成内存实现。
if (typeof globalThis.localStorage?.getItem !== 'function') {
  const mem = new Map<string, string>();
  const stub: Storage = {
    get length() {
      return mem.size;
    },
    clear: () => mem.clear(),
    getItem: (k: string) => mem.get(k) ?? null,
    key: (i: number) => [...mem.keys()][i] ?? null,
    removeItem: (k: string) => void mem.delete(k),
    setItem: (k: string, v: string) => void mem.set(k, String(v)),
  };
  Object.defineProperty(globalThis, 'localStorage', { value: stub, configurable: true });
}

// jsdom 缺 ResizeObserver / DOMMatrixReadOnly，而画布组件（React Flow）挂载时两者都要用。
// 不补的话组件在 effect 里直接抛错，整棵树被 React 卸载，页面只剩空白，断言全部失败。
// 这里给 ResizeObserver 一个会回调固定尺寸的实现（600×400 足够让视口算法跑起来），
// DOMMatrixReadOnly 只需要单位矩阵。
if (typeof globalThis.ResizeObserver !== 'function') {
  class TestResizeObserver {
    cb: ResizeObserverCallback;
    constructor(cb: ResizeObserverCallback) {
      this.cb = cb;
    }
    observe(target: Element): void {
      this.cb([{ target, contentRect: { width: 600, height: 400 } } as ResizeObserverEntry], this as unknown as ResizeObserver);
    }
    unobserve(): void {}
    disconnect(): void {}
  }
  Object.defineProperty(globalThis, 'ResizeObserver', { value: TestResizeObserver, configurable: true });
}

// jsdom 缺 DOMMatrixReadOnly：React Flow 量节点时会直接 new 它。
// 给一个只提供单位矩阵的占位实现，测试只关心文本断言，不需要真实变换。
if (typeof globalThis.DOMMatrixReadOnly !== 'function') {
  class TestDOMMatrix {
    m22 = 1;
    constructor(_transform?: string) {}
  }
  Object.defineProperty(globalThis, 'DOMMatrixReadOnly', { value: TestDOMMatrix, configurable: true });
}

// onUnhandledRequest:'error' so any test forgetting a handler fails
// loudly instead of silently hanging on a real fetch.
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
