// 发布渠道页：列表展示掩码 Key / 用量 / 状态，创建后一次性展示 Key。
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import PublishPage from './Publish';
import { server } from '@/test/msw-server';

vi.mock('@/store/auth', () => ({
  useAuth: Object.assign(
    <T,>(selector: (s: { role: string }) => T): T => selector({ role: 'admin' }),
    { getState: () => ({ logout: () => {} }) },
  ),
  getToken: () => null,
}));

const channel = {
  id: 'ch-1',
  name: '运维对外问答',
  agent_id: 'default',
  provider: 'deepseek',
  model: 'deepseek-flash',
  key_masked: 'pk-demo123456…',
  enabled: true,
  expires_at: null,
  session_id: 'sess-1',
  created_at: '2026-11-14T00:00:00Z',
  usage: {
    turns: 3,
    prompt_tokens: 120,
    completion_tokens: 80,
    total_tokens: 200,
    last_used_at: '2026-11-14T01:00:00Z',
  },
};

describe('PublishPage', () => {
  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
    server.use(
      http.get('/api/v1/publish/channels', () =>
        HttpResponse.json({ items: [channel], total: 1 }),
      ),
      http.get('/api/v1/agents', () =>
        HttpResponse.json({
          items: [
            {
              name: 'default',
              description: '默认助理',
              when_to_use: '',
              tools: [],
              disallowed_tools: [],
              permission_mode: '',
              model: '',
              max_turns: 0,
              system_prompt: '',
              critical_reminder: '',
              output_format: 'free',
              examples: [],
              knowledge_scope: { paths: [], doc_ids: [] },
              skills: null,
              sub_agents: [],
              source: 'builtin',
            },
          ],
          total: 1,
        }),
      ),
      http.get('/api/v1/aiops/models', () =>
        HttpResponse.json({
          providers: [
            { id: 'deepseek', label: 'DeepSeek', models: ['deepseek-flash'], model: 'deepseek-flash' },
          ],
          default: { provider: 'deepseek', model: 'deepseek-flash' },
        }),
      ),
    );
  });

  it('列表展示掩码 Key、用量与启用状态', async () => {
    render(
      <MemoryRouter>
        <PublishPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText('运维对外问答')).toBeInTheDocument();
    expect(screen.getByText('pk-demo123456…')).toBeInTheDocument();
    expect(screen.getByText('3 回合')).toBeInTheDocument();
    expect(screen.getByText('200 tokens')).toBeInTheDocument();
    expect(screen.getByText('启用中')).toBeInTheDocument();
  });

  it('按最近调用时间排序渠道', async () => {
    const older = {
      ...channel,
      id: 'ch-2',
      name: '旧渠道',
      usage: {
        ...channel.usage,
        last_used_at: '2026-11-10T01:00:00Z',
      },
    };
    server.use(
      http.get('/api/v1/publish/channels', () =>
        HttpResponse.json({ items: [older, channel], total: 2 }),
      ),
    );
    render(
      <MemoryRouter>
        <PublishPage />
      </MemoryRouter>,
    );

    await screen.findByText('运维对外问答');
    const header = screen.getByRole('columnheader', { name: '最近调用' });
    const rows = () => screen.getAllByRole('row').slice(1);
    // 默认最近调用在前
    expect(within(rows()[0]).getByText('运维对外问答')).toBeInTheDocument();

    await userEvent.click(within(header).getByRole('button', { name: '最近调用' }));
    expect(within(rows()[0]).getByText('旧渠道')).toBeInTheDocument();
  });

  it('创建渠道后展示一次性 Key，提交内容包含名称与助理', async () => {
    let posted: unknown = null;
    server.use(
      http.post('/api/v1/publish/channels', async ({ request }) => {
        posted = await request.json();
        return HttpResponse.json({ ...channel, id: 'ch-2', key: 'pk-abcdefghijklmnopqrstuvwxyz' });
      }),
    );
    render(
      <MemoryRouter>
        <PublishPage />
      </MemoryRouter>,
    );
    await screen.findByText('运维对外问答');
    await userEvent.click(screen.getByRole('button', { name: /新建渠道/ }));
    const editor = await screen.findByRole('dialog');
    await userEvent.type(within(editor).getByPlaceholderText('例如 运维问答对外版'), '对外测试');
    await userEvent.click(within(editor).getByRole('button', { name: '创建并签发 Key' }));

    const reveal = await screen.findByRole('dialog');
    expect(within(reveal).getByText('pk-abcdefghijklmnopqrstuvwxyz')).toBeInTheDocument();
    expect(within(reveal).getByText(/只显示这一次/)).toBeInTheDocument();
    expect(posted).toMatchObject({ name: '对外测试', agent_id: 'default' });
  });

  it('点名称打开编辑弹窗，回填详情并提交 PATCH', async () => {
    let patched: unknown = null;
    server.use(
      http.get('/api/v1/publish/channels/ch-1', () => HttpResponse.json(channel)),
      http.patch('/api/v1/publish/channels/ch-1', async ({ request }) => {
        patched = await request.json();
        return HttpResponse.json({ ...channel, name: '运维对外问答-改' });
      }),
    );
    render(
      <MemoryRouter>
        <PublishPage />
      </MemoryRouter>,
    );
    await screen.findByText('运维对外问答');
    await userEvent.click(screen.getByTitle('查看并编辑 运维对外问答'));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('渠道详情')).toBeInTheDocument();
    expect(within(dialog).getByText('3 回合')).toBeInTheDocument();
    expect(within(dialog).getByText('200 tokens')).toBeInTheDocument();
    expect(within(dialog).getByDisplayValue('运维对外问答')).toBeInTheDocument();

    const nameInput = within(dialog).getByLabelText('渠道名称');
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, '运维对外问答-改');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存修改' }));

    await waitFor(() =>
      expect(patched).toMatchObject({
        name: '运维对外问答-改',
        agent_id: 'default',
        provider: 'deepseek',
        model: 'deepseek-flash',
      }),
    );
    // 没动有效期就不提交，避免每次保存都重置过期时间
    expect(patched).not.toHaveProperty('expires_in_hours');
    expect(await screen.findByText('渠道已更新')).toBeInTheDocument();
  });
});
