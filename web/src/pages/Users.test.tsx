import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import UsersPage from './Users';
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

const USERS = [
  { id: 1, email: 'admin@local', display_name: 'Admin', role: 'admin', status: 'active' },
  { id: 2, email: 'dev@local', display_name: 'Dev', role: 'developer', status: 'active' },
];

describe('UsersPage', () => {
  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
    perms.isAdmin = true;
    server.use(
      http.get('/api/v1/users', () => HttpResponse.json(USERS)),
      http.post('/api/v1/users', async ({ request }) => {
        const body = (await request.json()) as {
          email: string;
          display_name: string;
          role: string;
        };
        return HttpResponse.json({
          id: 3,
          email: body.email,
          display_name: body.display_name,
          role: body.role,
          status: 'active',
        });
      }),
      http.post('/api/v1/users/:id/status', async ({ request, params }) => {
        const body = (await request.json()) as { status: string };
        const id = Number(params.id);
        const found = USERS.find((u) => u.id === id);
        if (!found) return new HttpResponse(null, { status: 404 });
        return HttpResponse.json({ ...found, status: body.status });
      }),
    );
  });

  it('lists local accounts with roles and status', async () => {
    render(
      <MemoryRouter>
        <UsersPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('admin@local')).toBeInTheDocument();
    expect(screen.getByText('dev@local')).toBeInTheDocument();
    expect(screen.getByText('管理员')).toBeInTheDocument();
    expect(screen.getByText('开发者')).toBeInTheDocument();
    expect(screen.getAllByText('启用')).toHaveLength(2);
  });

  it('creates a user and appends it to the list', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <UsersPage />
      </MemoryRouter>,
    );

    await screen.findByText('admin@local');
    await user.click(screen.getByRole('button', { name: /新建用户/ }));

    await user.type(screen.getByLabelText('邮箱'), 'new@local');
    await user.type(screen.getByLabelText('显示名'), 'Newbie');
    await user.type(screen.getByLabelText('初始密码'), 'pass-123456');
    await user.click(screen.getByRole('button', { name: /^创建$/ }));

    expect(await screen.findByText('new@local')).toBeInTheDocument();
    expect(screen.getByText('Newbie')).toBeInTheDocument();
  });

  it('disables and re-enables an account', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <UsersPage />
      </MemoryRouter>,
    );

    await screen.findByText('dev@local');
    const disableButtons = screen.getAllByRole('button', { name: /禁用/ });
    await user.click(disableButtons[1]);

    await waitFor(() => expect(screen.getByText('已禁用')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /启用/ })).toBeInTheDocument();
  });

  it('gates the page behind admin permission', async () => {
    perms.isAdmin = false;
    render(
      <MemoryRouter>
        <UsersPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('需要管理员权限')).toBeInTheDocument();
    expect(screen.queryByText('admin@local')).not.toBeInTheDocument();
  });
});
