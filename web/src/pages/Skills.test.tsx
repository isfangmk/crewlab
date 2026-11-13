// Skills 页面测试 — 覆盖技能目录的核心链路：
// 内置技能只读详情 / 新建技能包（含工具声明与资源文件）/ 编辑 / ZIP 导入预览。
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import SkillsPage from './Skills';
import { server } from '@/test/msw-server';

vi.mock('@/store/auth', () => ({
  useAuth: Object.assign(
    <T,>(selector: (s: { role: string }) => T): T => selector({ role: 'admin' }),
    { getState: () => ({ logout: () => {} }) },
  ),
  getToken: () => null,
}));

const longDesc =
  'Analyze database health and performance from ongrid database metrics sources. ' +
  'Use this as the first tool for any MySQL question that exporter metrics can cover.';

const diskSkill = {
  key: 'database-health',
  name: 'database-health',
  description: longDesc,
  class: 'safe',
  group: 'observe',
  source: 'disk',
  instructions: '# 数据库体检\n\n1. 先看 references/checks.md\n2. 再输出结论',
  params: [
    { name: 'device_id', type: 'int', required: true, desc: '目标设备 id' },
    { name: 'window', type: 'duration', default: '15m', desc: '分析时间窗' },
  ],
  tools: ['query_knowledge'],
  references: [{ path: 'checks.md', size: 12, binary: false }],
  assets: [{ path: 'logo.png', size: 2048, binary: true }],
  scripts: [],
  inventory_only: false,
  inject_state: false,
};

const userSkill = {
  key: 'runbook-writer',
  name: 'runbook-writer',
  description: '把排障过程整理成 runbook。',
  class: 'safe',
  group: 'knowledge',
  source: 'user',
  instructions: '# 写 runbook',
  params: [],
  tools: [],
  references: [],
  assets: [],
  scripts: [],
  inventory_only: false,
  inject_state: false,
};

// 分组目录：后端给固定枚举 + 中英文标题
const skillGroups = {
  groups: [
    { key: 'observe', title: '观测', title_en: 'Observability' },
    { key: 'device', title: '设备管理', title_en: 'Devices' },
    { key: 'knowledge', title: '知识', title_en: 'Knowledge' },
    { key: 'chart', title: '可视化', title_en: 'Visualization' },
    { key: 'other', title: '其他', title_en: 'Other' },
  ],
};

const builtinTools = [
  { name: 'query_knowledge', description: '检索组织知识库', risk: 'safe', mount: 'always' },
];

// 技能可以声明 MCP 工具：编辑器要按 Server 分组列出探测缓存里的工具
const mcpServers = [
  {
    id: 1,
    name: 'ai-engine',
    transport: 'http',
    endpoint: 'http://127.0.0.1:1',
    credential: '',
    header_template_json: '{}',
    trusted: false,
    approval_policy: 'never',
    enabled: true,
    status: 'ok',
    last_error: '',
    tools_cache_json: JSON.stringify([
      {
        name: 'get_host_load',
        description: '查询主机负载',
        input_schema: {
          type: 'object',
          properties: { host: { type: 'string', description: '主机名' } },
          required: ['host'],
        },
      },
    ]),
  },
];

function renderPage() {
  return render(
    <MemoryRouter>
      <SkillsPage />
    </MemoryRouter>,
  );
}

describe('SkillsPage', () => {
  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
    server.use(
      http.get('/api/v1/skills', () =>
        HttpResponse.json({ items: [diskSkill, userSkill], total: 2 }),
      ),
      http.get('/api/v1/skills/groups', () => HttpResponse.json(skillGroups)),
      http.get('/api/v1/tools/builtin', () =>
        HttpResponse.json({ items: builtinTools, total: builtinTools.length }),
      ),
      http.get('/api/v1/mcp/servers', () =>
        HttpResponse.json({ items: mcpServers, total: mcpServers.length }),
      ),
    );
  });

  it('点击技能行打开详情：正文、声明工具与三类资源清单都在，内置技能不可编辑', async () => {
    renderPage();
    await userEvent.click((await screen.findAllByText('database-health'))[0]);

    // 完整描述不再截断（列表行 + 弹窗正文各一份）
    expect(await screen.findAllByText(longDesc)).toHaveLength(2);
    expect(screen.getByText('device_id')).toBeInTheDocument();
    expect(screen.getByText('目标设备 id')).toBeInTheDocument();
    expect(screen.getByText('15m')).toBeInTheDocument();
    // SKILL.md 正文与资源清单
    expect(screen.getByText(/先看 references\/checks\.md/)).toBeInTheDocument();
    expect(screen.getByText('checks.md')).toBeInTheDocument();
    expect(screen.getByText('logo.png')).toBeInTheDocument();
    expect(screen.getByText('2048 B')).toBeInTheDocument();
    // 内置技能随代码发布：只读、可导出
    expect(screen.getByText(/内置技能：随 backend\/skills 发布，不可编辑/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /导出 ZIP/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '编辑' })).not.toBeInTheDocument();
  });

  it('导出技能包：成功后用全局 Toast 提示文件名', async () => {
    // jsdom 没有 URL.createObjectURL，导出流程会在这里抛错
    const createObjectURL = vi.fn(() => 'blob:skill');
    const revokeObjectURL = vi.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    server.use(
      http.get('/api/v1/skills/:key/export', () =>
        HttpResponse.json('zip-bytes', {
          headers: { 'content-disposition': 'attachment; filename="database-health.zip"' },
        }),
      ),
    );

    renderPage();
    await userEvent.click((await screen.findAllByRole('button', { name: '导出' }))[0]);

    // 全局 Toast：role=status，成功文案带导出的文件名
    expect(await screen.findByRole('status')).toHaveTextContent('database-health.zip');
  });

  it('来源列区分内置 / 新建 / ZIP 导入，来源筛选可过滤', async () => {
    const importedSkill = { ...userSkill, key: 'imported-pack', name: 'imported-pack', origin: 'imported' };
    server.use(
      http.get('/api/v1/skills', () =>
        HttpResponse.json({ items: [diskSkill, userSkill, importedSkill], total: 3 }),
      ),
    );

    renderPage();
    // 三种来源各出现一次
    expect((await screen.findAllByText('内置')).length).toBeGreaterThan(0);
    expect((await screen.findAllByText('新建')).length).toBeGreaterThan(0);
    expect((await screen.findAllByText('ZIP 导入')).length).toBeGreaterThan(0);

    // 选中「ZIP 导入」来源后只剩导入的技能
    const originChip = (await screen.findAllByText('ZIP 导入')).find(
      (el) => el.closest('button') !== null,
    );
    await userEvent.click(originChip as HTMLElement);
    await waitFor(() => {
      expect(screen.getAllByText('imported-pack').length).toBeGreaterThan(0);
      expect(screen.queryByText('runbook-writer')).not.toBeInTheDocument();
    });
  });

  it('按分组分组展示，筛选胶囊按后端枚举过滤', async () => {
    renderPage();
    // 分组标题出现在筛选胶囊与表格分组头两处
    expect((await screen.findAllByText('观测')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('知识').length).toBeGreaterThan(0);

    // 点「知识」分组胶囊后，只剩知识组的技能
    const groupChip = (await screen.findAllByText('知识')).find(
      (el) => el.closest('button') !== null,
    );
    await userEvent.click(groupChip as HTMLElement);
    await waitFor(() => {
      expect(screen.queryByText('database-health')).not.toBeInTheDocument();
      expect(screen.getAllByText('runbook-writer').length).toBeGreaterThan(0);
    });
  });

  it('用户技能可以编辑：表单回填正文与工具声明后提交 PATCH', async () => {
    const captured = {} as { body?: Record<string, unknown> };
    server.use(
      http.get('/api/v1/skills/:key', () =>
        HttpResponse.json({ ...userSkill, instructions: '# 写 runbook\n\n按时间线记录。' }),
      ),
      http.patch('/api/v1/skills/:key', async ({ request }) => {
        captured.body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...userSkill, instructions: String(captured.body.instructions) });
      }),
    );

    renderPage();
    // 行内操作是带文字的 RowAction：与全站列表行一致
    await userEvent.click(await screen.findByRole('button', { name: '编辑' }));

    // 回填：正文来自详情接口
    expect(await screen.findByDisplayValue(/# 写 runbook/)).toBeInTheDocument();
    const textarea = screen.getByDisplayValue(/# 写 runbook[\s\S]*按时间线记录/);
    await userEvent.clear(textarea);
    await userEvent.type(textarea, '# 写 runbook\n\n按时间线与影响面记录。');
    await userEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(captured.body).toBeDefined());
    expect(captured.body?.key).toBe('runbook-writer');
    expect(String(captured.body?.instructions)).toContain('按时间线与影响面记录');
    // 平台侧字段一并提交
    expect(captured.body?.risk).toBe('safe');
    expect(captured.body?.group).toBe('knowledge');
  });

  it('新建技能：填 key/描述/正文并勾选工具后提交 POST', async () => {
    const captured = {} as { body?: Record<string, unknown> };
    server.use(
      http.post('/api/v1/skills', async ({ request }) => {
        captured.body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          ...userSkill,
          key: String(captured.body.key),
          description: String(captured.body.description),
        });
      }),
      http.get('/api/v1/skills/db-explain', () =>
        HttpResponse.json({
          ...userSkill,
          key: 'db-explain',
          description: '解释慢查询',
          tools: ['query_knowledge'],
        }),
      ),
    );

    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: /新建技能/ }));

    await userEvent.type(screen.getByPlaceholderText('knowledge-search / my-runbook'), 'db-explain');
    await userEvent.type(
      screen.getByPlaceholderText('一句话说明何时该加载这个技能'),
      '解释慢查询',
    );
    await userEvent.type(
      screen.getByPlaceholderText(/# 什么时候用/),
      '# 慢查询\n\n先读 references/slow.md',
    );
    await userEvent.click(screen.getByRole('combobox', { name: '分组' }));
    await userEvent.click(await screen.findByRole('option', { name: '知识' }));
    await userEvent.click(screen.getByRole('checkbox', { name: '声明工具 query_knowledge' }));
    await userEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(captured.body).toBeDefined());
    expect(captured.body?.key).toBe('db-explain');
    expect(captured.body?.tools).toEqual(['query_knowledge']);
    expect(captured.body?.group).toBe('knowledge');
    expect(captured.body?.references).toEqual([]);
  });

  it('新建技能可以声明 MCP 工具：按 Server 分组勾选后按 wire 名提交', async () => {
    const captured = {} as { body?: Record<string, unknown> };
    server.use(
      http.post('/api/v1/skills', async ({ request }) => {
        captured.body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...userSkill, key: 'mcp-skill' });
      }),
    );

    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: /新建技能/ }));

    await userEvent.type(screen.getByPlaceholderText('knowledge-search / my-runbook'), 'mcp-skill');
    await userEvent.type(screen.getByPlaceholderText('一句话说明何时该加载这个技能'), '查主机负载');
    await userEvent.type(screen.getByPlaceholderText(/# 什么时候用/), '# 负载\n\n调用 MCP 工具');
    await userEvent.click(screen.getByRole('combobox', { name: '分组' }));
    await userEvent.click(await screen.findByRole('option', { name: '设备管理' }));

    // MCP 工具按 Server 分组出现，勾选后以 wire 名进 tools
    expect(await screen.findByText('mcp · ai-engine')).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole('checkbox', { name: '声明工具 mcp__ai-engine__get_host_load' }),
    );
    await userEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(captured.body).toBeDefined());
    expect(captured.body?.tools).toEqual(['mcp__ai-engine__get_host_load']);
  });

  it('详情把 MCP 工具标出来源，不可用时给出告警', async () => {
    server.use(
      http.get('/api/v1/skills', () =>
        HttpResponse.json({
          items: [
            {
              ...userSkill,
              key: 'mcp-declared',
              tools: ['mcp__ai-engine__get_host_load', 'mcp__gone__ask'],
              tool_refs: [
                {
                  name: 'mcp__ai-engine__get_host_load',
                  kind: 'mcp',
                  server: 'ai-engine',
                  available: true,
                  description: '查询主机负载',
                },
                {
                  name: 'mcp__gone__ask',
                  kind: 'unknown',
                  available: false,
                  description: '',
                },
              ],
            },
          ],
          total: 1,
        }),
      ),
    );

    renderPage();
    // 只有名称可点开详情：行内其他单元格不再触发打开
    await userEvent.click(await screen.findByRole('button', { name: 'runbook-writer' }));

    expect(await screen.findByText('mcp · ai-engine · get_host_load')).toBeInTheDocument();
    // 目录里已不存在的声明原样展示，且 tone=warning
    const gone = screen.getByText('mcp__gone__ask');
    expect(gone).toBeInTheDocument();
    expect(gone.closest('[data-tone]')?.getAttribute('data-tone')).toBe('warning');
  });

  it('导入 ZIP：先解析预览，再带风险等级与分组落库', async () => {
    // MSW 在 jsdom 下无法解析 multipart（jsdom 的 File 流读不出来，formData() 会挂住），
    // 这里用 fetch 探针抓取请求体，MSW 只负责返回成功响应。
    const realFetch = globalThis.fetch;
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) =>
      realFetch(input, init),
    );
    server.use(
      http.post('/api/v1/skills/parse', () =>
        HttpResponse.json({
          key: 'imported-skill',
          description: '来自 zip 的技能',
          instructions: '# 导入正文',
          preapproved_tools: '',
          risk: null,
          group: null,
          tools: ['query_knowledge'],
          inject_state: false,
          references: [{ path: 'guide.md', size: 10, binary: false }],
          assets: [],
          scripts: [],
        }),
      ),
      http.post('/api/v1/skills/import', () =>
        HttpResponse.json({ ...userSkill, key: 'imported-skill' }),
      ),
    );

    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: /导入 ZIP/ }));

    const zip = new File([new Uint8Array([0x50, 0x4b, 0x03, 0x04])], 'skill.zip', {
      type: 'application/zip',
    });
    // fireEvent.change 直接挂文件：userEvent.upload 会模拟点击隐藏的 file input，
    // jsdom 下会让 base-ui 的 focus guard 收到 relatedTarget=Document 而抛错。
    fireEvent.change(screen.getByLabelText('选择技能包 ZIP 文件'), {
      target: { files: [zip] },
    });

    // 预览：key / 资源计数 / 工具
    expect(await screen.findByText('imported-skill')).toBeInTheDocument();
    expect(screen.getByText(/references 1 · assets 0 · scripts 0/)).toBeInTheDocument();

    // 包内没有 risk/group：导入时显式选择分组
    await userEvent.click(screen.getByRole('combobox', { name: '分组' }));
    await userEvent.click(await screen.findByRole('option', { name: '知识' }));
    // 解析请求回来后才解除 busy，按钮可用再点，避免竞态。
    const importBtn = screen.getByRole('button', { name: '导入' });
    await waitFor(() => expect(importBtn).toBeEnabled());
    // 用 fireEvent 而非 userEvent：jsdom 下 base-ui 的 focus guard 在焦点迁移时
    // 会抛 relatedTarget 异常并打断 userEvent 后续的点击派发。
    fireEvent.click(importBtn);

    await waitFor(() => {
      expect(fetchSpy.mock.calls.some(([url]) => String(url).endsWith('/skills/import'))).toBe(true);
    });
    const importCall = fetchSpy.mock.calls.find(([url]) => String(url).endsWith('/skills/import'));
    const fd = importCall?.[1]?.body as FormData;
    expect(fd).toBeInstanceOf(FormData);
    expect(fd.get('file')).toBeInstanceOf(File);
    expect(fd.get('risk')).toBe('safe');
    expect(fd.get('group')).toBe('knowledge');
  });

  it('详情弹窗按分组列出资源文件', async () => {
    renderPage();
    await userEvent.click((await screen.findAllByText('database-health'))[0]);
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/参考资料 references\//)).toBeInTheDocument();
    expect(within(dialog).getByText(/素材 assets\//)).toBeInTheDocument();
    expect(within(dialog).getByText('二进制')).toBeInTheDocument();
  });
});
