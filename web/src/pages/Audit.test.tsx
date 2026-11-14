import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AuditPage from './Audit';
import { server } from '@/test/msw-server';

const perms = vi.hoisted(() => ({ isAdmin: true }));

vi.mock('@/store/me', () => ({
  usePermissions: () => ({
    role: perms.isAdmin ? 'admin' : 'developer',
    isAdmin: perms.isAdmin,
    isDeveloper: !perms.isAdmin,
    isViewer: false,
    canMutate: true,
  }),
}));

const ROWS = [
  {
    id: 2,
    actor_user_id: 1,
    actor_email: 'admin@local',
    action: 'approval.approve',
    resource_type: 'approval',
    resource_id: 'ap-1',
    detail: { reason: 'ok' },
    request_id: 'req-2',
    created_at: '2026-11-09T04:00:00.000Z',
  },
  {
    id: 1,
    actor_user_id: 1,
    actor_email: 'admin@local',
    action: 'user.create',
    resource_type: 'user',
    resource_id: '3',
    detail: { email: 'new@local', role: 'developer' },
    request_id: 'req-1',
    created_at: '2026-11-09T03:00:00.000Z',
  },
];

const requests: URLSearchParams[] = [];

describe('AuditPage', () => {
  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
    perms.isAdmin = true;
    requests.length = 0;
    server.use(
      http.get('/api/v1/users/options', () =>
        HttpResponse.json([
          { id: 1, email: 'admin@local' },
          { id: 2, email: 'dev@local' },
        ]),
      ),
      http.get('/api/v1/audit', ({ request }) => {
        const url = new URL(request.url);
        requests.push(url.searchParams);
        const sortDir = url.searchParams.get('sort_dir') ?? 'desc';
        const items = sortDir === 'asc' ? [...ROWS].reverse() : ROWS;
        return HttpResponse.json({
          items,
          total: items.length,
          actions: ['approval.approve', 'user.create'],
          resource_types: ['approval', 'user'],
        });
      }),
    );
  });

  it('lists audit records with readable action labels', async () => {
    render(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('批准审批')).toBeInTheDocument();
    expect(screen.getByText('创建用户')).toBeInTheDocument();
    expect(screen.getAllByText('admin@local')).toHaveLength(2);
    expect(requests[0].get('limit')).toBe('20');
    expect(requests[0].get('sort_dir')).toBe('desc');
  });

  it('toggles time sorting and refetches ascending', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>,
    );

    await screen.findByText('批准审批');
    const timeHeader = screen.getByRole('columnheader', { name: /时间/ });
    await user.click(within(timeHeader).getByRole('button'));

    await waitFor(() => expect(requests.some((p) => p.get('sort_dir') === 'asc')).toBe(true));
  });

  it('opens the detail dialog with parsed detail JSON', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>,
    );

    await screen.findByText('批准审批');
    // 详情入口统一为「点动作名」：不再有独立的「详情」按钮列。
    await user.click(screen.getByRole('button', { name: '批准审批' }));

    expect(await screen.findByText('审计详情')).toBeInTheDocument();
    expect(screen.getByText('req-2')).toBeInTheDocument();
    expect(screen.getByText(/"reason": "ok"/)).toBeInTheDocument();
  });

  it('exports the filtered rows as a redacted CSV', async () => {
    const user = userEvent.setup();
    const exportReqs: URLSearchParams[] = [];
    server.use(
      http.get('/api/v1/audit/export', ({ request }) => {
        exportReqs.push(new URL(request.url).searchParams);
        return new HttpResponse('\ufeff时间,操作者,动作\n2026-11-09,admin@local,user.create\n', {
          headers: {
            'content-type': 'text/csv; charset=utf-8',
            'content-disposition': 'attachment; filename="audit-log.csv"',
          },
        });
      }),
    );
    // jsdom 没有 blob URL 实现，这里只验证导出链路被触发。
    const createObjectURL = vi.fn(() => 'blob:audit');
    const revokeObjectURL = vi.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    // jsdom 不支持真实下载导航：拦掉 anchor.click 避免未实现告警。
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    render(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>,
    );

    await screen.findByText('批准审批');
    await user.click(screen.getByRole('button', { name: /导出审计/ }));

    await waitFor(() => expect(exportReqs).toHaveLength(1));
    expect(exportReqs[0].get('sort_dir')).toBe('desc');
    expect(exportReqs[0].get('action')).toBeNull();
    // 脱敏是服务端唯一口径：前端不传开关，也没有办法关掉。
    expect(exportReqs[0].get('redact')).toBeNull();
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1));
    expect(anchorClick).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledTimes(1);
    anchorClick.mockRestore();
  });

  it('filters by actor', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>,
    );

    await screen.findByText('批准审批');
    await user.click(screen.getByRole('combobox', { name: '操作者' }));
    await user.click(await screen.findByRole('option', { name: 'dev@local' }));

    await waitFor(() =>
      expect(requests.some((p) => p.get('actor_user_id') === '2')).toBe(true),
    );
  });

  it('gates the page behind admin permission', async () => {
    perms.isAdmin = false;
    render(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('需要管理员权限')).toBeInTheDocument();
    expect(requests).toHaveLength(0);
  });

  it('新增审计动作全部中文化，动态工作流决策按词典兜底', async () => {
    server.use(
      http.get('/api/v1/audit', () =>
        HttpResponse.json({
          items: [
            {
              id: 11,
              actor_user_id: 1,
              actor_email: 'admin@local',
              action: 'session.create',
              resource_type: 'chat_session',
              resource_id: 's-1',
              detail: {},
              request_id: 'r-11',
              created_at: '2026-11-09T05:00:00.000Z',
            },
            {
              id: 12,
              actor_user_id: 1,
              actor_email: 'admin@local',
              action: 'memory.delete',
              resource_type: 'memory',
              resource_id: 'm-1',
              detail: {},
              request_id: 'r-12',
              created_at: '2026-11-09T05:01:00.000Z',
            },
            {
              id: 13,
              actor_user_id: 1,
              actor_email: 'admin@local',
              action: 'eval.gate',
              resource_type: 'eval_run',
              resource_id: 'run-1',
              detail: {},
              request_id: 'r-13',
              created_at: '2026-11-09T05:02:00.000Z',
            },
            {
              id: 14,
              actor_user_id: 1,
              actor_email: 'admin@local',
              action: 'workflow.run_approve',
              resource_type: 'workflow_run',
              resource_id: 'run-2',
              detail: {},
              request_id: 'r-14',
              created_at: '2026-11-09T05:03:00.000Z',
            },
            {
              id: 15,
              actor_user_id: 1,
              actor_email: 'admin@local',
              action: 'llm.test',
              resource_type: 'llm_provider',
              resource_id: 'deepseek',
              detail: {},
              request_id: 'r-15',
              created_at: '2026-11-09T05:04:00.000Z',
            },
          ],
          total: 5,
          actions: ['session.create', 'memory.delete', 'eval.gate', 'workflow.run_approve', 'llm.test'],
          resource_types: ['chat_session', 'memory', 'eval_run', 'workflow_run', 'llm_provider'],
        }),
      ),
    );
    render(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('创建会话')).toBeInTheDocument();
    expect(screen.getByText('删除记忆')).toBeInTheDocument();
    expect(screen.getByText('评测门禁判定')).toBeInTheDocument();
    expect(screen.getByText('工作流运行·通过')).toBeInTheDocument();
    expect(screen.getByText('测试模型连接')).toBeInTheDocument();
    // 资源列同样中文化
    expect(screen.getByText('评测运行')).toBeInTheDocument();
  });

  it('详情弹窗的动作/资源走中文标签并保留原文', async () => {
    const user = userEvent.setup();
    server.use(
      http.get('/api/v1/audit', () =>
        HttpResponse.json({
          items: [
            {
              id: 21,
              actor_user_id: 1,
              actor_email: 'admin@local',
              action: 'workflow.run_reject',
              resource_type: 'workflow_run',
              resource_id: 'run-9',
              detail: {},
              request_id: 'r-21',
              created_at: '2026-11-09T06:00:00.000Z',
            },
          ],
          total: 1,
          actions: ['workflow.run_reject'],
          resource_types: ['workflow_run'],
        }),
      ),
    );
    render(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>,
    );

    await screen.findByText('工作流运行·驳回');
    await user.click(screen.getByRole('button', { name: '工作流运行·驳回' }));
    expect(await screen.findByText('审计详情')).toBeInTheDocument();
    // 标签 + 原文并存：既可读，又可核对原始 action
    expect(screen.getByText('workflow.run_reject')).toBeInTheDocument();
    // 资源标签在表格与弹窗各出现一次
    expect(screen.getAllByText('工作流运行')).toHaveLength(2);
  });
});
