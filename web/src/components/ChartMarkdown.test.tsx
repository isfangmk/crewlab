import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ChartMarkdown } from './ChartMarkdown';

// echarts 在 jsdom 里画不出真实画布，这里只关心围栏被识别成图表
vi.mock('echarts', () => ({
  init: () => ({
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
  }),
}));

afterEach(() => {
  cleanup();
});

describe('ChartMarkdown', () => {
  it('renders an echarts fence as a chart card', () => {
    const option = { xAxis: { type: 'category' }, series: [{ type: 'line', data: [1, 2] }] };
    render(
      <ChartMarkdown
        content={['```echarts', JSON.stringify({ chart: 'line', title: 'CPU 趋势', option }), '```'].join('\n')}
      />,
    );
    expect(screen.getByText('CPU 趋势')).toBeInTheDocument();
  });

  it('keeps non-chart code fences as code', () => {
    render(<ChartMarkdown content={'```python\nprint(1)\n```'} />);
    expect(screen.queryByText('CPU 趋势')).not.toBeInTheDocument();
  });

  it('falls back to a code block when the fence is not valid chart JSON', () => {
    render(<ChartMarkdown content={'```echarts\n{ broken\n```'} />);
    // 内容没被吞掉：仍然能在页面上找到这段文本
    expect(screen.getByText(/broken/)).toBeInTheDocument();
  });

  it('流式中从半截 JSON 渐进渲染图表，不闪原始 JSON', () => {
    const partial =
      '{"chart": "line", "title": "气温趋势", "option": {"series": [{"type": "line", "data": [16.0, 18.0';
    render(<ChartMarkdown content={`\`\`\`echarts\n${partial}`} streaming />);
    expect(screen.getByText('气温趋势')).toBeInTheDocument();
    // 半截 JSON 不以代码块形式出现在正文里
    expect(screen.queryByText(/"series"/)).not.toBeInTheDocument();
  });

  it('流式中还解析不出图表形状时显示骨架，而不是原始 JSON', () => {
    render(<ChartMarkdown content={'```echarts\n{"chart": "line", "titl'} streaming />);
    expect(screen.getByText('图表生成中…')).toBeInTheDocument();
    expect(screen.queryByText(/"chart"/)).not.toBeInTheDocument();
  });

  it('流式结束后解析失败仍退回代码块', () => {
    render(<ChartMarkdown content={'```echarts\n{ broken\n```'} streaming={false} />);
    expect(screen.getByText(/broken/)).toBeInTheDocument();
    expect(screen.queryByText('图表生成中…')).not.toBeInTheDocument();
  });
});
