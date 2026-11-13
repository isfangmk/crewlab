// Agents 页面测试 — 覆盖「点击助理卡片查看定义详情」链路，并验证
// 按 source 区分的编辑路径（user 直接编辑 / 内置预置 fork 成自定义）。
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AgentsPage from './Agents';
import { server } from '@/test/msw-server';

vi.mock('@/store/auth', () => ({
  useAuth: Object.assign(
    <T,>(selector: (s: { role: string }) => T): T => selector({ role: 'admin' }),
    { getState: () => ({ logout: () => {} }) },
  ),
  getToken: () => null,
  
}));

const diskAgent = {
  name: 'specialist-sre',
  description: 'SRE 专家：黄金四信号 / SLO / 错误预算。',
  when_to_use: '当任务围绕系统是否健康时派给我。',
  system_prompt: '# SRE 专家\n你是 SRE 专家，优先看黄金四信号。',
  tools: ['query_promql', 'query_incidents'],
  permission_mode: 'read-only',
  critical_reminder: '',
  output_format: 'free',
  examples: [],
  knowledge_scope: { paths: [], doc_ids: [] },
  source: 'disk',
};

const userAgent = {
  name: 'my_db_helper',
  description: '我的数据库助手。',
  system_prompt: '你是数据库专家。',
  tools: ['analyze_database_status'],
  permission_mode: '',
  critical_reminder: '禁止臆测。',
  output_format: 'checklist',
  examples: [{ input: '慢查询怎么查？', output: '- 先看慢日志' }],
  knowledge_scope: { paths: ['数据库'], doc_ids: [] },
  source: 'user',
};

const listURL = '/api/v1/skills';
const mcpListURL = '/api/v1/mcp/servers';

describe('AgentsPage', () => {
  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
    server.use(
      http.get('/api/v1/agents', () =>
        HttpResponse.json({ items: [diskAgent, userAgent], total: 2 }),
      ),
      // AgentEditor 打开时拉工具列表
      http.get(listURL, () => HttpResponse.json({ items: [], total: 0 })),
      http.get(mcpListURL, () => HttpResponse.json({ items: [], total: 0 })),
      http.get('/api/v1/tools/builtin', () => HttpResponse.json({ items: [], total: 0 })),
      // 知识范围选择器：目录 / 文档 / 命中预览
      http.get('/api/v1/knowledge/paths', () =>
        HttpResponse.json({ items: [{ path: '数据库', count: 2 }], total: 1 }),
      ),
      http.get('/api/v1/knowledge/docs', () =>
        HttpResponse.json({
          items: [
            {
              id: 'doc-slow-query',
              source_type: 'manual',
              title: 'MySQL 慢查询排查',
              path: '数据库',
              tags: [],
              created_at: '2026-11-01T00:00:00Z',
              updated_at: '2026-11-01T00:00:00Z',
            },
          ],
          total: 1,
        }),
      ),
      http.post('/api/v1/knowledge/scope-preview', () =>
        HttpResponse.json({
          doc_count: 2,
          matched_paths: [{ path: '数据库', count: 2 }],
          unknown_paths: [],
          unknown_doc_ids: [],
        }),
      ),
    );
  });

  it('点击预置助理卡片打开只读详情，编辑入口是「复制为自定义助理」', async () => {
    render(
      <MemoryRouter>
        <AgentsPage />
      </MemoryRouter>,
    );
    // 淡色卡片操作不能被实心主按钮背景覆盖，否则浅色主题下紫字落在紫底上。
    const useButton = (await screen.findAllByRole('button', { name: '使用此助理' }))[0];
    expect(useButton).toHaveAttribute('data-variant', 'plain');
    expect(useButton).toHaveClass('bg-indigo-500/10', 'text-indigo-200');
    // 卡片标题用短名；名称是唯一的详情入口（卡片本身不响应点击）
    await userEvent.click(await screen.findByRole('button', { name: 'SRE 专家' }));

    const dialog = await screen.findByRole('dialog');
    // system prompt 原文完整展示
    expect(within(dialog).getByText(/你是 SRE 专家，优先看黄金四信号/)).toBeInTheDocument();
    // 工具清单
    expect(within(dialog).getByText('query_promql')).toBeInTheDocument();
    // disk source 不可直接编辑 → 提供 fork 入口，没有「编辑」按钮
    expect(within(dialog).getByRole('button', { name: /复制为自定义助理/ })).toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: '编辑' })).not.toBeInTheDocument();
  });

  it('点击自定义助理卡片，详情里有「编辑」入口', async () => {
    render(
      <MemoryRouter>
        <AgentsPage />
      </MemoryRouter>,
    );
    // user 助理无短名，name 同时出现在标题按钮和 mono 行；点标题按钮
    await userEvent.click(await screen.findByRole('button', { name: 'my_db_helper' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/你是数据库专家/)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: /编辑/ })).toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: /复制为自定义助理/ })).not.toBeInTheDocument();
  });

  it('新建助理时按 Server 分组挂载 MCP 能力并预览可检索工具数', async () => {
    server.use(
      http.get(listURL, () => HttpResponse.json({ items: [{ key: 'query_promql' }], total: 1 })),
      http.get('/api/v1/tools/builtin', () => HttpResponse.json({ items: [], total: 0 })),
      http.get(mcpListURL, () => HttpResponse.json({
        items: [{
          id: 1,
          name: 'demo-operations',
          transport: 'http',
          endpoint: 'https://mcp.example.com/mcp',
          credential: '',
          header_template_json: '{}',
          trusted: false,
          approval_policy: 'always',
          enabled: true,
          status: 'ok',
          last_error: '',
          tools_cache_json: JSON.stringify([
            { name: 'get_service_health', description: 'Read health.', input_schema: { type: 'object', properties: {} } },
          ]),
        }],
        total: 1,
      })),
    );

    render(
      <MemoryRouter>
        <AgentsPage />
      </MemoryRouter>,
    );

    await userEvent.click(await screen.findByRole('button', { name: /新建助理/ }));
    const dialog = await screen.findByRole('dialog');
    // 白名单为空 = 继承全部，Server 直接显示为已挂载
    expect(await within(dialog).findByText('mcp · demo-operations')).toBeInTheDocument();
    expect(within(dialog).getByText('已挂载')).toBeInTheDocument();
    // 进上下文的只有内置工具；MCP 工具进检索目录，靠 search/load 调用
    expect(within(dialog).getByText('MCP 可检索 1')).toBeInTheDocument();
    expect(within(dialog).getByText('内置 0')).toBeInTheDocument();

    // 工具清单默认折叠，展开后可见 wire 名
    await userEvent.click(within(dialog).getByRole('button', { name: /1 个工具/ }));
    expect(within(dialog).getByText('mcp__demo-operations__get_service_health')).toBeInTheDocument();

    // 禁用名单可逐个屏蔽
    await userEvent.click(within(dialog).getByRole('button', { name: '屏蔽 mcp__demo-operations__get_service_health' }));
    expect(within(dialog).getByText('已屏蔽 1')).toBeInTheDocument();
  });

  it('套用模板会同时填正文、关键提醒、输出格式与示例', async () => {
    render(
      <MemoryRouter>
        <AgentsPage />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole('button', { name: /新建助理/ }));
    const dialog = await screen.findByRole('dialog');

    await userEvent.click(within(dialog).getByRole('button', { name: '知识问答' }));

    expect(within(dialog).getByDisplayValue(/组织知识库助理/)).toBeInTheDocument();
    expect(within(dialog).getByDisplayValue(/不得用常识冒充知识库内容/)).toBeInTheDocument();
    // 模板自带一条示例：输入与输出都要落进表单
    expect(within(dialog).getByDisplayValue('DNS 解析失败通常先查什么？')).toBeInTheDocument();
    expect(within(dialog).getByText('自由文本')).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole('button', { name: 'SRE 排障' }));
    expect(within(dialog).getByText('清单')).toBeInTheDocument();
  });

  it('保存时提交输出格式、示例与知识范围', async () => {
    let saved: Record<string, unknown> | null = null;
    server.use(
      http.post('/api/v1/agents/custom', async ({ request }) => {
        saved = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...userAgent, name: 'scoped_agent' });
      }),
    );

    render(
      <MemoryRouter>
        <AgentsPage />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole('button', { name: /新建助理/ }));
    const dialog = await screen.findByRole('dialog');

    await userEvent.type(
      within(dialog).getByPlaceholderText(/lower_snake/),
      'scoped_agent',
    );
    await userEvent.type(within(dialog).getByPlaceholderText(/一句话说清/), '限定知识范围的助理');
    await userEvent.click(within(dialog).getByRole('button', { name: 'SRE 排障' }));

    // 指定范围 → 勾选目录 → 预览命中数
    await userEvent.click(within(dialog).getByRole('radio', { name: '指定范围' }));
    const pathBox = await within(dialog).findByRole('checkbox', { name: /范围目录 数据库/ });
    await userEvent.click(pathBox);
    expect(await within(dialog).findByText(/命中 2 篇文档/)).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));

    expect(saved).not.toBeNull();
    const payload = saved as unknown as Record<string, unknown>;
    expect(payload.output_format).toBe('checklist');
    expect(payload.critical_reminder).toMatch(/证据/);
    expect((payload.examples as unknown[]).length).toBe(1);
    expect(payload.knowledge_scope).toEqual({ paths: ['数据库'], doc_ids: [] });
  });

  it('范围内命中 0 篇时禁止保存', async () => {
    server.use(
      http.post('/api/v1/knowledge/scope-preview', () =>
        HttpResponse.json({
          doc_count: 0,
          matched_paths: [],
          unknown_paths: ['数据库'],
          unknown_doc_ids: [],
        }),
      ),
    );

    render(
      <MemoryRouter>
        <AgentsPage />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole('button', { name: /新建助理/ }));
    const dialog = await screen.findByRole('dialog');

    // 必填项先填齐，确保禁用只来自范围校验
    await userEvent.type(within(dialog).getByPlaceholderText(/lower_snake/), 'empty_scope_agent');
    await userEvent.type(within(dialog).getByPlaceholderText(/一句话说清/), '范围为空');
    await userEvent.click(within(dialog).getByRole('button', { name: 'SRE 排障' }));

    await userEvent.click(within(dialog).getByRole('radio', { name: '指定范围' }));
    await userEvent.click(await within(dialog).findByRole('checkbox', { name: /范围目录 数据库/ }));

    expect(await within(dialog).findByText(/保存会被拒绝/)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: '保存' })).toBeDisabled();
  });

  it('详情弹窗展示知识范围与输出格式', async () => {
    render(
      <MemoryRouter>
        <AgentsPage />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole('button', { name: 'my_db_helper' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('知识范围')).toBeInTheDocument();
    expect(within(dialog).getByText('数据库')).toBeInTheDocument();
    expect(within(dialog).getByText('清单')).toBeInTheDocument();
    expect(within(dialog).getByText('输出格式与示例')).toBeInTheDocument();
  });
});
