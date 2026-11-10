import { describe, expect, it } from 'vitest';

import {
  COLLAPSE_KEEP,
  COLLAPSE_THRESHOLD,
  collapsedMessageCount,
} from './historyCollapse';

describe('collapsedMessageCount', () => {
  it('短会话不折叠，页面上不会出现折叠按钮', () => {
    expect(collapsedMessageCount(0, false)).toBe(0);
    expect(collapsedMessageCount(1, false)).toBe(0);
    expect(collapsedMessageCount(COLLAPSE_THRESHOLD, false)).toBe(0);
  });

  it('超过阈值默认只保留最近 COLLAPSE_KEEP 条', () => {
    const total = COLLAPSE_THRESHOLD + 1;
    expect(collapsedMessageCount(total, false)).toBe(total - COLLAPSE_KEEP);
  });

  it('展开后不再隐藏任何消息', () => {
    expect(collapsedMessageCount(1000, true)).toBe(0);
  });

  it('阈值与保留条数可显式覆盖，便于边界测试', () => {
    expect(collapsedMessageCount(10, false, 5, 2)).toBe(8);
    expect(collapsedMessageCount(5, false, 5, 2)).toBe(0);
  });

  it('参数非法时显式报错，不静默降级', () => {
    expect(() => collapsedMessageCount(-1, false)).toThrow(/非负整数/);
    expect(() => collapsedMessageCount(1.5, false)).toThrow(/非负整数/);
    expect(() => collapsedMessageCount(100, false, 10, 20)).toThrow(/不能大于折叠阈值/);
    expect(() => collapsedMessageCount(100, false, 10, 0)).toThrow(/正整数/);
  });
});
