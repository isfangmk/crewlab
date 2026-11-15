// Agents 页面测试 — 覆盖「点击助理卡片查看定义详情」链路，并验证
// 按 source 区分的编辑路径（user 直接编辑 / 内置预置 fork 成自定义）。
import { render, screen, waitFor, within } from '@testing-library/react';
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
  display_name: 'SRE 专家',
  description: 'SRE 专家：黄金四信号 / SLO / 错误预算。',
  when_to_use: '当任务围绕系统是否健康时派给我。',
  system_prompt: '# SRE 专家\n你是 SRE 专家，优先看黄金四信号。',
  tools: ['query_promql', 'query_incidents'],
  permission_mode: 'read-only',
  critical_reminder: '',
  output_format: 'free',
  examples: [],
  knowledge_scope: { paths: [], doc_ids: [] },
  sub_agents: [],
  source: 'disk',
};

const userAgent = {
  name: 'my_db_helper',
  display_name: '数据库专家',
  description: '我的数据库助手。',
  system_prompt: '你是数据库专家。',
  tools: ['analyze_database_status'],
  permission_mode: '',
  critical_reminder: '禁止臆测。',
  output_format: 'checklist',
  examples: [{ input: '慢查询怎么查？', output: '- 先看慢日志' }],
  knowledge_scope: { paths: ['数据库'], doc_ids: [] },
  sub_agents: [],
  source: 'user',
};

const listURL = '/api/v1/skills';
const mcpListURL = '/api/v1/mcp/servers';

// 创建助理走 5 步向导：先填基础信息，再过必填的人设，才能进入后续步骤。
async function fillCreateBasics(dialog: HTMLElement, name = 'test_agent') {
  await userEvent.type(within(dialog).getByPlaceholderText(/oncall-duty/), name);
  await userEvent.type(within(dialog).getByPlaceholderText(/例如：运维值班助理/), '测试助理');
  await userEvent.type(within(dialog).getByPlaceholderText(/一句话说清/), '测试用途');
}

async function fillCreatePersona(dialog: HTMLElement) {
  await userEvent.type(
    within(dialog).getByPlaceholderText(/你是 X 专家/),
    '你是测试专家。',
  );
}

async function clickNext(dialog: HTMLElement) {
  await userEvent.click(within(dialog).getByRole('button', { name: '下一步' }));
}

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
      // 试跑面板拉模型目录
      http.get('/api/v1/aiops/models', () =>
        HttpResponse.json({ providers: [], default: { provider: '', model: '' } }),
      ),
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
    expect(useButton).toHaveClass('bg-indigo-500/10', 'text-indigo-300');
    // 卡片标题用显示名称；Agent 标识单独一行，二者都必须在卡片上可见
    expect(await screen.findByText('specialist-sre')).toBeInTheDocument();
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
    // user 助理用显示名称做标题，Agent ID 保留在副标题
    await userEvent.click(await screen.findByRole('button', { name: '数据库专家' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('my_db_helper')).toBeInTheDocument();
    expect(within(dialog).getByText(/你是数据库专家/)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: /编辑/ })).toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: /复制为自定义助理/ })).not.toBeInTheDocument();
  });

  it('卡片只在显式配置模型时展示模型名', async () => {
    server.use(
      http.get('/api/v1/agents', () =>
        HttpResponse.json({
          items: [
            { ...diskAgent, model: '' },
            { ...userAgent, model: 'deepseek-chat' },
          ],
          total: 2,
        }),
      ),
    );
    render(
      <MemoryRouter>
        <AgentsPage />
      </MemoryRouter>,
    );
    expect(await screen.findByRole('button', { name: 'SRE 专家' })).toBeInTheDocument();
    // 未配置模型时不重复「默认模型」；显式配置的模型仍然展示。
    expect(screen.queryByText('默认模型')).not.toBeInTheDocument();
    expect(screen.getByText('deepseek-chat')).toBeInTheDocument();
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
          approval_policy: 'always',
          tool_risks: { get_service_health: 'approval_required' },
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
    await fillCreateBasics(dialog, 'mcp_agent');
    await clickNext(dialog);
    await fillCreatePersona(dialog);
    await clickNext(dialog);
    await clickNext(dialog);
    // 能力分组默认收起；展开 MCP Server 后只显示 Server 摘要，工具清单仍收起
    expect(within(dialog).queryByText('mcp · demo-operations')).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: /MCP Server/ }));
    expect(within(dialog).getByText('mcp · demo-operations')).toBeInTheDocument();
    expect(within(dialog).queryByText('mcp__demo-operations__get_service_health')).not.toBeInTheDocument();
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
    expect(within(dialog).getAllByText('已屏蔽 1').length).toBeGreaterThan(0);
  });

  it('套用模板会同时填正文、关键提醒、输出格式与示例', async () => {
    render(
      <MemoryRouter>
        <AgentsPage />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole('button', { name: /新建助理/ }));
    const dialog = await screen.findByRole('dialog');
    await fillCreateBasics(dialog, 'persona_agent');
    await clickNext(dialog);

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
      within(dialog).getByPlaceholderText(/oncall-duty/),
      'scoped_agent',
    );
    await userEvent.type(within(dialog).getByPlaceholderText(/例如：运维值班助理/), '范围测试助理');
    await userEvent.type(within(dialog).getByPlaceholderText(/一句话说清/), '限定知识范围的助理');
    await clickNext(dialog);
    await userEvent.click(within(dialog).getByRole('button', { name: 'SRE 排障' }));
    await clickNext(dialog);

    // 指定范围 → 勾选目录 → 预览命中数
    await userEvent.click(within(dialog).getByRole('radio', { name: '指定范围' }));
    const pathBox = await within(dialog).findByRole('checkbox', { name: /范围目录 数据库/ });
    await userEvent.click(pathBox);
    expect(
      await within(dialog).findByText((_, element) => element?.textContent?.trim() === '命中 2 篇文档'),
    ).toBeInTheDocument();
    await clickNext(dialog);
    await clickNext(dialog);

    await userEvent.click(within(dialog).getByRole('button', { name: '创建' }));

    expect(saved).not.toBeNull();
    const payload = saved as unknown as Record<string, unknown>;
    expect(payload.display_name).toBe('范围测试助理');
    expect(payload.output_format).toBe('checklist');
    expect(payload.critical_reminder).toMatch(/证据/);
    expect((payload.examples as unknown[]).length).toBe(1);
    expect(payload.knowledge_scope).toEqual({ paths: ['数据库'], doc_ids: [] });
  });

  it('知识范围目录按层级展示，选中父目录覆盖子目录', async () => {
    server.use(
      http.get('/api/v1/knowledge/paths', () =>
        HttpResponse.json({
          items: [
            { path: '运维文档/Linux', count: 2 },
            { path: '运维文档/容器', count: 1 },
          ],
          total: 3,
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
    await fillCreateBasics(dialog, 'tree_agent');
    await clickNext(dialog);
    await fillCreatePersona(dialog);
    await clickNext(dialog);
    await userEvent.click(within(dialog).getByRole('radio', { name: '指定范围' }));

    // 中间层级由子路径自动补全，计数按含子目录口径汇总
    const parent = await within(dialog).findByRole('checkbox', { name: '范围目录 运维文档' });
    const child = within(dialog).getByRole('checkbox', { name: '范围目录 运维文档/Linux' });
    expect(parent).not.toBeChecked();
    expect(child).not.toBeChecked();

    await userEvent.click(parent);
    expect(parent).toBeChecked();
    expect(child).toBeChecked();
    expect(child).toHaveAttribute('aria-disabled', 'true');

    await userEvent.click(parent);
    expect(parent).not.toBeChecked();
    expect(child).not.toBeChecked();
    expect(child).not.toHaveAttribute('aria-disabled', 'true');
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
    await userEvent.type(within(dialog).getByPlaceholderText(/oncall-duty/), 'empty_scope_agent');
    await userEvent.type(within(dialog).getByPlaceholderText(/例如：运维值班助理/), '空范围助理');
    await userEvent.type(within(dialog).getByPlaceholderText(/一句话说清/), '范围为空');
    await clickNext(dialog);
    await userEvent.click(within(dialog).getByRole('button', { name: 'SRE 排障' }));
    await clickNext(dialog);
    await userEvent.click(within(dialog).getByRole('radio', { name: '指定范围' }));
    await userEvent.click(await within(dialog).findByRole('checkbox', { name: /范围目录 数据库/ }));

    expect(await within(dialog).findByText(/保存会被拒绝/)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: '下一步' })).toBeDisabled();
  });

  it('创建向导逐步校验必填项，返回上一步保留草稿', async () => {
    render(
      <MemoryRouter>
        <AgentsPage />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole('button', { name: /新建助理/ }));
    const dialog = await screen.findByRole('dialog');

    const nextButton = within(dialog).getByRole('button', { name: '下一步' });
    expect(nextButton).toBeDisabled();
    await fillCreateBasics(dialog, 'wizard_agent');
    expect(nextButton).toBeEnabled();

    await clickNext(dialog);
    expect(within(dialog).getByText(/第 2 \/ 5 步/)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: '下一步' })).toBeDisabled();
    await fillCreatePersona(dialog);
    expect(within(dialog).getByRole('button', { name: '下一步' })).toBeEnabled();

    await userEvent.click(within(dialog).getByRole('button', { name: '上一步' }));
    expect(within(dialog).getByPlaceholderText(/oncall-duty/)).toHaveValue('wizard_agent');
    expect(within(dialog).getByPlaceholderText(/例如：运维值班助理/)).toHaveValue('测试助理');
    expect(within(dialog).getByPlaceholderText(/一句话说清/)).toHaveValue('测试用途');
  });

  it('Agent 标识按后端正则校验并给出准确提示', async () => {
    render(
      <MemoryRouter>
        <AgentsPage />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole('button', { name: /新建助理/ }));
    const dialog = await screen.findByRole('dialog');

    const nameInput = within(dialog).getByPlaceholderText(/oncall-duty/);
    expect(nameInput).toHaveAttribute('maxlength', '64');
    expect(within(dialog).getByText(/最长 64 位/)).toBeInTheDocument();

    await userEvent.type(nameInput, 'Bad Name');
    expect((await within(dialog).findAllByText(/格式不正确/)).length).toBeGreaterThan(0);
    expect(within(dialog).getByRole('button', { name: '下一步' })).toBeDisabled();

    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, 'default');
    expect((await within(dialog).findAllByText(/default 是系统保留名/)).length).toBeGreaterThan(0);

    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, 'valid-agent_2');
    expect(within(dialog).queryAllByText(/格式不正确/)).toHaveLength(0);
    expect(within(dialog).queryAllByText(/系统保留名/)).toHaveLength(0);

    await userEvent.type(within(dialog).getByPlaceholderText(/例如：运维值班助理/), '合法标识助理');
    await userEvent.type(within(dialog).getByPlaceholderText(/一句话说清/), '校验 Agent 标识格式');
    expect(within(dialog).getByRole('button', { name: '下一步' })).toBeEnabled();
  });

  it('知识范围支持搜索目录与文档并直接勾选', async () => {
    render(
      <MemoryRouter>
        <AgentsPage />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole('button', { name: /新建助理/ }));
    const dialog = await screen.findByRole('dialog');
    await fillCreateBasics(dialog, 'search_scope_agent');
    await clickNext(dialog);
    await fillCreatePersona(dialog);
    await clickNext(dialog);
    await userEvent.click(within(dialog).getByRole('radio', { name: '指定范围' }));

    const search = within(dialog).getByRole('searchbox', { name: '搜索知识范围' });
    await userEvent.type(search, '慢查询');
    expect(await within(dialog).findByText(/匹配 0 个目录 · 1 篇文档/)).toBeInTheDocument();
    const docBox = within(dialog).getByRole('checkbox', { name: '范围文档 MySQL 慢查询排查' });
    await userEvent.click(docBox);
    expect(docBox).toBeChecked();

    await userEvent.clear(search);
    await userEvent.type(search, '数据');
    const folderBox = await within(dialog).findByRole('checkbox', { name: '范围目录 数据库' });
    await userEvent.click(folderBox);
    expect(folderBox).toBeChecked();

    // 搜索不到已选项时，已选摘要仍保留，避免用户以为选择丢了
    await userEvent.clear(search);
    await userEvent.type(search, '网络');
    expect(await within(dialog).findByText('已选范围')).toBeInTheDocument();
    expect(within(dialog).getByText('MySQL 慢查询排查')).toBeInTheDocument();
  });

  it('能力绑定支持按工具、技能与 MCP 工具搜索', async () => {
    server.use(
      http.get('/api/v1/tools/builtin', () =>
        HttpResponse.json({
          items: [
            { name: 'query_promql', description: 'Query Prometheus', risk: 'safe', mount: 'always' },
            { name: 'query_incidents', description: 'List incidents', risk: 'approval_required', mount: 'always' },
          ],
          total: 2,
        }),
      ),
      http.get(listURL, () =>
        HttpResponse.json({
          items: [
            { key: 'sre-runbook', description: 'SRE runbook', tools: ['query_promql'] },
            { key: 'k8s-ops', description: 'Kubernetes operations', tools: [] },
          ],
          total: 2,
        }),
      ),
      http.get(mcpListURL, () =>
        HttpResponse.json({
          items: [{
            id: 2,
            name: 'demo-operations',
            transport: 'http',
            endpoint: 'https://mcp.example.com/mcp',
            credential: '',
            header_template_json: '{}',
            approval_policy: 'always',
            tool_risks: { get_service_health: 'approval_required' },
            enabled: true,
            status: 'ok',
            last_error: '',
            tools_cache_json: JSON.stringify([
              { name: 'get_service_health', description: 'Read health.', input_schema: { type: 'object', properties: {} } },
            ]),
          }],
          total: 1,
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
    await fillCreateBasics(dialog, 'search_capability_agent');
    await clickNext(dialog);
    await fillCreatePersona(dialog);
    await clickNext(dialog);
    await clickNext(dialog);

    const search = within(dialog).getByRole('searchbox', { name: '搜索能力' });
    await userEvent.type(search, 'promql');
    expect(await within(dialog).findByText('匹配 2 项')).toBeInTheDocument();
    expect(within(dialog).getByText('query_promql')).toBeInTheDocument();
    // 风险标签随目录行渲染：内置安全工具标「安全」
    expect(within(dialog).getByText('安全')).toBeInTheDocument();
    expect(within(dialog).queryByText('query_incidents')).not.toBeInTheDocument();
    expect(within(dialog).getByText('sre-runbook')).toBeInTheDocument();
    expect(within(dialog).queryByText('k8s-ops')).not.toBeInTheDocument();
    expect(within(dialog).queryByText('mcp · demo-operations')).not.toBeInTheDocument();

    await userEvent.clear(search);
    await userEvent.type(search, 'service_health');
    expect(await within(dialog).findByText('匹配 1 项')).toBeInTheDocument();
    expect(within(dialog).getByText('mcp · demo-operations')).toBeInTheDocument();
    expect(
      within(dialog).getByText('mcp__demo-operations__get_service_health'),
    ).toBeInTheDocument();
    // MCP 工具行按 tool_risks 渲染「需审批」
    expect(within(dialog).getByText('需审批')).toBeInTheDocument();
  });

  it('详情弹窗展示知识范围与输出格式', async () => {
    render(
      <MemoryRouter>
        <AgentsPage />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole('button', { name: '数据库专家' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('知识范围')).toBeInTheDocument();
    expect(within(dialog).getByText('数据库')).toBeInTheDocument();
    expect(within(dialog).getByText('清单')).toBeInTheDocument();
    expect(within(dialog).getByText('输出格式与示例')).toBeInTheDocument();
  });

  it('编辑器 footer 有版本历史入口，可回滚到旧版本', async () => {
    const rolled: number[] = [];
    server.use(
      http.get('/api/v1/agents/custom/my_db_helper/versions', () =>
        HttpResponse.json({
          items: [
            { version: 2, change_summary: '改提示', created_by: 1, created_at: '2026-11-10T00:00:00Z', restored_from_version: null },
            { version: 1, change_summary: '初版', created_by: 1, created_at: '2026-11-09T00:00:00Z', restored_from_version: null },
          ],
          total: 2,
        }),
      ),
      http.post('/api/v1/agents/custom/my_db_helper/rollback', async ({ request }) => {
        const body = (await request.json()) as { version: number };
        rolled.push(body.version);
        return HttpResponse.json({ ...userAgent, version: 3 });
      }),
    );

    render(
      <MemoryRouter>
        <AgentsPage />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole('button', { name: '数据库专家' }));
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /编辑/ }));

    await userEvent.click(await screen.findByRole('button', { name: '版本' }));
    expect(await screen.findByText('版本历史')).toBeInTheDocument();
    expect(await screen.findByText('v2')).toBeInTheDocument();
    expect(screen.getByText('v1')).toBeInTheDocument();

    // 列表按新→旧排列：第二个「回滚」按钮对应 v1
    const rollbackButtons = screen.getAllByRole('button', { name: '回滚' });
    await userEvent.click(rollbackButtons[1]);
    const confirmDialog = await screen.findByRole('dialog', { name: /回滚到版本 1/ });
    await userEvent.click(within(confirmDialog).getByRole('button', { name: /^回滚$/ }));
    await waitFor(() => expect(rolled).toContain(1));
  });

  it('编辑器 footer 的试跑按钮打开草稿试跑面板', async () => {
    render(
      <MemoryRouter>
        <AgentsPage />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole('button', { name: '数据库专家' }));
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /编辑/ }));

    await userEvent.click(await screen.findByRole('button', { name: '试跑' }));
    expect(await screen.findByText('试跑不落会话、不影响正式对话')).toBeInTheDocument();
  });
});
