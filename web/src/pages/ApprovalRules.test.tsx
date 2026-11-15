// 审批规则页测试 — 规则列表、创建透传与权限门：
//   1. 规则表按判定顺序渲染，动作标签四档可辨
//   2. 新建弹窗提交把完整配置发给 /approval-rules
//   3. 非 admin 看不到新建/编辑/删除入口
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ApprovalRulesPage from './ApprovalRules';
import { server } from '@/test/msw-server';

// client.ts 只需要 token 取值与 401 兜底；测试里全部打桩。
vi.mock('@/store/auth', () => ({
  useAuth: Object.assign(
    <T,>(selector: (s: { role: string }) => T): T => selector({ role: 'admin' }),
    { getState: () => ({ logout: () => {} }) },
  ),
  getToken: () => null,
}));

const perm = vi.hoisted(() => ({ isAdmin: true }));
vi.mock('@/store/me', () => ({
  usePermissions: () => ({ isAdmin: perm.isAdmin, canMutate: !perm.isAdmin, role: perm.isAdmin ? 'admin' : 'user' }),
}));

const rulesURL = '/api/v1/approval-rules';
const agentsURL = '/api/v1/agents';

const rule = {
  id: 'rule-1',
  name: '告警确认升级',
  enabled: true,
  tool_pattern: 'mcp__obs-tools__acknowledge_alert',
  agent_id: null,
  risk: null,
  action: 'escalate',
  priority: 9,
  created_at: '2026-11-15T00:00:00Z',
  updated_at: '2026-11-15T00:00:00Z',
};

function useHandlers(created: unknown[]) {
  server.use(
    http.get(rulesURL, () => HttpResponse.json({ items: [rule], total: 1 })),
    http.get(agentsURL, () =>
      HttpResponse.json({ items: [{ name: 'ops', display_name: '运维助理' }], total: 1 }),
    ),
    http.post(rulesURL, async ({ request }) => {
      created.push(await request.json());
      return HttpResponse.json({ ...rule, id: 'rule-2' });
    }),
  );
}

describe('ApprovalRulesPage', () => {
  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
    perm.isAdmin = true;
  });

  it('规则表按判定顺序渲染并标动作', async () => {
    useHandlers([]);
    render(
      <MemoryRouter>
        <ApprovalRulesPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('告警确认升级')).toBeInTheDocument();
    expect(screen.getByText('mcp__obs-tools__acknowledge_alert')).toBeInTheDocument();
    expect(screen.getByText('升级')).toBeInTheDocument();
    // 判定顺序列：首条 = #1
    expect(screen.getByText('#1')).toBeInTheDocument();
  });

  it('新建规则把完整配置发给后端', async () => {
    const user = userEvent.setup();
    const created: unknown[] = [];
    useHandlers(created);
    render(
      <MemoryRouter>
        <ApprovalRulesPage />
      </MemoryRouter>,
    );

    await screen.findByText('告警确认升级');
    await user.click(screen.getByRole('button', { name: '新建规则' }));
    await user.type(screen.getByRole('textbox', { name: '名称 *' }), '只读免审');
    await user.type(screen.getByRole('textbox', { name: /工具匹配/ }), 'run_python');
    await user.click(screen.getByRole('combobox', { name: '动作' }));
    await user.click(await screen.findByRole('option', { name: '自动放行' }));
    await user.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(created.length).toBe(1));
    expect(created[0]).toMatchObject({
      name: '只读免审',
      tool_pattern: 'run_python',
      action: 'auto_approve',
      agent_id: null,
      risk: null,
    });
  });

  it('非 admin 只读：无新建与编辑入口', async () => {
    perm.isAdmin = false;
    useHandlers([]);
    render(
      <MemoryRouter>
        <ApprovalRulesPage />
      </MemoryRouter>,
    );

    await screen.findByText('告警确认升级');
    expect(screen.queryByRole('button', { name: '新建规则' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /编辑/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /删除/ })).not.toBeInTheDocument();
  });
});
