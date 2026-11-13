import { render, screen, waitFor, within } from '@testing-library/react';
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

const USER_SEED = [
  { id: 1, email: 'admin@local', display_name: 'Admin', role: 'admin', status: 'active' },
  { id: 2, email: 'dev@local', display_name: 'Dev', role: 'developer', status: 'active' },
];

// 测试内的用户表：每个用例从种子重建，避免上一个用例的写入影响下一个
let USERS: typeof USER_SEED = [];

const roleBodies: { id: number; role: string }[] = [];
const passwordBodies: { id: number; password: string }[] = [];
const deletedIds: number[] = [];

describe('UsersPage', () => {
  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
    perms.isAdmin = true;
    USERS = USER_SEED.map((u) => ({ ...u }));
    roleBodies.length = 0;
    passwordBodies.length = 0;
    deletedIds.length = 0;
    server.use(
      http.get('/api/v1/users', ({ request }) => {
        // 模拟后端的服务端筛选与分页：按 q / role / status 过滤，再按 limit / offset 切片
        const url = new URL(request.url);
        const q = url.searchParams.get('q');
        const role = url.searchParams.get('role');
        const status = url.searchParams.get('status');
        const limit = Number(url.searchParams.get('limit') ?? '20');
        const offset = Number(url.searchParams.get('offset') ?? '0');
        let matched = USERS.filter((u) => {
          if (role !== null && u.role !== role) return false;
          if (status !== null && u.status !== status) return false;
          if (q !== null && q !== '') {
            const needle = q.toLowerCase();
            if (!u.email.toLowerCase().includes(needle) && !u.display_name.toLowerCase().includes(needle)) {
              return false;
            }
          }
          return true;
        });
        matched = [...matched].sort((a, b) => a.id - b.id);
        return HttpResponse.json({
          items: matched.slice(offset, offset + limit),
          total: matched.length,
        });
      }),
      http.post('/api/v1/users', async ({ request }) => {
        const body = (await request.json()) as {
          email: string;
          display_name: string;
          role: string;
        };
        const created = {
          id: 3,
          email: body.email,
          display_name: body.display_name,
          role: body.role,
          status: 'active',
        };
        USERS.push(created);
        return HttpResponse.json(created);
      }),
      http.post('/api/v1/users/:id/status', async ({ request, params }) => {
        const body = (await request.json()) as { status: string };
        const id = Number(params.id);
        const found = USERS.find((u) => u.id === id);
        if (!found) return new HttpResponse(null, { status: 404 });
        found.status = body.status;
        return HttpResponse.json({ ...found });
      }),
      http.patch('/api/v1/users/:id/role', async ({ request, params }) => {
        const body = (await request.json()) as { role: string };
        const id = Number(params.id);
        const found = USERS.find((u) => u.id === id);
        if (!found) return new HttpResponse(null, { status: 404 });
        roleBodies.push({ id, role: body.role });
        found.role = body.role;
        return HttpResponse.json({ ...found });
      }),
      http.post('/api/v1/users/:id/password', async ({ request, params }) => {
        const body = (await request.json()) as { password: string };
        passwordBodies.push({ id: Number(params.id), password: body.password });
        return new HttpResponse(null, { status: 204 });
      }),
      http.delete('/api/v1/users/:id', ({ params }) => {
        const id = Number(params.id);
        deletedIds.push(id);
        USERS = USERS.filter((u) => u.id !== id);
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
    // 状态走 StatusPill（圆点 + 语义色），两个账号都是「启用」
    expect(screen.getAllByText('启用')).toHaveLength(2);
    expect(screen.getAllByText('启用').every((el) => el.closest('span.rounded-full'))).toBe(true);
  });

  it('filters accounts by keyword and by role', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <UsersPage />
      </MemoryRouter>,
    );

    await screen.findByText('admin@local');

    // 关键字搜索：只留邮箱命中的那一行
    await user.type(screen.getByLabelText('搜索'), 'dev@');
    await waitFor(() => expect(screen.queryByText('admin@local')).not.toBeInTheDocument());
    expect(screen.getByText('dev@local')).toBeInTheDocument();

    // 清空搜索后按角色筛选
    await user.click(screen.getByRole('button', { name: '清空搜索' }));
    await user.click(screen.getByRole('combobox', { name: '角色筛选' }));
    await user.click(await screen.findByRole('option', { name: '管理员' }));

    await waitFor(() => expect(screen.queryByText('dev@local')).not.toBeInTheDocument());
    expect(screen.getByText('admin@local')).toBeInTheDocument();
  });

  it('filters accounts by status and shows an empty state when nothing matches', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <UsersPage />
      </MemoryRouter>,
    );

    await screen.findByText('admin@local');

    await user.click(screen.getByRole('combobox', { name: '状态筛选' }));
    await user.click(await screen.findByRole('option', { name: '已禁用' }));

    expect(await screen.findByText('没有匹配的用户')).toBeInTheDocument();
    expect(screen.queryByText('admin@local')).not.toBeInTheDocument();
  });

  it('paginates accounts on the server and jumps between pages', async () => {
    const many = Array.from({ length: 25 }, (_, i) => ({
      id: i + 1,
      email: `user${i + 1}@local`,
      display_name: `User ${i + 1}`,
      role: 'viewer' as const,
      status: 'active' as const,
    }));
    server.use(
      http.get('/api/v1/users', ({ request }) => {
        const url = new URL(request.url);
        const limit = Number(url.searchParams.get('limit') ?? '20');
        const offset = Number(url.searchParams.get('offset') ?? '0');
        return HttpResponse.json({
          items: many.slice(offset, offset + limit),
          total: many.length,
        });
      }),
    );

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <UsersPage />
      </MemoryRouter>,
    );

    // 默认 20 条/页：只渲染第一页的 20 行
    await screen.findByText('user1@local');
    expect(screen.getByText('user20@local')).toBeInTheDocument();
    expect(screen.queryByText('user21@local')).not.toBeInTheDocument();
    expect(screen.getByText('1-20 / 共 25 条')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '下一页' }));

    await waitFor(() => expect(screen.getByText('user21@local')).toBeInTheDocument());
    expect(screen.queryByText('user1@local')).not.toBeInTheDocument();
    expect(screen.getByText('21-25 / 共 25 条')).toBeInTheDocument();
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

    // 删除走统一确认弹窗（不再用原生 confirm）
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('dev@local')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: '删除' }));

    await waitFor(() => expect(deletedIds).toEqual([2]));
    await waitFor(() => expect(screen.queryByText('dev@local')).not.toBeInTheDocument());
    expect(confirmSpy).not.toHaveBeenCalled();
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
