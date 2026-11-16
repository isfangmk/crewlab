// 排查工作台测试：快照渲染（统计/worker 卡/底部动作）、重试请求、事件流日志追加。
// REST 走 msw，SSE 订阅打桩（WorkflowRun.test 同款口径）。
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { InvestigationWorkbench } from './InvestigationWorkbench';
import type { InvestigationDispatch, InvestigationStreamEvent } from '@/api/investigation';
import { server } from '@/test/msw-server';

vi.mock('@/api/investigation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/investigation')>()),
  subscribeDispatchEvents: vi.fn(async () => {}),
}));

vi.mock('@/store/auth', () => ({
  useAuth: Object.assign(
    <T,>(selector: (s: { role: string }) => T): T => selector({ role: 'admin' }),
    { getState: () => ({ logout: () => {} }) },
  ),
  getToken: () => null,
}));
vi.mock('@/store/me', () => ({
  usePermissions: () => ({
    role: 'admin',
    isAdmin: true,
    isDeveloper: false,
    isViewer: false,
    canMutate: true,
  }),
}));

import { subscribeDispatchEvents } from '@/api/investigation';

const wire: InvestigationDispatch = {
  id: 12,
  dispatch_uid: 'd-1',
  session_id: 's-1',
  question: '过去 24 小时告警异常',
  status: 'running',
  config: { token_budget_per_worker: 8000 },
  budget: { used_tokens_total: 4200, token_budget_per_worker: 8000 },
  findings: [
    { n: 1, worker_uid: 'w-1', retired: false, title: 'Redis 内存爬升', detail: '78% → 85.1%', evidence: [{ call_id: 'c-1', tool: 'query_metrics', excerpt: 'mem=85.1' }] },
  ],
  stats: { total: 2, succeeded: 1, failed: 0, stopped: 0, running: 1, findings: 1, tool_calls: 3 },
  workers: [
    {
      worker_uid: 'w-1',
      name: '指标员',
      target: '关联指标趋势',
      attempt: 1,
      status: 'succeeded',
      conclusion: 'Redis 内存持续爬升未回落',
      findings: [],
      log_text: '开始排查：指标员 / 关联指标趋势',
      error: '',
      budget: {},
      duration_ms: 8200,
      created_at: '2026-11-16T10:00:00+00:00',
      finished_at: '2026-11-16T10:00:08+00:00',
    },
    {
      worker_uid: 'w-2',
      name: '变更员',
      target: '发布/配置变更',
      attempt: 1,
      status: 'running',
      conclusion: '',
      findings: [],
      log_text: '拉取近 24h 发布记录',
      error: '',
      budget: {},
      duration_ms: 42000,
      created_at: '2026-11-16T10:00:00+00:00',
      finished_at: null,
    },
  ],
  error: '',
  duration_ms: 0,
  created_by: 1,
  created_at: '2026-11-16T10:00:00+00:00',
  finished_at: null,
};

function handlers() {
  return [
    http.get('/api/v1/investigations', () => HttpResponse.json({ items: [wire], total: 1 })),
    http.get('/api/v1/investigations/d-1', () => HttpResponse.json(wire)),
    http.post('/api/v1/investigations/d-1/stop', () => HttpResponse.json({ stopped: true })),
    http.post('/api/v1/investigations/d-1/workers/:workerUid/retry', () =>
      HttpResponse.json({ worker_uid: 'w-1', status: 'running', attempt: 2 }),
    ),
  ];
}

beforeEach(() => {
  vi.mocked(subscribeDispatchEvents).mockReset();
  vi.mocked(subscribeDispatchEvents).mockResolvedValue(undefined);
  server.use(...handlers());
});

describe('InvestigationWorkbench', () => {
  it('渲染快照：问题、统计、worker 卡与底部动作', async () => {
    render(
      <InvestigationWorkbench sessionId="s-1" onClose={() => {}} />,
    );
    expect(await screen.findByText('过去 24 小时告警异常')).toBeInTheDocument();
    // 统计：1/2 完成 + 1 条发现
    expect(await screen.findByText('1/2')).toBeInTheDocument();
    expect(screen.getByText('指标员')).toBeInTheDocument();
    expect(screen.getByText('变更员')).toBeInTheDocument();
    // 运行中的路自动展开：日志可见
    expect(await screen.findByText('拉取近 24h 发布记录')).toBeInTheDocument();
    // 底部动作
    expect(screen.getByRole('button', { name: '全部停止' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '汇总结论回对话' })).toBeInTheDocument();
  });

  it('重试此路发出 POST 并刷新', async () => {
    const posts: string[] = [];
    const failedWire: InvestigationDispatch = {
      ...wire,
      status: 'failed',
      stats: { ...wire.stats, succeeded: 0, failed: 1, running: 0 },
      workers: wire.workers.map((w) =>
        w.worker_uid === 'w-1' ? { ...w, status: 'failed', conclusion: '', error: 'connect timeout' } : w,
      ),
    };
    server.use(
      http.get('/api/v1/investigations/d-1', () => HttpResponse.json(failedWire)),
      http.post('/api/v1/investigations/d-1/workers/w-1/retry', () => {
        posts.push('w-1');
        return HttpResponse.json({ worker_uid: 'w-1', status: 'running', attempt: 2 });
      }),
    );
    render(<InvestigationWorkbench sessionId="s-1" onClose={() => {}} />);
    // 失败的路自动展开，重试按钮可见
    await userEvent.click(await screen.findByRole('button', { name: '重试此路' }));
    await waitFor(() => expect(posts).toEqual(['w-1']));
  });

  it('事件流 worker_log 追加到实时日志', async () => {
    vi.mocked(subscribeDispatchEvents).mockImplementation(async (_uid, onEvent) => {
      onEvent({
        event: 'worker_log',
        dispatch_uid: 'd-1',
        worker_uid: 'w-2',
        line: '正在核对灰度与回滚记录…',
        tool: 'get_change',
      } satisfies InvestigationStreamEvent);
    });
    render(<InvestigationWorkbench sessionId="s-1" onClose={() => {}} />);
    expect(await screen.findByText('正在核对灰度与回滚记录…')).toBeInTheDocument();
  });

  it('无排查任务显示空态引导，不卡在加载中', async () => {
    server.use(
      http.get('/api/v1/investigations', () => HttpResponse.json({ items: [], total: 0 })),
    );
    render(<InvestigationWorkbench sessionId="s-1" onClose={() => {}} />);
    expect(await screen.findByText('本会话还没有排查任务')).toBeInTheDocument();
    expect(screen.queryByText('加载中…')).not.toBeInTheDocument();
  });
});
