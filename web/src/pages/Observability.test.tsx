// 可观测页测试 —— 覆盖「详情入口统一为点输入摘要」：
//   1. 列表行不再整体可点，只有输入摘要是打开 Trace 详情的入口
//   2. 点摘要才拉详情并弹窗
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it } from 'vitest';

import ObservabilityPage from './Observability';
import { server } from '@/test/msw-server';

const TURN = {
  trace_id: 'trace-1',
  session_id: 'session-abcdef01',
  agent_id: 'sre_agent',
  provider: 'openai',
  model: 'gpt-4o',
  status: 'ok',
  request_id: 'req-1',
  input_summary: '查询 CPU 趋势',
  error_message: null,
  prompt_tokens: 120,
  completion_tokens: 340,
  total_tokens: 460,
  duration_ms: 1500,
  tool_call_count: 1,
  skill_call_count: 0,
  started_at: '2026-11-09T04:00:00.000Z',
  ended_at: '2026-11-09T04:00:01.500Z',
};

const OVERVIEW = {
  stats: {
    total: 1,
    error_count: 0,
    running_count: 0,
    avg_duration_ms: 1500,
    p95_duration_ms: 1500,
    total_tokens: 460,
    tool_call_total: 1,
    skill_call_total: 0,
  },
  agents: ['sre_agent'],
  models: ['gpt-4o'],
};

describe('ObservabilityPage', () => {
  let detailRequests: string[] = [];

  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
    detailRequests = [];
    server.use(
      http.get('/api/v1/observability/turns', () =>
        HttpResponse.json({ items: [TURN], total: 1 }),
      ),
      http.get('/api/v1/observability/overview', () => HttpResponse.json(OVERVIEW)),
      http.get('/api/v1/observability/turns/:traceId', ({ params }) => {
        detailRequests.push(String(params.traceId));
        return HttpResponse.json({ ...TURN, tool_calls: [] });
      }),
    );
  });

  it('列表行的输入摘要是打开 Trace 详情的入口', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <ObservabilityPage />
      </MemoryRouter>,
    );

    const summary = await screen.findByRole('button', { name: '查询 CPU 趋势' });
    // 摘要之前是整行可点（<tr> 上挂 onClick），现在行不再响应点击
    expect(summary.closest('tr')).not.toHaveAttribute('onclick');
    expect(detailRequests).toHaveLength(0);

    await user.click(summary);

    await waitFor(() => expect(detailRequests).toEqual(['trace-1']));
    expect(await screen.findByText('Trace 详情')).toBeInTheDocument();
  });
});
