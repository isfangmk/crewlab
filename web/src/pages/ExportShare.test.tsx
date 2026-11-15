// 导出与分享管理页测试：列表与筛选参数、撤销确认流、行内获取新链接。
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ExportSharePage from './ExportShare';
import { server } from '@/test/msw-server';

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

const rows = [
  {
    id: 1,
    kind: 'session',
    target_id: 's-1',
    title: '告警排查会话',
    created_by: 1,
    creator: 'admin@local',
    created_at: '2026-11-15T10:00:00+00:00',
    expires_at: '2026-12-01T10:00:00+00:00',
    expired: false,
  },
  {
    id: 2,
    kind: 'eval',
    target_id: 'run-1',
    title: '回归数据集',
    created_by: 1,
    creator: 'admin@local',
    created_at: '2026-11-10T10:00:00+00:00',
    expires_at: '2026-11-11T10:00:00+00:00',
    expired: true,
  },
];

function renderPage() {
  return render(
    <MemoryRouter>
      <ExportSharePage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  });
});

describe('ExportShare 管理页', () => {
  it('列表渲染类型/标题/状态胶囊', async () => {
    server.use(
      http.get('/api/v1/shares', () => HttpResponse.json({ items: rows, total: 2 })),
    );
    renderPage();
    expect(await screen.findByText('告警排查会话')).toBeInTheDocument();
    expect(screen.getByText('回归数据集')).toBeInTheDocument();
    // 「有效/已过期」同时出现在筛选胶囊与状态胶囊里
    expect(screen.getAllByText('有效').length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText('已过期').length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText('admin@local')).toHaveLength(2);
  });

  it('类型/状态筛选与标题搜索都透传进请求', async () => {
    const captured: Record<string, string>[] = [];
    server.use(
      http.get('/api/v1/shares', ({ request }) => {
        captured.push(Object.fromEntries(new URL(request.url).searchParams));
        return HttpResponse.json({ items: rows, total: 2 });
      }),
    );
    renderPage();
    await screen.findByText('告警排查会话');

    await userEvent.click(screen.getByRole('button', { name: '评测' }));
    await waitFor(() => expect(captured.at(-1)?.kind).toBe('eval'));

    await userEvent.click(screen.getByRole('button', { name: '已过期' }));
    await waitFor(() => expect(captured.at(-1)?.status).toBe('expired'));

    await userEvent.type(screen.getByRole('searchbox'), '告警');
    await waitFor(() => expect(captured.at(-1)?.q).toBe('告警'));
  });

  it('撤销走确认弹窗，确认后发 DELETE 并刷新', async () => {
    const deletes: string[] = [];
    let calls = 0;
    server.use(
      http.get('/api/v1/shares', () => {
        calls += 1;
        // 撤销后刷新：只剩另一条
        return HttpResponse.json(
          calls === 1 ? { items: rows, total: 2 } : { items: [rows[1]], total: 1 },
        );
      }),
      http.delete('/api/v1/shares/session/1', () => {
        deletes.push('session/1');
        return HttpResponse.json({ ok: true });
      }),
    );
    renderPage();
    await screen.findByText('告警排查会话');

    await userEvent.click(screen.getAllByRole('button', { name: '撤销分享' })[0]);
    expect(await screen.findByText('撤销分享链接')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '撤销' }));

    await waitFor(() => expect(deletes).toEqual(['session/1']));
    await waitFor(() => expect(screen.queryByText('告警排查会话')).not.toBeInTheDocument());
  });

  it('行内「获取链接」生成新分享并复制，旧链接不自动作废', async () => {
    const posts: unknown[] = [];
    server.use(
      http.get('/api/v1/shares', () => HttpResponse.json({ items: rows, total: 2 })),
      http.get('/api/v1/chat/sessions', () => HttpResponse.json({ items: [], total: 0 })),
      http.get('/api/v1/evals/datasets', () => HttpResponse.json({ items: [], total: 0 })),
      http.post('/api/v1/chat/sessions/s-1/share', async ({ request }) => {
        posts.push(await request.json());
        return HttpResponse.json({
          token: 'raw-token-1',
          expires_at: '2026-11-17T10:00:00+00:00',
          session_id: 's-1',
        });
      }),
    );
    renderPage();
    await screen.findByText('告警排查会话');

    await userEvent.click(screen.getAllByRole('button', { name: '获取链接' })[0]);
    expect(await screen.findByText('获取分享链接')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '生成并复制链接' }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({ expires_in_hours: 24 });
    const link = (await screen.findByLabelText('分享链接')) as HTMLInputElement;
    expect(link.value).toContain('/share/raw-token-1');
    expect((navigator.clipboard.writeText as ReturnType<typeof vi.fn>)).toHaveBeenCalledWith(
      expect.stringContaining('/share/raw-token-1'),
    );
  });
});
