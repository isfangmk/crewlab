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
    await user.click(screen.getAllByRole('button', { name: '详情' })[0]);

    expect(await screen.findByText('审计详情')).toBeInTheDocument();
    expect(screen.getByText('req-2')).toBeInTheDocument();
    expect(screen.getByText(/"reason": "ok"/)).toBeInTheDocument();
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
});
