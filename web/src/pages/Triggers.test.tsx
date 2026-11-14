// Triggers 页面测试：计划/最近运行字段、只读角色操作可见性、
// 以及按已发布快照声明变量生成 values_json 的创建链路。
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import TriggersPage from './Triggers';
import { server } from '@/test/msw-server';
import { selectOption } from '@/test/select-option';

const perms = vi.hoisted(() => ({ canMutate: true, isAdmin: true }));

vi.mock('@/store/me', () => ({
  usePermissions: () => ({
    role: perms.isAdmin ? 'admin' : 'viewer',
    isAdmin: perms.isAdmin,
    isDeveloper: false,
    isViewer: !perms.isAdmin,
    canMutate: perms.canMutate,
  }),
}));

const RULES = [
  {
    id: 7,
    name: '每日巡检',
    kind: 'cron',
    cron_expr: '0 9 * * *',
    target_kind: 'workflow',
    workflow_id: 1,
    agent_id: null,
    prompt: '',
    values_json: '{"topic":"巡检"}',
    enabled: true,
    max_retries: 0,
    has_webhook: false,
    next_fire_at: '2026-11-15T01:00:00.000Z',
    last_fired_at: '2026-11-14T01:00:00.000Z',
    created_at: '2026-11-10T00:00:00.000Z',
  },
  {
    id: 8,
    name: '外部回调',
    kind: 'webhook',
    cron_expr: '',
    target_kind: 'agent',
    workflow_id: null,
    agent_id: 'default',
    prompt: '做一次巡检',
    values_json: '{}',
    enabled: false,
    max_retries: 2,
    has_webhook: true,
    next_fire_at: null,
    last_fired_at: null,
    created_at: '2026-11-11T00:00:00.000Z',
  },
];

const WORKFLOW_SUMMARY = {
  id: 1,
  name: '演示流程',
  description: '',
  status: 'published',
  version: 2,
  validation_error: '',
  has_published: true,
  node_count: 2,
  edge_count: 1,
  warnings: [],
  last_run: null,
  created_at: '2026-11-10T00:00:00.000Z',
  updated_at: '2026-11-10T00:00:00.000Z',
};

const PUBLISHED_DETAIL = {
  ...WORKFLOW_SUMMARY,
  definition_source: 'published',
  definition: {
    nodes: [
      {
        id: 'start',
        kind: 'start',
        label: '开始',
        x: 120,
        y: 160,
        output_key: '',
        variables: [
          {
            name: 'topic',
            type: 'string',
            description: '巡检主题',
            required: true,
            default: null,
          },
          {
            name: 'limit',
            type: 'number',
            description: '',
            required: false,
            default: null,
          },
          {
            name: 'payload',
            type: 'json',
            description: '',
            required: false,
            default: null,
          },
        ],
      },
      { id: 'end', kind: 'end', label: '结束', x: 400, y: 160, output_key: '', variables: [] },
    ],
    edges: [{ id: 'e1', source: 'start', target: 'end', route: '' }],
  },
};

describe('TriggersPage', () => {
  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
    perms.canMutate = true;
    perms.isAdmin = true;
    server.use(
      http.get('/api/v1/triggers', () =>
        HttpResponse.json({ items: RULES, total: RULES.length }),
      ),
      http.get('/api/v1/workflows', () =>
        HttpResponse.json({ items: [WORKFLOW_SUMMARY], total: 1 }),
      ),
      http.get('/api/v1/workflows/1', () => HttpResponse.json(PUBLISHED_DETAIL)),
      http.get('/api/v1/agents', () => HttpResponse.json({ items: [], total: 0 })),
    );
  });

  it('展示计划与最近运行，写操作对有权限用户可见', async () => {
    render(
      <MemoryRouter>
        <TriggersPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('columnheader', { name: '计划' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '最近运行' })).toBeInTheDocument();
    expect(screen.getByText('0 9 * * *')).toBeInTheDocument();
    expect(screen.getByText(/下次 /)).toBeInTheDocument();
    expect(screen.getByText('按调用')).toBeInTheDocument();
    // 两条规则各有一个触发按钮；停用的 webhook 规则没有下次触发时间
    expect(screen.getAllByRole('button', { name: '触发' })).toHaveLength(2);
    expect(screen.getAllByRole('button', { name: '启停' })).toHaveLength(2);
    expect(screen.getAllByTitle('停用')).toHaveLength(1);
    expect(screen.getAllByTitle('启用')).toHaveLength(1);
  });

  it('按最近运行时间排序，未运行过的排最后', async () => {
    const olderRule = {
      ...RULES[0],
      id: 9,
      name: '旧巡检',
      last_fired_at: '2026-11-12T01:00:00.000Z',
    };
    server.use(
      http.get('/api/v1/triggers', () =>
        HttpResponse.json({
          items: [RULES[1], olderRule, RULES[0]],
          total: 3,
        }),
      ),
    );
    render(
      <MemoryRouter>
        <TriggersPage />
      </MemoryRouter>,
    );

    await screen.findByText('每日巡检');
    const header = screen.getByRole('columnheader', { name: '最近运行' });
    const rows = () => screen.getAllByRole('row').slice(1);
    expect(within(rows()[0]).getByText('每日巡检')).toBeInTheDocument();

    await userEvent.click(within(header).getByRole('button', { name: '最近运行' }));
    expect(within(rows()[0]).getByText('旧巡检')).toBeInTheDocument();
    expect(within(rows()[2]).getByText('外部回调')).toBeInTheDocument();

    // 切回降序，未运行过的规则仍然在最后
    await userEvent.click(within(header).getByRole('button', { name: '最近运行' }));
    expect(within(rows()[0]).getByText('每日巡检')).toBeInTheDocument();
    expect(within(rows()[2]).getByText('外部回调')).toBeInTheDocument();
  });

  it('只读用户看不到触发/启停/删除入口', async () => {
    perms.canMutate = false;
    perms.isAdmin = false;
    render(
      <MemoryRouter>
        <TriggersPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('每日巡检')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '触发' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '启停' })).not.toBeInTheDocument();
    expect(screen.getAllByText('只读')).toHaveLength(2);
  });

  it('创建时按已发布快照变量生成 values_json', async () => {
    const bodies: Record<string, unknown>[] = [];
    server.use(
      http.post('/api/v1/triggers', async ({ request }) => {
        bodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ id: 9, name: '新触发器', kind: 'cron', webhook_url: '' });
      }),
    );

    render(
      <MemoryRouter>
        <TriggersPage />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole('button', { name: '新建触发器' }));

    const dialog = await screen.findByRole('dialog');
    await userEvent.type(within(dialog).getByLabelText('名称'), '新触发器');
    await selectOption(within(dialog).getByRole('combobox', { name: '工作流' }), '演示流程');

    // 变量按外部触发真正运行的已发布快照加载
    expect(await within(dialog).findByText('巡检主题')).toBeInTheDocument();
    const save = within(dialog).getByRole('button', { name: '保存' });
    expect(save).toBeDisabled();

    await userEvent.type(within(dialog).getByLabelText('topic'), '自动巡检');
    await userEvent.type(within(dialog).getByLabelText('limit'), '3');
    fireEvent.change(within(dialog).getByLabelText('payload'), {
      target: { value: '{"a":1}' },
    });
    await waitFor(() => expect(save).toBeEnabled());
    await act(async () => {
      await userEvent.click(save);
    });

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0].workflow_id).toBe(1);
    expect(bodies[0].values_json).toBe(
      JSON.stringify({ topic: '自动巡检', limit: 3, payload: { a: 1 } }),
    );
  });
});
