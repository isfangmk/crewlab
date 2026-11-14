// MCP 服务卡片测试 — 验证整行摘要与内联工具清单仍可用。
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
  embedding_sync_status: 'ok',
  embedding_sync_count: 3,
};

beforeEach(() => {
  localStorage.setItem('ongrid-locale', 'zh-CN');
  server.use(
    http.get('/api/v1/secrets', () => HttpResponse.json({ items: [] })),
    http.get('/api/v1/mcp/servers', () => HttpResponse.json({ items: [mcpServer], total: 1 })),
  );
});

describe('MCP 服务卡片', () => {
  it('默认展示连接摘要，展开后显示工具清单', async () => {
    render(
      <MemoryRouter>
        <McpPage />
      </MemoryRouter>,
    );
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
});
