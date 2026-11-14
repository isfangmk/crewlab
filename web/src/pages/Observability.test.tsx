// 可观测页测试 —— 覆盖：
//   1. 列表行不再整体可点，只有输入摘要是打开 Trace 详情的入口
//   2. 时间 / 耗时表头可切换排序，并把排序参数带给后端
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
  channel: '',
  status: 'ok',
  request_id: 'req-1',
  input_summary: '查询 CPU 趋势',
  error_message: null,
  prompt_tokens: 120,
  completion_tokens: 340,
  total_tokens: 460,
  duration_ms: 1500,
  first_token_ms: 400,
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
    avg_first_token_ms: 400,
    p95_first_token_ms: 400,
    total_tokens: 460,
    tool_call_total: 1,
    skill_call_total: 0,
  },
  agents: ['sre_agent'],
  models: ['gpt-4o'],
  channels: [],
};

describe('ObservabilityPage', () => {
  let detailRequests: string[] = [];
  let listQueries: URLSearchParams[] = [];

  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
    detailRequests = [];
    listQueries = [];
    server.use(
      http.get('/api/v1/observability/turns', ({ request }) => {
        listQueries.push(new URL(request.url).searchParams);
        return HttpResponse.json({ items: [TURN], total: 1 });
      }),
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
    // 列表与概览都要展示首字延迟
    expect(screen.getByText('首字 P95')).toBeInTheDocument();
    expect(screen.getByText('首字 400 ms')).toBeInTheDocument();

    await user.click(summary);

    await waitFor(() => expect(detailRequests).toEqual(['trace-1']));
    expect(await screen.findByText('Trace 详情')).toBeInTheDocument();
    expect(screen.getByText('首字延迟')).toBeInTheDocument();
  });

  it('时间与耗时表头支持切换排序', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <ObservabilityPage />
      </MemoryRouter>,
    );

    await screen.findByRole('button', { name: '查询 CPU 趋势' });
    const timeHeader = screen.getByRole('button', { name: '时间' });
    const latencyHeader = screen.getByRole('button', { name: '耗时' });
    expect(timeHeader.closest('th')).toHaveAttribute('aria-sort', 'descending');
    expect(listQueries.at(-1)?.get('sort_by')).toBe('started_at');
    expect(listQueries.at(-1)?.get('sort_order')).toBe('desc');

    // 切到耗时列默认倒序，原时间列回到未排序状态
    await user.click(latencyHeader);
    await waitFor(() =>
      expect(
        listQueries.some(
          (query) => query.get('sort_by') === 'duration_ms' && query.get('sort_order') === 'desc',
        ),
      ).toBe(true),
    );
    expect(latencyHeader.closest('th')).toHaveAttribute('aria-sort', 'descending');
    expect(timeHeader.closest('th')).toHaveAttribute('aria-sort', 'none');

    // 再次点击同一列切换为升序
    await user.click(latencyHeader);
    await waitFor(() =>
      expect(
        listQueries.some(
          (query) => query.get('sort_by') === 'duration_ms' && query.get('sort_order') === 'asc',
        ),
      ).toBe(true),
    );
    expect(latencyHeader.closest('th')).toHaveAttribute('aria-sort', 'ascending');

    // 切回时间列时恢复默认倒序
    const beforeTimeClick = listQueries.length;
    await user.click(timeHeader);
    await waitFor(() =>
      expect(
        listQueries
          .slice(beforeTimeClick)
          .some(
            (query) =>
              query.get('sort_by') === 'started_at' && query.get('sort_order') === 'desc',
          ),
      ).toBe(true),
    );
    expect(timeHeader.closest('th')).toHaveAttribute('aria-sort', 'descending');
  });

  it('自定义时间范围在条件胶囊里显示实际起止时间', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <ObservabilityPage />
      </MemoryRouter>,
    );

    await screen.findByRole('button', { name: '查询 CPU 趋势' });
    await user.click(screen.getByRole('combobox', { name: '时间范围' }));
    await user.click(await screen.findByRole('option', { name: '自定义范围' }));

    fireEvent.change(screen.getByLabelText('开始时间'), {
      target: { value: '2026-11-14T09:00' },
    });
    fireEvent.change(screen.getByLabelText('结束时间'), {
      target: { value: '2026-11-14T18:30' },
    });

    expect(
      screen.getByText('时间：2026/11/14 09:00 → 2026/11/14 18:30'),
    ).toBeInTheDocument();
    await waitFor(() => {
      const startedFrom = listQueries.at(-1)?.get('started_from');
      const startedTo = listQueries.at(-1)?.get('started_to');
      expect(startedFrom).not.toBeNull();
      expect(startedTo).not.toBeNull();
      expect(new Date(startedFrom ?? '').getTime()).toBe(new Date('2026-11-14T09:00').getTime());
      expect(new Date(startedTo ?? '').getTime()).toBe(new Date('2026-11-14T18:30').getTime());
    });
  });
});
