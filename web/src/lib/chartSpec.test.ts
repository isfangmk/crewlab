import { describe, expect, it } from 'vitest';
import {
  isChartFence,
  isChartOption,
  parseChartSpec,
  parseChartSpecStreaming,
  repairJsonPrefix,
} from './chartSpec';

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

describe('repairJsonPrefix', () => {
  it('closes truncated containers and keeps complete values', () => {
    expect(repairJsonPrefix('{"a": [1, 2, 3')).toBe('{"a": [1, 2, 3]}');
    expect(repairJsonPrefix('{"a": {"b": "x"')).toBe('{"a": {"b": "x"}}');
  });

  it('drops a half-typed string or literal', () => {
    expect(repairJsonPrefix('{"a": [1, 2, "ab')).toBe('{"a": [1, 2]}');
    expect(repairJsonPrefix('{"a": [1, 2, tru')).toBe('{"a": [1, 2]}');
    expect(repairJsonPrefix('{"a": [1, 2, 3.')).toBe('{"a": [1, 2]}');
  });

  it('ignores commas and colons after the cut', () => {
    expect(repairJsonPrefix('{"a": 1, "b":')).toBe('{"a": 1}');
    expect(repairJsonPrefix('{"a": [1, 2,')).toBe('{"a": [1, 2]}');
  });

  it('returns empty when nothing complete exists', () => {
    expect(repairJsonPrefix('{"a":')).toBe('');
    expect(repairJsonPrefix('{"a": "unterminated')).toBe('');
    expect(repairJsonPrefix('not json')).toBe('');
  });
});

describe('parseChartSpecStreaming', () => {
  it('renders from truncated chart JSON and grows with the stream', () => {
    const partial =
      '{"chart": "line", "title": "气温", "option": {"xAxis": {"type": "category", "data": ["10-17", "10-18"';
    const spec = parseChartSpecStreaming(partial);
    expect(spec?.title).toBe('气温');
    expect(spec?.option).toEqual({
      xAxis: { type: 'category', data: ['10-17', '10-18'] },
    });

    // 多流出一个数据点后能解析出更长的序列
    const grown = parseChartSpecStreaming(partial + ', "10-19"');
    expect((grown?.option as { xAxis: { data: string[] } }).xAxis.data).toEqual([
      '10-17',
      '10-18',
      '10-19',
    ]);
  });

  it('handles a truncated bare option', () => {
    const spec = parseChartSpecStreaming('{"series": [{"type": "line", "data": [1, 2, 3');
    expect(spec?.option).toEqual({ series: [{ type: 'line', data: [1, 2, 3] }] });
  });

  it('returns null before any chart shape exists', () => {
    expect(parseChartSpecStreaming('{"chart": "line", "titl')).toBeNull();
  });

  it('matches strict parse for complete JSON', () => {
    const full = JSON.stringify({ series: [{ type: 'line', data: [1] }] });
    expect(parseChartSpecStreaming(full)).toEqual(parseChartSpec(full));
  });

  it('returns null for garbage', () => {
    expect(parseChartSpecStreaming('not json at all')).toBeNull();
    expect(parseChartSpecStreaming('')).toBeNull();
  });
});
