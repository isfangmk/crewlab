// 排查折叠块与引用角标测试：折叠头计数/状态、展开每路结论、跳工作台定位、
// [n] 角标点击回调。API 层整体打桩（SSE 订阅是空实现）。
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { DispatchBlock } from './DispatchBlock';
import { MessageBubble } from '../MessageBubble';
import type { InvestigationDispatch } from '@/api/investigation';

vi.mock('@/api/investigation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/investigation')>()),
  getInvestigation: vi.fn(),
  subscribeDispatchEvents: vi.fn(async () => {}),
}));

vi.mock('@/store/auth', () => ({
  useAuth: Object.assign(
    <T,>(selector: (s: { role: string }) => T): T => selector({ role: 'admin' }),
    { getState: () => ({ logout: () => {} }) },
  ),
  getToken: () => null,
}));

import { getInvestigation } from '@/api/investigation';

const wire: InvestigationDispatch = {
  id: 12,
  dispatch_uid: 'd-1',
  session_id: 's-1',
  question: '过去 24 小时告警异常',
  status: 'succeeded',
  config: {},
  budget: { used_tokens_total: 9600, token_budget_per_worker: 8000 },
  findings: [
    { n: 1, worker_uid: 'w-1', retired: false, title: 'Redis 内存爬升', detail: '78% → 85.1%', evidence: [] },
    { n: 2, worker_uid: 'w-2', retired: false, title: '5xx 与发布重合', detail: 'v3.2.1', evidence: [] },
  ],
  stats: { total: 2, succeeded: 1, failed: 1, stopped: 0, running: 0, findings: 2, tool_calls: 4 },
  workers: [
    {
      worker_uid: 'w-1',
      name: '指标员',
      target: '关联指标趋势',
      attempt: 1,
      status: 'succeeded',
      conclusion: 'Redis 内存持续爬升未回落',
      findings: [],
      log_text: '',
      error: '',
      budget: {},
      duration_ms: 8200,
      created_at: null,
      finished_at: null,
    },
    {
      worker_uid: 'w-2',
      name: '历史员',
      target: '过往处置记录',
      attempt: 1,
      status: 'failed',
      conclusion: '',
      findings: [],
      log_text: '',
      error: 'connect timeout',
      budget: {},
      duration_ms: 30000,
      created_at: null,
      finished_at: null,
    },
  ],
  error: '',
  duration_ms: 30000,
  created_by: 1,
  created_at: '2026-11-16T10:00:00+00:00',
  finished_at: '2026-11-16T10:00:30+00:00',
};

const call = {
  name: 'dispatch_investigation',
  arguments: {
    question: '过去 24 小时告警异常',
    workers_json: JSON.stringify([
      { name: '指标员', target: '关联指标趋势' },
      { name: '历史员', target: '过往处置记录' },
    ]),
  },
  result: {
    dispatch_uid: 'd-1',
    status: 'succeeded',
    partial: false,
    workers: [
      { worker_uid: 'w-1', name: '指标员', status: 'succeeded', conclusion: 'Redis 内存持续爬升未回落', error: '', duration_ms: 8200 },
      { worker_uid: 'w-2', name: '历史员', status: 'failed', conclusion: '', error: 'connect timeout', duration_ms: 30000 },
    ],
    still_running: [],
    findings: wire.findings,
  },
};

beforeEach(() => {
  vi.mocked(getInvestigation).mockClear();
  vi.mocked(getInvestigation).mockResolvedValue(wire);
});

describe('DispatchBlock', () => {
  it('折叠头显示路数与计数，展开看每路结论', async () => {
    render(<DispatchBlock call={call} />);
    const head = screen.getByRole('button', { name: /并行排查 2 路/ });
    expect(head).toHaveAttribute('aria-expanded', 'false');
    // 计数在折叠头右侧
    expect(head.textContent).toContain('1 完成');
    expect(head.textContent).toContain('1 失败');

    await userEvent.click(head);
    expect(screen.getByText('Redis 内存持续爬升未回落')).toBeInTheDocument();
    expect(screen.getByText('指标员')).toBeInTheDocument();
    expect(screen.getByText('历史员')).toBeInTheDocument();
  });

  it('行尾定位按钮回调 workerUid', async () => {
    const onLocate = vi.fn();
    render(<DispatchBlock call={call} onLocate={onLocate} />);
    await userEvent.click(screen.getByRole('button', { name: /并行排查 2 路/ }));
    await userEvent.click(screen.getByRole('button', { name: /在工作台定位 指标员/ }));
    expect(onLocate).toHaveBeenCalledWith({ kind: 'worker', workerUid: 'w-1' });
  });

  it('pending 时折叠头显示进行中', () => {
    render(<DispatchBlock call={{ name: 'dispatch_investigation', arguments: call.arguments }} pending />);
    const head = screen.getByRole('button', { name: /并行排查 2 路/ });
    expect(head.textContent).toContain('进行中');
  });
});

describe('汇总引用角标', () => {
  it('正文 [n] 渲染为角标，点击回调 finding 编号', async () => {
    const onLocate = vi.fn();
    render(
      <MessageBubble
        message={{
          id: 'm-1',
          role: 'assistant',
          content: '源头是 v3.2.1 发布[2]，Redis 是慢性问题[1]。',
        }}
        findingCount={2}
        onLocate={onLocate}
      />,
    );
    const badge = screen.getByRole('button', { name: '引用发现 2' });
    await userEvent.click(badge);
    await waitFor(() => expect(onLocate).toHaveBeenCalledWith({ kind: 'finding', n: 2 }));
    // 另一枚角标也渲染出来了
    expect(screen.getByRole('button', { name: '引用发现 1' })).toBeInTheDocument();
  });

  it('没有排查发现时正文 [n] 保持原样', () => {
    render(<MessageBubble message={{ id: 'm-2', role: 'assistant', content: '结论见 [1]。' }} />);
    expect(screen.queryByRole('button', { name: /引用发现/ })).not.toBeInTheDocument();
  });
});

describe('快照兜底', () => {
  it('快照拉取失败时仍展示工具卡静态结果', async () => {
    vi.mocked(getInvestigation).mockRejectedValue(new Error('boom'));
    render(<DispatchBlock call={call} />);
    expect(await screen.findByText(/并行排查 · 2 路/)).toBeInTheDocument();
  });
});
