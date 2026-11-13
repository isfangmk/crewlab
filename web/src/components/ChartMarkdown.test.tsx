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
});
