import { describe, expect, it } from 'vitest';

import { compareNullableTime } from './sort';

describe('compareNullableTime', () => {
  it('按方向比较有效时间', () => {
    const older = '2026-11-12T08:00:00Z';
    const newer = '2026-11-14T08:00:00Z';

    expect(compareNullableTime(older, newer, 'asc')).toBeLessThan(0);
    expect(compareNullableTime(older, newer, 'desc')).toBeGreaterThan(0);
  });

  it('空值与非法时间始终排在最后', () => {
    const value = '2026-11-14T08:00:00Z';

    expect(compareNullableTime(null, value, 'desc')).toBe(1);
    expect(compareNullableTime(null, value, 'asc')).toBe(1);
    expect(compareNullableTime('not-a-date', value, 'asc')).toBe(1);
    expect(compareNullableTime(value, null, 'desc')).toBe(-1);
    expect(compareNullableTime(null, undefined, 'asc')).toBe(0);
  });
});
