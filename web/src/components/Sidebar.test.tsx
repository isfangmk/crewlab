import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { Sidebar } from './Sidebar';
import { useAuth } from '@/store/auth';
import { useUi } from '@/store/ui';
import { server } from '@/test/msw-server';

vi.mock('@/store/me', () => ({
  useMe: () => ({ me: null, loading: false, error: null, refresh: vi.fn() }),
  usePermissions: () => ({ isAdmin: true, canMutate: true, role: 'admin' }),
}));

describe('Sidebar workbench nav', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('ongrid-locale', 'zh-CN');
    useAuth.setState({ token: null, email: 'admin@local', role: 'admin' });
    useUi.setState({ sidebarCollapsed: false, sessionsCollapsed: false, sessionsPreviewOpen: false, sessionsTriggerHovered: false });
    server.use(
      http.get('/api/v1/chat/sessions', () => HttpResponse.json({ items: [], total: 0 })),
    );
  });

  it('shows workbench links and settings', async () => {
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    );
    expect(await screen.findByRole('button', { name: '对话' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '助理' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '能力' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '知识库' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '工作流' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '治理' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '会话' })).toHaveAttribute('href', '/sessions');
    expect(screen.getByRole('link', { name: '助理' })).toHaveAttribute('href', '/agents');
    expect(screen.getByRole('link', { name: '工具' })).toHaveAttribute('href', '/tools');
    expect(screen.getByRole('link', { name: '技能' })).toHaveAttribute('href', '/skills');
    expect(screen.getByRole('link', { name: 'MCP' })).toHaveAttribute('href', '/mcp');
    expect(screen.getByRole('link', { name: '知识库' })).toHaveAttribute('href', '/knowledge');
    expect(screen.getByRole('link', { name: '审批' })).toHaveAttribute('href', '/approvals');
    expect(screen.getByLabelText('导出与分享（规划中）')).toBeInTheDocument();
    expect(screen.getByLabelText('试玩台（规划中）')).toBeInTheDocument();
    expect(screen.getByLabelText('记忆（规划中）')).toBeInTheDocument();
    expect(screen.getByLabelText('插件包（规划中）')).toBeInTheDocument();
    expect(screen.getByLabelText('沙箱（规划中）')).toBeInTheDocument();
    expect(screen.getByLabelText('外部源（规划中）')).toBeInTheDocument();
    expect(screen.getByLabelText('检索试跑（规划中）')).toBeInTheDocument();
    expect(screen.getByLabelText('定时触发（规划中）')).toBeInTheDocument();
    expect(screen.getByLabelText('对外发布（规划中）')).toBeInTheDocument();
    expect(screen.getByLabelText('IM 渠道（规划中）')).toBeInTheDocument();
    expect(screen.getByLabelText('审计日志（规划中）')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '可观测' })).toHaveAttribute('href', '/observability');
    expect(screen.getByLabelText('评测（规划中）')).toBeInTheDocument();
    expect(screen.getByLabelText('用户与权限（规划中）')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '定时触发' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '仪表盘' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '设备' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: '设置' })).toHaveAttribute('href', '/settings/llm');
  });

  it('uses the icon rail on narrow screens without overwriting desktop preferences', () => {
    let matches = true;
    let notify: (() => void) | undefined;
    const media = {
      get matches() { return matches; },
      media: '(max-width: 767px)', onchange: null,
      addEventListener: vi.fn((_event: string, listener: () => void) => { notify = listener; }),
      removeEventListener: vi.fn(), addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
    };
    vi.stubGlobal('matchMedia', vi.fn(() => media));
    try {
      const view = render(<MemoryRouter><Sidebar /></MemoryRouter>);
      fireEvent.click(screen.getAllByRole('button', { name: '展开侧边栏' })[0]);
      expect(screen.getByRole('button', { name: '折叠侧边栏' })).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: '折叠侧边栏' }));
      expect(useUi.getState().sidebarCollapsed).toBe(false);
      act(() => { matches = false; notify?.(); });
      expect(screen.getByRole('button', { name: '折叠侧边栏' })).toBeInTheDocument();
      act(() => useUi.getState().setSidebarCollapsed(true));
      expect(screen.getAllByRole('button', { name: '展开侧边栏' })).toHaveLength(2);
      view.unmount();
      expect(media.removeEventListener).toHaveBeenCalledWith('change', expect.any(Function));
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
