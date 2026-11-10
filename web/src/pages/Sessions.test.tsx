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

// 模型筛选项来自「会话里出现过的模型」接口；页面挂载即请求，所有用例都要给 handler。
const modelsHandler = http.get('/api/v1/chat/sessions/models', () =>
  HttpResponse.json({ items: ['deepseek-v3', 'glm-flash'] }),
);

describe('SessionsPage', () => {
  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
    server.use(
      agentsHandler,
      modelsHandler,
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
    // 最近活动列用相对时间扫读，精确时间挂在 title 上
    expect(screen.getByRole('columnheader', { name: /最近活动/ })).toBeInTheDocument();

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
      modelsHandler,
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

    // 只有时间列可排序：点击「最近活动」表头按 last_message_at 排序。
    await userEvent.click(screen.getByRole('button', { name: /最近活动/ }));
    await waitFor(() => {
      expect(urls.some((u) => u.includes('sort_by=last_message_at'))).toBe(true);
    });

    const checkboxes = screen.getAllByRole('checkbox', { name: '选择会话' });
    await userEvent.click(checkboxes[0]);
    expect(await screen.findByRole('button', { name: /删除所选 \(1\)/ })).toBeInTheDocument();

    // 删除走统一弹窗：先确认再发请求，不再用原生 confirm
    await userEvent.click(screen.getByRole('button', { name: /删除所选 \(1\)/ }));
    expect(screen.queryByRole('dialog')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '删除' }));
    await waitFor(() => {
      expect(deletedIds).toEqual(['1']);
    });
    // 弹窗确认后关闭
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
    expect(confirmSpy).not.toHaveBeenCalled();

    confirmSpy.mockRestore();
  });

  it('按模型精确筛选：请求带 model 参数，选项来自会话里出现过的模型', async () => {
    const user = userEvent.setup();
    const urls: string[] = [];
    server.use(
      agentsHandler,
      modelsHandler,
      http.get('/api/v1/chat/sessions', ({ request }) => {
        urls.push(request.url);
        const model = new URL(request.url).searchParams.get('model');
        const all = [
          { id: '1', title: '排查 MCP 配置', model: 'glm-flash', agent_id: 'default' },
          { id: '2', title: '数据库状态', model: 'deepseek-v3', agent_id: 'default' },
        ];
        const items = model === null ? all : all.filter((s) => s.model === model);
        return HttpResponse.json({ items, total: items.length });
      }),
    );

    render(
      <MemoryRouter>
        <SessionsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('link', { name: '数据库状态' })).toBeInTheDocument();

    await user.click(screen.getByRole('combobox', { name: '模型' }));
    await user.click(await screen.findByRole('option', { name: 'deepseek-v3' }));

    await waitFor(() => {
      expect(urls.some((u) => u.includes('model=deepseek-v3'))).toBe(true);
    });
    await waitFor(() => {
      expect(screen.getByRole('link', { name: '数据库状态' })).toBeInTheDocument();
      expect(screen.queryByRole('link', { name: '排查 MCP 配置' })).not.toBeInTheDocument();
    });
  });

  it('展示运行信号：轮数、工具/技能调用、tokens、待审批与分享/附件', async () => {
    server.use(
      agentsHandler,
      modelsHandler,
      http.get('/api/v1/chat/sessions', () =>
        HttpResponse.json({
          items: [
            {
              id: '20',
              title: '失败回合',
              model: 'glm-flash',
              agent_id: 'default',
              updated_at: '2026-11-09T04:00:00.000Z',
              last_message_at: '2026-11-09T06:00:00.000Z',
              message_count: 8,
              turn_count: 4,
              tool_call_count: 3,
              skill_call_count: 1,
              total_tokens: 1234,
              last_turn_status: 'error',
              pending_approvals: 2,
              attachment_count: 2,
              shared: true,
            },
          ],
          total: 1,
        }),
      ),
    );

    render(
      <MemoryRouter>
        <SessionsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('link', { name: '失败回合' })).toBeInTheDocument();
    // 轮数按用户提问计；工具 / 技能调用次数分开列
    expect(screen.getByText('4 轮 · 3 次工具 · 1 次技能')).toBeInTheDocument();
    expect(screen.getByText('1.2k tokens')).toBeInTheDocument();
    expect(screen.getByText('待审 2')).toBeInTheDocument();
    // 已分享 + 附件数徽标挂在标题行
    expect(screen.getByLabelText('已分享')).toBeInTheDocument();
    expect(screen.getByTitle('2 个附件')).toBeInTheDocument();
    // 最近活动优先取最后一条消息，而不是会话行更新时间
    expect(screen.getByTitle(/最后一条消息/)).toBeInTheDocument();
    // 失败胶囊可点进观测页，并带上该会话筛选
    const failed = screen.getByRole('link', { name: '失败' });
    expect(failed).toHaveAttribute('href', '/observability?session_id=20');
  });

  it('长会话摘要默认折叠，可展开；短会话不出现折叠按钮', async () => {
    const user = userEvent.setup();
    const longPreview = '先复述问题，再复现，最后给出根因和修复建议。'.repeat(6);
    server.use(
      agentsHandler,
      modelsHandler,
      http.get('/api/v1/chat/sessions', () =>
        HttpResponse.json({
          items: [
            {
              id: '10',
              title: '长会话',
              model: 'glm-flash',
              agent_id: 'default',
              updated_at: '2026-11-09T04:00:00.000Z',
              message_count: 88,
              turn_count: 88,
              last_message_preview: longPreview,
            },
            {
              id: '11',
              title: '短会话',
              model: 'glm-flash',
              agent_id: 'default',
              updated_at: '2026-11-09T05:00:00.000Z',
              message_count: 4,
              turn_count: 2,
              last_message_preview: '简短的一条回复',
            },
          ],
          total: 2,
        }),
      ),
    );

    render(
      <MemoryRouter>
        <SessionsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('link', { name: '长会话' })).toBeInTheDocument();
    expect(screen.getByText('88 轮')).toBeInTheDocument();
    expect(screen.getByText('2 轮')).toBeInTheDocument();

    // 短会话不给折叠按钮：一行摘要本来就够看。
    const expandButtons = screen.getAllByRole('button', { name: /展开摘要/ });
    expect(expandButtons).toHaveLength(1);
    expect(expandButtons[0]).toHaveAttribute('aria-expanded', 'false');

    await user.click(expandButtons[0]);
    const collapse = await screen.findByRole('button', { name: /收起摘要/ });
    expect(collapse).toHaveAttribute('aria-expanded', 'true');

    await user.click(collapse);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /展开摘要/ })).toHaveAttribute(
        'aria-expanded',
        'false',
      );
    });
  });
});
