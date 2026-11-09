import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import SessionsPage from './Sessions';
import { server } from '@/test/msw-server';

const agentsHandler = http.get('/api/v1/agents', () =>
  HttpResponse.json({
    items: [{ name: 'default', description: '默认', when_to_use: '', source: 'builtin' }],
    total: 1,
  }),
);

describe('SessionsPage', () => {
  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
    server.use(
      agentsHandler,
      http.get('/api/v1/chat/sessions', ({ request }) => {
        const url = new URL(request.url);
        const q = url.searchParams.get('q')?.trim().toLowerCase() ?? '';
        const sortBy = url.searchParams.get('sort_by');
        const sortDir = url.searchParams.get('sort_dir') ?? 'desc';
        let all = [
          {
            id: '1',
            title: '排查 MCP 配置',
            model: 'glm-flash',
            agent_id: 'default',
            pinned: true,
            updated_at: '2026-11-09T04:00:00.000Z',
          },
          {
            id: '2',
            title: '数据库状态',
            model: 'deepseek-v3',
            agent_id: 'default',
            pinned: false,
            updated_at: '2026-11-09T05:00:00.000Z',
          },
        ];
        if (q !== '') {
          all = all.filter(
            (s) => s.title.toLowerCase().includes(q) || s.model.toLowerCase().includes(q),
          );
        }
        if (sortBy === 'title') {
          all = [...all].sort((a, b) =>
            sortDir === 'asc' ? a.title.localeCompare(b.title) : b.title.localeCompare(a.title),
          );
        }
        return HttpResponse.json({ items: all, total: all.length });
      }),
      http.post('/api/v1/chat/sessions/batch-delete', async ({ request }) => {
        const body = (await request.json()) as { ids: string[] };
        return HttpResponse.json({ deleted: body.ids.length });
      }),
    );
  });

  it('列出会话并支持标题搜索', async () => {
    render(
      <MemoryRouter>
        <SessionsPage />
      </MemoryRouter>,
    );
    expect(await screen.findByRole('link', { name: /排查 MCP 配置/ })).toHaveAttribute('href', '/chat/1');
    expect(screen.getByRole('link', { name: /数据库状态/ })).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: '分页' })).toBeInTheDocument();
    // 绝对时间，不再出现「xx 前」
    expect(screen.queryByText(/分钟前|小时前|刚刚|秒前/)).not.toBeInTheDocument();

    await userEvent.type(screen.getByPlaceholderText('搜索标题或模型…'), 'deepseek');
    await waitFor(() => {
      expect(screen.getByRole('link', { name: /数据库状态/ })).toBeInTheDocument();
      expect(screen.queryByRole('link', { name: /排查 MCP 配置/ })).not.toBeInTheDocument();
    });
  });

  it('表头排序会带 sort_by，勾选后可批量删除', async () => {
    const urls: string[] = [];
    let deletedIds: string[] = [];
    server.use(
      agentsHandler,
      http.get('/api/v1/chat/sessions', ({ request }) => {
        urls.push(request.url);
        return HttpResponse.json({
          items: [
            {
              id: '1',
              title: '会话甲',
              model: 'glm-flash',
              agent_id: 'default',
              updated_at: '2026-11-09T04:00:00.000Z',
            },
            {
              id: '2',
              title: '会话乙',
              model: 'glm-flash',
              agent_id: 'default',
              updated_at: '2026-11-09T05:00:00.000Z',
            },
          ],
          total: 2,
        });
      }),
      http.post('/api/v1/chat/sessions/batch-delete', async ({ request }) => {
        const body = (await request.json()) as { ids: string[] };
        deletedIds = body.ids;
        return HttpResponse.json({ deleted: body.ids.length });
      }),
    );

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(
      <MemoryRouter>
        <SessionsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('link', { name: '会话甲' })).toBeInTheDocument();

    // 只有时间列可排序：点击「更新」表头按 updated_at 排序。
    await userEvent.click(screen.getByRole('button', { name: /更新/ }));
    await waitFor(() => {
      expect(urls.some((u) => u.includes('sort_by=updated_at'))).toBe(true);
    });

    const checkboxes = screen.getAllByRole('checkbox', { name: '选择会话' });
    await userEvent.click(checkboxes[0]);
    expect(await screen.findByRole('button', { name: /删除所选 \(1\)/ })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /删除所选 \(1\)/ }));
    await waitFor(() => {
      expect(deletedIds).toEqual(['1']);
    });

    confirmSpy.mockRestore();
  });
});
