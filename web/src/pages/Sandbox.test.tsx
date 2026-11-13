// 沙箱页测试 — 列表 / 筛选 / 详情 / 手动运行的错误回显都走真实接口形状。
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it } from 'vitest';

import SandboxPage from './Sandbox';
import { server } from '@/test/msw-server';

const succeededRun = {
  id: 'exec-1',
  source: 'workflow',
  source_ref: 'note',
  session_id: '',
  workflow_run_id: 'run-abc',
  language: 'python',
  image: 'python:3.12-slim',
  network_enabled: false,
  code: "print(6 * 7)\nprint('done')",
  stdout: '42\ndone',
  stderr: '',
  exit_code: 0,
  status: 'succeeded',
  error: null,
  duration_ms: 4561,
  started_at: '2026-11-13T10:00:00Z',
  finished_at: '2026-11-13T10:00:04Z',
};

const failedRun = {
  ...succeededRun,
  id: 'exec-2',
  source: 'manual',
  source_ref: 'manual',
  workflow_run_id: '',
  code: 'raise SystemExit(3)',
  stdout: '',
  stderr: 'boom',
  exit_code: 3,
  status: 'failed',
  error: '沙盒执行失败，退出码 3',
  duration_ms: 820,
};

const summary = {
  total: 2,
  by_source: { workflow: 1, manual: 1 },
  by_status: { succeeded: 1, failed: 1 },
  avg_duration_ms: 2690,
};

function renderPage() {
  return render(
    <MemoryRouter>
      <SandboxPage />
    </MemoryRouter>,
  );
}

function useList(items: unknown[]) {
  server.use(
    http.get('/api/v1/sandbox/summary', () => HttpResponse.json(summary)),
    http.get('/api/v1/sandbox/executions', () => HttpResponse.json({ items, total: items.length })),
  );
}

describe('SandboxPage', () => {
  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
  });

  it('列表：代码首行做详情入口，来源与状态分别用 Chip / StatusPill', async () => {
    useList([succeededRun, failedRun]);
    renderPage();

    expect(await screen.findByRole('button', { name: 'print(6 * 7)' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'raise SystemExit(3)' })).toBeInTheDocument();
    // 来源是分类用 Chip，状态用 StatusPill（文案各自独立）
    expect(screen.getAllByText('工作流节点').length).toBeGreaterThan(0);
    expect(screen.getAllByText('手动试跑').length).toBeGreaterThan(0);
    // 文案同时出现在状态胶囊、筛选胶囊与指标卡上，这里只校验「渲染出来了」
    expect(screen.getAllByText('成功').length).toBeGreaterThan(0);
    expect(screen.getAllByText('失败').length).toBeGreaterThan(0);
    // 耗时按量级换单位，毫秒级也要可读
    expect(screen.getByText('4.6 s')).toBeInTheDocument();
    expect(screen.getByText('820 ms')).toBeInTheDocument();
  });

  it('搜索把关键字带到服务端，不把全表拉到前端过滤', async () => {
    const seen: string[] = [];
    server.use(
      http.get('/api/v1/sandbox/summary', () => HttpResponse.json(summary)),
      http.get('/api/v1/sandbox/executions', ({ request }) => {
        seen.push(new URL(request.url).searchParams.get('q') ?? '');
        return HttpResponse.json({ items: [succeededRun], total: 1 });
      }),
    );
    renderPage();
    await screen.findByRole('button', { name: 'print(6 * 7)' });

    await userEvent.type(screen.getByLabelText('搜索执行记录'), 'note');
    await waitFor(() => expect(seen).toContain('note'));
  });

  it('点代码打开详情：展示输出、错误与运行上下文', async () => {
    useList([failedRun]);
    renderPage();

    await userEvent.click(await screen.findByRole('button', { name: 'raise SystemExit(3)' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('标准错误')).toBeInTheDocument();
    expect(within(dialog).getByText('boom')).toBeInTheDocument();
    expect(within(dialog).getByText('错误')).toBeInTheDocument();
    expect(within(dialog).getByText('沙盒执行失败，退出码 3')).toBeInTheDocument();
    // 上下文里要能看出这次是断网跑的
    expect(within(dialog).getByText('已断网')).toBeInTheDocument();
  });

  it('没有记录时给空态，而不是空白表格', async () => {
    useList([]);
    renderPage();

    expect(await screen.findByText('还没有执行记录')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '跑一段试试' })).toBeInTheDocument();
  });

  it('运行被拒绝时把原因留在弹窗里', async () => {
    useList([succeededRun]);
    server.use(
      http.post('/api/v1/sandbox/run', () =>
        HttpResponse.json(
          { detail: '沙盒默认断网，无法安装依赖；请改用预装好依赖的镜像，或打开 SANDBOX_NETWORK_ENABLED 后重试' },
          { status: 400 },
        ),
      ),
    );
    renderPage();
    await screen.findByRole('button', { name: 'print(6 * 7)' });

    await userEvent.click(screen.getByRole('button', { name: '运行代码' }));
    const dialog = await screen.findByRole('dialog', { name: '运行代码' });
    await userEvent.click(within(dialog).getByRole('button', { name: '运行' }));

    expect(await within(dialog).findByRole('alert')).toHaveTextContent('沙盒默认断网');
    // 等「运行中」状态收尾，避免 React 在测试结束后才落地这次更新
    await waitFor(() => expect(within(dialog).getByRole('button', { name: '运行' })).toBeEnabled());
  });

  it('筛选条口径：计数用「共 N 条」，指标卡数量与网格列数一致', async () => {
    useList([succeededRun, failedRun]);
    renderPage();
    await screen.findByRole('button', { name: 'print(6 * 7)' });

    // 区间文案归 TablePager，筛选条只放总数（与 /users、/sessions 一致）
    expect(screen.getByText('共 2 条')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: '分页' })).toHaveTextContent('1-2 / 共 2 条');

    // 5 个指标配 5 列：行尾不能留空格子
    const metricRow = screen.getByText('执行总数').closest('.grid');
    expect(metricRow?.className).toContain('lg:grid-cols-5');
  });

  it('记录型列表不放行内操作列，详情只走代码名', async () => {
    useList([succeededRun, failedRun]);
    renderPage();
    await screen.findByRole('button', { name: 'print(6 * 7)' });

    const heads = [...document.querySelectorAll('thead th')].map((th) => th.textContent?.trim());
    expect(heads).toEqual(['代码 / 来源标识', '来源', '状态', '退出码', '耗时', '开始时间']);
  });

  it('点失败指标卡按状态筛选，请求带 status', async () => {
    const seen: string[] = [];
    server.use(
      http.get('/api/v1/sandbox/summary', () => HttpResponse.json(summary)),
      http.get('/api/v1/sandbox/executions', ({ request }) => {
        seen.push(new URL(request.url).searchParams.get('status') ?? '');
        return HttpResponse.json({ items: [failedRun], total: 1 });
      }),
    );
    renderPage();
    await screen.findByRole('button', { name: 'raise SystemExit(3)' });

    await userEvent.click(screen.getByRole('button', { name: /失败/ }));
    await waitFor(() => expect(seen).toContain('failed'));
  });

  it('详情里的执行 ID 点击即复制，复制结果用 Toast 提示', async () => {
    // userEvent.setup() 会装一个可读写的 clipboard stub，比手写 mock 更贴近真实交互
    const user = userEvent.setup();
    useList([failedRun]);
    renderPage();

    await user.click(await screen.findByRole('button', { name: 'raise SystemExit(3)' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: '复制执行 ID' }));

    await waitFor(async () => {
      expect(await navigator.clipboard.readText()).toBe('exec-2');
    });
    expect(await screen.findByText('已复制执行 ID')).toBeInTheDocument();
  });
});
