// 用量与成本看板测试 — 成本口径与软限额链路：
//   1. 缺手动单价的模型成本显示「—」+ 未计价回合，不按 0 计
//   2. 今日 token 触达软限额时出警告横幅（只提醒不拦截）
//   3. 模型筛选透传进请求；admin 可保存软限额
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import UsagePage from './Usage';
import { server } from '@/test/msw-server';

// client.ts 只需要 token 取值与 401 兜底；测试里全部打桩。
vi.mock('@/store/auth', () => ({
  useAuth: Object.assign(
    <T,>(selector: (s: { role: string }) => T): T => selector({ role: 'admin' }),
    { getState: () => ({ logout: () => {} }) },
  ),
  getToken: () => null,
}));
vi.mock('@/store/me', () => ({
  usePermissions: () => ({ isAdmin: true, canMutate: true, role: 'admin' }),
}));
// echarts 画布是纯展示边界：jsdom 无 canvas，直接以空组件代替
vi.mock('@/components/ChartCanvas', () => ({
  ChartCanvas: () => null,
  CHART_HEIGHT_CLASS: 'h-[18rem]',
}));

const summaryURL = '/api/v1/usage/summary';
const limitURL = '/api/v1/system-settings/usage/daily_token_soft_limit';

function makeSummary(overrides: Record<string, unknown> = {}) {
  return {
    stats: {
      turns: 3,
      prompt_tokens: 4_000_000,
      completion_tokens: 2_000_000,
      total_tokens: 6_000_000,
      duration_avg_ms: 1234,
      cost: null,
      unpriced_turns: 1,
      unpriced_tokens: 2_000_000,
    },
    by_day: [
      {
        key: '2026-11-15',
        turns: 3,
        prompt_tokens: 4_000_000,
        completion_tokens: 2_000_000,
        total_tokens: 6_000_000,
        duration_avg_ms: 1234,
        cost: null,
        unpriced_turns: 1,
      },
    ],
    by_model: [
      {
        key: 'model-a',
        turns: 2,
        prompt_tokens: 3_000_000,
        completion_tokens: 1_000_000,
        total_tokens: 4_000_000,
        duration_avg_ms: 1000,
        cost: 17.5,
        unpriced_turns: 0,
      },
      {
        key: 'model-b',
        turns: 1,
        prompt_tokens: 1_000_000,
        completion_tokens: 1_000_000,
        total_tokens: 2_000_000,
        duration_avg_ms: 1500,
        cost: null,
        unpriced_turns: 1,
      },
    ],
    by_agent: [
      {
        key: 'agent-x',
        turns: 3,
        prompt_tokens: 4_000_000,
        completion_tokens: 2_000_000,
        total_tokens: 6_000_000,
        duration_avg_ms: 1234,
        cost: null,
        unpriced_turns: 1,
      },
    ],
    facets: { models: ['model-a', 'model-b'], agents: ['agent-x'], channels: [''] },
    soft_limit_tokens: 5000,
    today_tokens: 6_000_000,
    ...overrides,
  };
}

describe('UsagePage', () => {
  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
  });

  it('缺价目成本显示「—」并标注未计价回合', async () => {
    const calls: string[] = [];
    server.use(
      http.get(summaryURL, ({ request }) => {
        calls.push(request.url);
        return HttpResponse.json(makeSummary());
      }),
    );
    render(<UsagePage />);

    expect(await screen.findByText('未计价回合')).toBeInTheDocument();
    // 总成本有未计价回合 → 「—」，不给半真合计
    expect(screen.getByText('1 回合未计价')).toBeInTheDocument();
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(1);

    // 按模型表：价目模型给数，缺价目给「—」
    const modelSection = screen.getByText('按模型').closest('div')!;
    const modelTable = within(modelSection).getByRole('table');
    expect(within(modelTable).getByText('17.50')).toBeInTheDocument();
    expect(within(modelTable).getAllByText('—').length).toBeGreaterThanOrEqual(1);
  });

  it('今日 token 触达软限额时出警告横幅', async () => {
    server.use(
      http.get(summaryURL, () =>
        HttpResponse.json(makeSummary({ soft_limit_tokens: 5000, today_tokens: 6_000_000 })),
      ),
    );
    render(<UsagePage />);

    expect(await screen.findByText(/今日 token 已达软限额/)).toBeInTheDocument();
  });

  it('模型筛选透传；admin 保存软限额', async () => {
    const user = userEvent.setup();
    const calls: string[] = [];
    const puts: { url: string; body: unknown }[] = [];
    server.use(
      http.get(summaryURL, ({ request }) => {
        calls.push(request.url);
        return HttpResponse.json(makeSummary());
      }),
      http.put(limitURL, async ({ request }) => {
        puts.push({ url: request.url, body: await request.json() });
        return HttpResponse.json({ category: 'usage', key: 'daily_token_soft_limit', value: '8000', sensitive: false });
      }),
    );
    render(<UsagePage />);

    await screen.findByText('未计价回合');
    await user.click(screen.getByRole('combobox', { name: '模型' }));
    await user.click(await screen.findByRole('option', { name: 'model-a' }));
    await waitFor(() => expect(calls.length).toBeGreaterThanOrEqual(2));
    expect(new URL(calls[calls.length - 1]).searchParams.get('model')).toBe('model-a');

    const limit = screen.getByRole('spinbutton', { name: '日 token 软限额' });
    await user.clear(limit);
    await user.type(limit, '8000');
    await user.click(screen.getByRole('button', { name: '保存限额' }));
    await waitFor(() => expect(puts.length).toBe(1));
    expect(puts[0].url).toContain('/system-settings/usage/daily_token_soft_limit');
    expect(puts[0].body).toEqual({ value: '8000' });
  });
});
