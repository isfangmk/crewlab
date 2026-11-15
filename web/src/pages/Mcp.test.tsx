// MCP 服务卡片测试 — 摘要行、内联工具清单、资源/提示词分组与 Registry 预填。
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import McpPage from './Mcp';
import { server } from '@/test/msw-server';

vi.mock('@/store/auth', () => ({
  useAuth: Object.assign(
    <T,>(selector: (s: { role: string }) => T): T => selector({ role: 'admin' }),
    { getState: () => ({ logout: () => {} }) },
  ),
  getToken: () => null,
}));

const mcpServer = {
  id: 1,
  name: 'demo-tools',
  transport: 'sse',
  endpoint: 'http://127.0.0.1:18099/sse',
  credential: '',
  header_template_json: '{}',
  approval_policy: 'always',
  enabled: true,
  status: 'ok',
  last_error: '',
  tools_cache_json: JSON.stringify([
    { name: 'add', description: '两数相加。', input_schema: { type: 'object', properties: {} } },
    { name: 'echo', description: '原样返回输入。', input_schema: { type: 'object', properties: {} } },
    { name: 'current_time', description: '返回服务器时间。', input_schema: { type: 'object', properties: {} } },
  ]),
  resources_count: 1,
  prompts_count: 1,
  last_probed_at: '2026-11-14T08:00:00Z',
  oauth_status: 'authorized',
  embedding_sync_status: 'ok',
  embedding_sync_count: 3,
};

const mcpResources = [
  {
    uri: 'demo://greeting',
    name: 'greeting',
    description: '问候语资源',
    mime_type: 'text/plain',
  },
];

const mcpPrompts = [
  {
    name: 'summarize',
    description: '生成主题摘要',
    arguments: [{ name: 'topic', description: '主题', required: true }],
  },
];

beforeEach(() => {
  localStorage.setItem('ongrid-locale', 'zh-CN');
  server.use(
    http.get('/api/v1/secrets', () => HttpResponse.json({ items: [] })),
    http.get('/api/v1/mcp/servers', () => HttpResponse.json({ items: [mcpServer], total: 1 })),
    http.get('/api/v1/mcp/servers/:id/resources', () =>
      HttpResponse.json({ items: mcpResources, total: mcpResources.length }),
    ),
    http.get('/api/v1/mcp/servers/:id/prompts', () =>
      HttpResponse.json({ items: mcpPrompts, total: mcpPrompts.length }),
    ),
  );
});

function renderPage() {
  render(
    <MemoryRouter>
      <McpPage />
    </MemoryRouter>,
  );
}

describe('MCP 服务卡片', () => {
  it('默认展示连接摘要，展开后显示工具清单', async () => {
    renderPage();
    expect(await screen.findByText('demo-tools')).toBeInTheDocument();
    expect(screen.getByText('共 1 个服务')).toBeInTheDocument();
    expect(screen.getByText('http://127.0.0.1:18099/sse')).toBeInTheDocument();
    expect(screen.getByText('3 个工具')).toBeInTheDocument();
    expect(screen.getByText('每次审批')).toBeInTheDocument();
    expect(screen.getByText('索引 3')).toBeInTheDocument();
    expect(screen.queryByText('mcp__demo-tools__echo')).not.toBeInTheDocument();

    const toggle = screen.getByRole('button', { name: /demo-tools/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(await screen.findByText('工具清单')).toBeInTheDocument();
    expect(screen.getByText('mcp__demo-tools__echo')).toBeInTheDocument();
  });

  it('摘要行显示资源与提示词计数', async () => {
    renderPage();
    expect(await screen.findByText('资源 1')).toBeInTheDocument();
    expect(screen.getByText('提示词 1')).toBeInTheDocument();
  });

  it('展开区展示资源与提示词分组，可读取资源、取用提示词', async () => {
    server.use(
      http.get('/api/v1/mcp/servers/:id/resources/read', () =>
        HttpResponse.json({
          uri: 'demo://greeting',
          contents: [{ uri: 'demo://greeting', mime_type: 'text/plain', text: '你好，这是测试资源内容' }],
        }),
      ),
      http.post('/api/v1/mcp/servers/:id/prompts/get', () =>
        HttpResponse.json({
          name: 'summarize',
          description: '生成主题摘要',
          messages: [{ role: 'user', text: '请总结主题：测试主题' }],
        }),
      ),
    );
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: /demo-tools/ }));

    // 资源分组：uri / 名称 / 类型 + 读取入口
    expect(await screen.findByText('demo://greeting')).toBeInTheDocument();
    expect(screen.getByText('greeting')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '读取' }));
    expect(await screen.findByText('你好，这是测试资源内容')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '关闭' }));

    // 提示词分组：参数 chip + 使用入口 → 填参取用
    expect(screen.getByText('summarize')).toBeInTheDocument();
    expect(screen.getByText('topic')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '使用' }));
    await userEvent.type(screen.getByRole('textbox', { name: /topic/ }), '测试主题');
    await userEvent.click(screen.getByRole('button', { name: '取用提示词' }));
    expect(await screen.findByText('请总结主题：测试主题')).toBeInTheDocument();
  });

  it('从 Registry 搜索并预填登记表单', async () => {
    server.use(
      http.get('/api/v1/mcp/registry/servers', () =>
        HttpResponse.json({
          items: [
            {
              name: 'io.example-fs',
              description: '文件系统服务',
              transport: 'sse',
              endpoint: 'https://example.com/mcp/sse',
              hint: '',
            },
          ],
          next_cursor: '',
          total: 1,
        }),
      ),
    );
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: '从 Registry 添加' }));
    await userEvent.type(screen.getByRole('searchbox', { name: '搜索注册表' }), 'filesystem');
    await userEvent.click(screen.getByRole('button', { name: '搜索' }));
    expect(await screen.findByText('io.example-fs')).toBeInTheDocument();
    expect(screen.getByText('文件系统服务')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '添加' }));
    // 预填登记表单：名称 / 端点
    expect(await screen.findByDisplayValue('io.example-fs')).toBeInTheDocument();
    expect(screen.getByDisplayValue('https://example.com/mcp/sse')).toBeInTheDocument();
  });
});

describe('Registry 分页深翻', () => {
  it('加载更多追加下一页并跨页去重', async () => {
    server.use(
      http.get('/api/v1/mcp/registry/servers', ({ request }) => {
        const cursor = new URL(request.url).searchParams.get('cursor') ?? '';
        if (cursor === '') {
          return HttpResponse.json({
            items: [
              { name: 'item-a', description: '', transport: 'http', endpoint: 'https://a.example/mcp', hint: '' },
              { name: 'item-b', description: '', transport: 'http', endpoint: 'https://b.example/mcp', hint: '' },
            ],
            next_cursor: 'c1',
            total: 2,
          });
        }
        // 第二页故意与第一页重复 item-b
        return HttpResponse.json({
          items: [
            { name: 'item-b', description: '', transport: 'http', endpoint: 'https://b.example/mcp', hint: '' },
            { name: 'item-c', description: '', transport: 'sse', endpoint: 'https://c.example/sse', hint: '' },
          ],
          next_cursor: '',
          total: 2,
        });
      }),
    );
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: '从 Registry 添加' }));
    await userEvent.click(screen.getByRole('button', { name: '搜索' }));
    expect(await screen.findByText('已加载 2 个')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '加载更多' }));
    expect(await screen.findByText('已加载 3 个')).toBeInTheDocument();
    expect(screen.getByText('item-a')).toBeInTheDocument();
    expect(screen.getByText('item-c')).toBeInTheDocument();
    // 重复的 item-b 只保留一行
    expect(screen.getAllByText('item-b')).toHaveLength(1);
    // 已到最后一页，不再显示加载更多
    expect(screen.queryByRole('button', { name: '加载更多' })).not.toBeInTheDocument();
  });

  it('慢响应不覆盖新搜索结果', async () => {
    server.use(
      http.get('/api/v1/mcp/registry/servers', async ({ request }) => {
        const search = new URL(request.url).searchParams.get('search') ?? '';
        if (search === 'slow') {
          await new Promise((resolve) => setTimeout(resolve, 600));
          return HttpResponse.json({
            items: [{ name: 'slow-item', description: '', transport: 'http', endpoint: 'https://slow.example/mcp', hint: '' }],
            next_cursor: '',
            total: 1,
          });
        }
        return HttpResponse.json({
          items: [{ name: 'fast-item', description: '', transport: 'http', endpoint: 'https://fast.example/mcp', hint: '' }],
          next_cursor: '',
          total: 1,
        });
      }),
    );
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: '从 Registry 添加' }));
    const searchBox = screen.getByRole('searchbox', { name: '搜索注册表' });
    // 先发慢查询，不等它返回就立刻改词重搜
    await userEvent.type(searchBox, 'slow');
    await userEvent.click(screen.getByRole('button', { name: '搜索' }));
    await userEvent.clear(searchBox);
    await userEvent.type(searchBox, 'fast');
    await userEvent.click(screen.getByRole('button', { name: '搜索' }));

    expect(await screen.findByText('fast-item')).toBeInTheDocument();
    // 等慢响应抵达后仍不得覆盖新结果
    await new Promise((resolve) => setTimeout(resolve, 800));
    expect(screen.queryByText('slow-item')).not.toBeInTheDocument();
    expect(screen.getByText('fast-item')).toBeInTheDocument();
  });

  it('加载更多失败后从断点续拉，不丢已加载项', async () => {
    let moreCalls = 0;
    server.use(
      http.get('/api/v1/mcp/registry/servers', ({ request }) => {
        const cursor = new URL(request.url).searchParams.get('cursor') ?? '';
        if (cursor === '') {
          return HttpResponse.json({
            items: [{ name: 'item-a', description: '', transport: 'http', endpoint: 'https://a.example/mcp', hint: '' }],
            next_cursor: 'c1',
            total: 1,
          });
        }
        moreCalls += 1;
        if (moreCalls === 1) {
          return HttpResponse.json({ detail: 'Registry 不可达' }, { status: 502 });
        }
        return HttpResponse.json({
          items: [{ name: 'item-b', description: '', transport: 'http', endpoint: 'https://b.example/mcp', hint: '' }],
          next_cursor: '',
          total: 1,
        });
      }),
    );
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: '从 Registry 添加' }));
    await userEvent.click(screen.getByRole('button', { name: '搜索' }));
    expect(await screen.findByText('已加载 1 个')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '加载更多' }));
    expect(await screen.findByText(/加载更多失败/)).toBeInTheDocument();
    // 已加载项保留
    expect(screen.getByText('item-a')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByText('已加载 2 个')).toBeInTheDocument();
    expect(screen.getByText('item-b')).toBeInTheDocument();
    expect(screen.queryByText(/加载更多失败/)).not.toBeInTheDocument();
  });
});

describe('OAuth 授权', () => {
  it('摘要行显示 OAuth 授权状态', async () => {
    renderPage();
    expect(await screen.findByText('OAuth 已授权')).toBeInTheDocument();
  });

  it('弹窗手动粘贴 Token 并保存', async () => {
    const posted: unknown[] = [];
    server.use(
      http.get('/api/v1/mcp/servers/:id/oauth/status', () =>
        HttpResponse.json({ authorized: false, auth_source: null, expires_at: null, expired: false }),
      ),
      http.post('/api/v1/mcp/servers/:id/oauth/token', async ({ request }) => {
        posted.push(await request.json());
        return HttpResponse.json({ authorized: true, auth_source: 'manual', expires_at: null, expired: false });
      }),
    );
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'OAuth 授权' }));
    const dialog = await screen.findByRole('dialog', { name: 'OAuth 授权 · demo-tools' });
    await userEvent.click(within(dialog).getByRole('button', { name: '手动粘贴 Token' }));
    await userEvent.type(within(dialog).getByRole('textbox', { name: /Access Token/ }), 'tok-1');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存 Token' }));
    expect(await within(dialog).findByText('已授权（手动）')).toBeInTheDocument();
    expect(posted).toMatchObject([{ access_token: 'tok-1' }]);
  });

  it('解绑需二次确认', async () => {
    let revoked = 0;
    server.use(
      http.get('/api/v1/mcp/servers/:id/oauth/status', () =>
        HttpResponse.json({ authorized: true, auth_source: 'oauth', expires_at: '2026-12-01T00:00:00Z', expired: false }),
      ),
      http.delete('/api/v1/mcp/servers/:id/oauth', () => {
        revoked += 1;
        return HttpResponse.json({ ok: true });
      }),
    );
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'OAuth 授权' }));
    const dialog = await screen.findByRole('dialog', { name: 'OAuth 授权 · demo-tools' });
    await userEvent.click(within(dialog).getByRole('button', { name: '解绑' }));
    const confirm = await screen.findByRole('dialog', { name: '解绑 Token？' });
    await userEvent.click(within(confirm).getByRole('button', { name: '解绑' }));
    expect(await within(dialog).findByText('未授权')).toBeInTheDocument();
    expect(revoked).toBe(1);
  });
});
