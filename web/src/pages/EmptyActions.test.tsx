import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { server } from '@/test/msw-server';
import McpPage from './Mcp';

const auth = vi.hoisted(() => ({ role: 'admin' }));
vi.mock('@/store/auth', () => ({
  useAuth: Object.assign(<T,>(selector: (s: typeof auth) => T) => selector(auth), { getState: () => ({ logout: () => {} }) }),
  getToken: () => null, 
}));

beforeEach(() => {
  auth.role = 'admin';
  localStorage.setItem('ongrid-locale', 'zh-CN');
  server.use(http.get('/api/v1/secrets', () => HttpResponse.json({ items: [] })));
});

describe('MCP empty actions', () => {
  it('opens the existing editor from the empty card and cancels without saving', async () => {
    const save = vi.fn();
    server.use(
      http.get('/api/v1/mcp/servers', () => HttpResponse.json({ items: [], total: 0 })),
      http.post('/api/v1/mcp/servers', () => { save(); return HttpResponse.json({}); }),
    );
    render(<MemoryRouter><McpPage /></MemoryRouter>);
    const title = await screen.findByText('还没有注册任何 MCP 服务');
    await userEvent.click(within(title.parentElement!).getByRole('button', { name: '接入 MCP' }));
    const dialog = await screen.findByRole('dialog', { name: '新建 MCP 服务' });
    await userEvent.click(within(dialog).getByRole('button', { name: '取消' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(save).not.toHaveBeenCalled();
  });

  it('hides creation for viewers', async () => {
    auth.role = 'viewer';
    server.use(http.get('/api/v1/mcp/servers', () => HttpResponse.json({ items: [], total: 0 })));
    render(<MemoryRouter><McpPage /></MemoryRouter>);
    await screen.findByText('还没有注册任何 MCP 服务');
    expect(screen.queryByRole('button', { name: '接入 MCP' })).not.toBeInTheDocument();
  });
});
