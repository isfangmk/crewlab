// MCP 服务卡片测试 — 摘要行、内联工具清单、资源/提示词分组与 Registry 预填。
import { render, screen } from '@testing-library/react';
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
  trusted: false,
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
