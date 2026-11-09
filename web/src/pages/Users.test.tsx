import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import UsersPage from './Users';
import { server } from '@/test/msw-server';

const perms = vi.hoisted(() => ({ isAdmin: true }));

vi.mock('@/store/me', () => ({
  useMe: () => ({
    me: { id: 1, email: 'admin@local', display_name: 'Admin', role: 'admin', status: 'active' },
    loading: false,
    error: null,
    refresh: async () => {},
  }),
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

const roleBodies: { id: number; role: string }[] = [];
const passwordBodies: { id: number; password: string }[] = [];
const deletedIds: number[] = [];

describe('UsersPage', () => {
  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
    perms.isAdmin = true;
    roleBodies.length = 0;
    passwordBodies.length = 0;
    deletedIds.length = 0;
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
      http.patch('/api/v1/users/:id/role', async ({ request, params }) => {
        const body = (await request.json()) as { role: string };
        const id = Number(params.id);
        const found = USERS.find((u) => u.id === id);
        if (!found) return new HttpResponse(null, { status: 404 });
        roleBodies.push({ id, role: body.role });
        return HttpResponse.json({ ...found, role: body.role });
      }),
      http.post('/api/v1/users/:id/password', async ({ request, params }) => {
        const body = (await request.json()) as { password: string };
        passwordBodies.push({ id: Number(params.id), password: body.password });
        return new HttpResponse(null, { status: 204 });
      }),
      http.delete('/api/v1/users/:id', ({ params }) => {
        deletedIds.push(Number(params.id));
        return new HttpResponse(null, { status: 204 });
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

  it('changes a member role from the row dialog', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <UsersPage />
      </MemoryRouter>,
    );

    await screen.findByText('dev@local');
    await user.click(screen.getAllByRole('button', { name: /改角色/ })[1]);
    expect(await screen.findByText('账号：dev@local')).toBeInTheDocument();

    await user.click(screen.getByRole('combobox', { name: '角色' }));
    await user.click(await screen.findByRole('option', { name: '只读' }));
    await user.click(screen.getByRole('button', { name: /^保存$/ }));

    await waitFor(() => expect(roleBodies).toEqual([{ id: 2, role: 'viewer' }]));
    await waitFor(() => expect(screen.queryByText('修改角色')).not.toBeInTheDocument());
    expect(screen.getAllByText('只读').length).toBeGreaterThan(0);
  });

  it('resets a member password', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <UsersPage />
      </MemoryRouter>,
    );

    await screen.findByText('dev@local');
    await user.click(screen.getAllByRole('button', { name: '重置密码' })[1]);
    await user.type(await screen.findByLabelText('新密码'), 'new-pass-789');
    await user.click(screen.getByRole('button', { name: /^重置$/ }));

    await waitFor(() =>
      expect(passwordBodies).toEqual([{ id: 2, password: 'new-pass-789' }]),
    );
    await waitFor(() => expect(screen.queryByText('新密码')).not.toBeInTheDocument());
  });

  it('deletes a member after confirmation', async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(
      <MemoryRouter>
        <UsersPage />
      </MemoryRouter>,
    );

    await screen.findByText('dev@local');
    await user.click(screen.getAllByRole('button', { name: '删除账号' })[1]);

    await waitFor(() => expect(deletedIds).toEqual([2]));
    await waitFor(() => expect(screen.queryByText('dev@local')).not.toBeInTheDocument());
    confirmSpy.mockRestore();
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
