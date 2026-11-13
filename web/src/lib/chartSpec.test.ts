import { describe, expect, it } from 'vitest';
import { isChartFence, isChartOption, parseChartSpec } from './chartSpec';

describe('isChartFence', () => {
  it.each([
    ['language-echarts', true],
    ['language-chart', true],
    ['language-ECHARTS', true],
    ['language-json', false],
    ['language-python', false],
    [undefined, false],
  ] as const)('className %s -> %s', (className, expected) => {
    expect(isChartFence(className)).toBe(expected);
  });
});

describe('isChartOption', () => {
  it('accepts an option with series', () => {
    expect(isChartOption({ series: [{ type: 'line', data: [1] }] })).toBe(true);
  });

  it('accepts an option with axes', () => {
    expect(isChartOption({ xAxis: {}, yAxis: {} })).toBe(true);
  });

  it('rejects an empty object', () => {
    expect(isChartOption({})).toBe(false);
  });

  it('rejects non-objects', () => {
    expect(isChartOption(null)).toBe(false);
    expect(isChartOption([1, 2])).toBe(false);
    expect(isChartOption('series')).toBe(false);
  });
});

describe('parseChartSpec', () => {
  it('parses a full spec with chart/title/option/summary', () => {
    const spec = parseChartSpec(
      JSON.stringify({
        chart: 'line',
        title: 'CPU 趋势',
        option: { series: [{ type: 'line', data: [1, 2] }] },
        summary: '峰值 2',
      }),
    );
    expect(spec).not.toBeNull();
    expect(spec?.chart).toBe('line');
    expect(spec?.title).toBe('CPU 趋势');
    expect(spec?.summary).toBe('峰值 2');
  });

  it('parses a bare ECharts option', () => {
    const spec = parseChartSpec(JSON.stringify({ xAxis: {}, series: [] }));
    expect(spec).not.toBeNull();
    expect(spec?.option).toEqual({ xAxis: {}, series: [] });
  });

  it('returns null for invalid JSON', () => {
    expect(parseChartSpec('{ not json')).toBeNull();
  });

  it('returns null for JSON that is not a chart', () => {
    expect(parseChartSpec(JSON.stringify({ foo: 1 }))).toBeNull();
  });

  it('returns null for empty input', () => {
    expect(parseChartSpec('')).toBeNull();
    expect(parseChartSpec('   ')).toBeNull();
  });
});
