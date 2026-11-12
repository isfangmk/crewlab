// Workflows 页面测试 — 覆盖画布清单与运行记录两种视图：
//   1. 清单：规模 + 状态 + 版本 + 完整时间，默认最近更新在前，列头可切正序
//   2. 搜索 / 状态筛选（服务端筛选，分页条展示总数）
//   3. 分页：翻页与每页条数走服务端 limit/offset
//   4. 新建：模板预置合法图（start → knowledge → agent → end），创建后进入编排页
//   5. 运行：必填变量未填禁止提交，填好后先校验再 POST 运行并跳到运行详情
//   6. 运行记录：服务端返回的运行列表、状态胶囊与耗时
//   7. 删除：走统一确认弹窗
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import WorkflowsPage from './Workflows';
import { server } from '@/test/msw-server';
import { fullDateTime } from '@/lib/format';
import { emptyNode } from '@/lib/workflowMeta';
import type { WorkflowSummary } from '@/api/workflows';

vi.mock('@/store/auth', () => ({
  useAuth: Object.assign(
    <T,>(selector: (s: { role: string }) => T): T => selector({ role: 'admin' }),
    { getState: () => ({ logout: () => {} }) },
  ),
  getToken: () => null,
}));

const listURL = '/api/v1/workflows';
const runsURL = '/api/v1/workflows/runs/list';
const validateURL = '/api/v1/workflows/validate';
const catalogURL = '/api/v1/workflows/catalog';

/** 目录：知识问答模板要带 LLM 节点，模型从这里取。 */
const catalog = {
  node_groups: [
    { key: 'flow', title: '编排', title_en: 'Flow' },
    { key: 'model', title: '模型', title_en: 'Model' },
  ],
  node_kinds: [
    { key: 'start', title: '开始', title_en: 'Start', group: 'flow' },
    { key: 'knowledge', title: '知识检索', title_en: 'Knowledge search', group: 'action' },
    { key: 'agent', title: 'LLM 节点', title_en: 'LLM node', group: 'model' },
    { key: 'end', title: '结束', title_en: 'End', group: 'flow' },
  ],
  models: [{ provider: 'deepseek', model: 'deepseek-flash', title: 'deepseek · deepseek-flash' }],
  mcp_tools: [],
};

function summary(overrides: Partial<WorkflowSummary> & Pick<WorkflowSummary, 'id' | 'name'>): WorkflowSummary {
  return {
    description: '',
    status: 'draft',
    version: 1,
    validation_error: '',
    // 默认给「已发布」：既有用例都在测运行入口，未发布的显隐另有专门用例
    has_published: true,
    node_count: 4,
    edge_count: 3,
    warnings: [],
    last_run: null,
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
    ...overrides,
  };
}

const older = summary({
  id: 11,
  name: '旧画布',
  description: '巡检',
  updated_at: '2026-01-02T03:04:00Z',
});
const newer = summary({
  id: 12,
  name: '新画布',
  description: '知识问答',
  status: 'published',
  version: 3,
  warnings: ['agent 节点未选择模型'],
  last_run: { run_uid: 'run-newer', status: 'succeeded', created_at: '2026-08-02T03:05:00Z' },
  updated_at: '2026-08-02T03:04:00Z',
});

/** 知识问答模板产出的定义：运行弹窗从这里取开始节点变量。 */
function questionDefinition() {
  const start = emptyNode('start', 'start', { x: 0, y: 0 });
  start.variables = [{ name: 'question', type: 'string', description: '要回答的问题', required: true, default: null }];
  const answer = emptyNode('agent', 'answer', { x: 300, y: 0 });
  answer.provider = 'openai';
  answer.model = 'gpt-4o';
  answer.instruction = '回答 {{question}}';
  const end = emptyNode('end', 'end', { x: 600, y: 0 });
  return {
    nodes: [start, answer, end],
    edges: [
      { id: 'e1', source: 'start', target: 'answer', route: '' },
      { id: 'e2', source: 'answer', target: 'end', route: '' },
    ],
  };
}

/**
 * 列表接口的 mock：按后端的口径处理 q/status/sort/limit/offset，
 * 搜索、筛选、排序、分页都在「服务端」完成，total 是筛选后的总数。
 */
function useListHandlers(items: WorkflowSummary[] = [older, newer]) {
  server.use(
    http.get(listURL, ({ request }) => {
      const url = new URL(request.url);
      const keyword = (url.searchParams.get('q') ?? '').trim().toLowerCase();
      const status = url.searchParams.get('status') ?? '';
      const sort = url.searchParams.get('sort') ?? 'desc';
      const limit = Number.parseInt(url.searchParams.get('limit') ?? '20', 10);
      const offset = Number.parseInt(url.searchParams.get('offset') ?? '0', 10);

      const matched = items
        .filter((item) => (status === '' ? true : item.status === status))
        .filter((item) =>
          keyword === ''
            ? true
            : item.name.toLowerCase().includes(keyword) || item.description.toLowerCase().includes(keyword),
        )
        .sort((a, b) => {
          const delta = Date.parse(a.updated_at) - Date.parse(b.updated_at);
          return sort === 'asc' ? delta : -delta;
        });

      return HttpResponse.json({ items: matched.slice(offset, offset + limit), total: matched.length });
    }),
    http.get(runsURL, () => HttpResponse.json({ items: [], total: 0 })),
    http.get(catalogURL, () => HttpResponse.json(catalog)),
  );
}

function renderPage(entry = '/workflows') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/workflows" element={<WorkflowsPage />} />
        <Route path="/workflows/:id" element={<div>编排页占位</div>} />
        <Route path="/workflows/runs/:runUid" element={<div>运行详情占位</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

/** 行内动作按钮的可访问名只有动作词，先按标题定位到行。 */
function rowOf(title: string): HTMLElement {
  const cell = screen.getByText(title);
  const row = cell.closest('tr');
  if (!(row instanceof HTMLElement)) throw new Error(`workflow row not found: ${title}`);
  return row;
}

function firstRowTitle(): string {
  return document.querySelector('tbody tr td:nth-child(1)')?.textContent ?? '';
}

describe('WorkflowsPage', () => {
  beforeEach(() => {
    // 固定中文与格式，避免断言受运行机器语言影响。
    localStorage.setItem('ongrid-locale', 'zh-CN');
    useListHandlers();
  });

  it('清单：结构规模 + 发布状态 + 运行状态 + 版本', async () => {
    renderPage();
    await screen.findByText('新画布');

    // 表头顺序：工作流 / 结构 / 状态 / 运行状态 / 版本 / 更新时间 / 操作
    expect(
      [...document.querySelectorAll('thead th')].map((th) => th.textContent?.trim()),
    ).toEqual(['工作流', '结构', '状态', '运行状态', '版本', '更新时间', '操作']);

    const row = rowOf('新画布');
    expect(row).toHaveTextContent('4节点');
    expect(row).toHaveTextContent('3连线');
    expect(within(row).getByText('已发布')).toBeInTheDocument();
    // 运行状态取最近一次运行，可点进运行详情
    expect(within(row).getByText('成功')).toBeInTheDocument();
    expect(within(row).getByRole('button', { name: '成功' })).toHaveAttribute('title', 'run-newer');
    expect(within(rowOf('旧画布')).getByText('未运行')).toBeInTheDocument();
    expect(within(row).getByText('v3')).toBeInTheDocument();
    expect(within(row).getByText('1 条提示')).toBeInTheDocument();
    // 更新时间用完整日期时间，不出现「N 天前」
    expect(within(row).getByText(fullDateTime('2026-08-02T03:04:00Z'))).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/天前|小时前|分钟前/);

    // 默认倒序：最近更新的在最前
    expect(firstRowTitle()).toContain('新画布');

    // 列头切换为正序
    await userEvent.click(screen.getByRole('button', { name: /更新时间/ }));
    await waitFor(() => expect(firstRowTitle()).toContain('旧画布'));

    // 再点回倒序
    await userEvent.click(screen.getByRole('button', { name: /更新时间/ }));
    await waitFor(() => expect(firstRowTitle()).toContain('新画布'));
  });

  it('运行状态：按最近一次运行展示运行中 / 等待人工 / 失败 / 未运行，点击进运行详情', async () => {
    const items = [
      summary({
        id: 21,
        name: '运行中的',
        last_run: { run_uid: 'run-running', status: 'running', created_at: '2026-08-03T00:00:00Z' },
      }),
      summary({
        id: 22,
        name: '等待人工的',
        last_run: { run_uid: 'run-waiting', status: 'waiting', created_at: '2026-08-03T00:00:00Z' },
      }),
      summary({
        id: 23,
        name: '失败的',
        last_run: { run_uid: 'run-failed', status: 'failed', created_at: '2026-08-03T00:00:00Z' },
      }),
      summary({ id: 24, name: '未运行过的' }),
    ];
    server.use(http.get(listURL, () => HttpResponse.json({ items, total: items.length })));

    renderPage();
    await screen.findByText('运行中的');

    expect(within(rowOf('运行中的')).getByText('运行中')).toBeInTheDocument();
    expect(within(rowOf('等待人工的')).getByText('等待人工')).toBeInTheDocument();
    expect(within(rowOf('失败的')).getByText('失败')).toBeInTheDocument();
    expect(within(rowOf('未运行过的')).getByText('未运行')).toBeInTheDocument();

    await userEvent.click(within(rowOf('运行中的')).getByRole('button', { name: '运行中' }));
    expect(await screen.findByText('运行详情占位')).toBeInTheDocument();
  });

  it('分页：翻页与每页条数都按 limit/offset 向服务端取数', async () => {
    const many = Array.from({ length: 25 }, (_, index) =>
      summary({
        id: 100 + index,
        name: `画布 ${String(index + 1).padStart(2, '0')}`,
        // 序号越小越新，倒序时 画布 01 在最前
        updated_at: new Date(Date.UTC(2026, 0, 25 - index)).toISOString(),
      }),
    );
    const requests: { limit: string; offset: string }[] = [];
    server.use(
      http.get(listURL, ({ request }) => {
        const url = new URL(request.url);
        const limit = Number.parseInt(url.searchParams.get('limit') ?? '20', 10);
        const offset = Number.parseInt(url.searchParams.get('offset') ?? '0', 10);
        requests.push({ limit: String(limit), offset: String(offset) });
        return HttpResponse.json({ items: many.slice(offset, offset + limit), total: many.length });
      }),
    );

    renderPage();
    expect(await screen.findByText('画布 01')).toBeInTheDocument();
    expect(screen.getByText('1-20 / 共 25 条')).toBeInTheDocument();
    expect(screen.getByText('1 / 2')).toBeInTheDocument();
    expect(screen.queryByText('画布 21')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(await screen.findByText('画布 21')).toBeInTheDocument();
    expect(requests).toContainEqual({ limit: '20', offset: '0' });
    expect(requests).toContainEqual({ limit: '20', offset: '20' });
    expect(screen.getByText('21-25 / 共 25 条')).toBeInTheDocument();
    expect(screen.getByText('2 / 2')).toBeInTheDocument();
  });

  it('搜索与状态筛选共同生效', async () => {
    renderPage();

    await userEvent.type(await screen.findByPlaceholderText('搜索名称 / 描述'), '巡检');
    await waitFor(() => expect(screen.queryByText('新画布')).not.toBeInTheDocument());
    expect(screen.getByText('旧画布')).toBeInTheDocument();

    // 清空关键字后按状态筛选
    await userEvent.clear(screen.getByPlaceholderText('搜索名称 / 描述'));
    await userEvent.click(screen.getByRole('button', { name: '已发布' }));
    await waitFor(() => expect(screen.queryByText('旧画布')).not.toBeInTheDocument());
    expect(screen.getByText('新画布')).toBeInTheDocument();

    // 关键字与状态是「与」关系：草稿 + 「新」没有交集，走空态
    await userEvent.click(screen.getByRole('button', { name: '草稿' }));
    await userEvent.type(screen.getByPlaceholderText('搜索名称 / 描述'), '新');
    expect(await screen.findByText('没有匹配的工作流')).toBeInTheDocument();

    // 清除筛选后两个条件一起复位
    await userEvent.click(screen.getByRole('button', { name: '清除筛选' }));
    expect(await screen.findByText('旧画布')).toBeInTheDocument();
    expect(screen.getByText('新画布')).toBeInTheDocument();
  });

  it('新建：知识问答模板预置合法图，创建后直接进编排页', async () => {
    let createdBody: Record<string, unknown> | null = null;
    server.use(
      http.post(listURL, async ({ request }) => {
        createdBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          ...summary({ id: 77, name: String(createdBody.name) }),
          definition: createdBody.definition,
        });
      }),
    );

    renderPage();
    await screen.findByText('新画布');
    await userEvent.click(screen.getByRole('button', { name: '新建工作流' }));

    await userEvent.type(screen.getByPlaceholderText('例如：工单巡检'), '客服问答');
    await userEvent.click(screen.getByRole('button', { name: /知识问答/ }));
    // 目录里的第一个模型会预置到 LLM 节点，避免后端「必须指定模型」拒绝保存
    expect(await screen.findByText(/deepseek · deepseek-flash/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '创建并打开' }));

    await waitFor(() => expect(createdBody).not.toBeNull());
    const body = createdBody as unknown as {
      name: string;
      status?: string;
      definition: {
        nodes: { id: string; kind: string; query: string; instruction: string; provider: string; model: string }[];
      };
    };
    expect(body.name).toBe('客服问答');
    // 创建请求只带名称、描述与定义：状态与版本由后端生成，发布才升版本。
    expect(body.status).toBeUndefined();
    // 模板只预置结构：四类节点按 start → knowledge → agent → end 连好
    expect(body.definition.nodes.map((node) => node.kind)).toEqual(['start', 'knowledge', 'agent', 'end']);
    expect(body.definition.nodes[1].query).toBe('{{question}}');
    expect(body.definition.nodes[2].instruction).toContain('{{context}}');
    expect(body.definition.nodes[2]).toMatchObject({ provider: 'deepseek', model: 'deepseek-flash' });

    // 创建成功后跳到编排页
    expect(await screen.findByText('编排页占位')).toBeInTheDocument();
  });

  it('新建：没有可用模型时知识问答模板退化成「检索 → 结束」', async () => {
    let createdBody: Record<string, unknown> | null = null;
    server.use(
      http.get(catalogURL, () => HttpResponse.json({ ...catalog, models: [] })),
      http.post(listURL, async ({ request }) => {
        createdBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...summary({ id: 78, name: '无模型' }), definition: createdBody.definition });
      }),
    );

    renderPage();
    await screen.findByText('新画布');
    await userEvent.click(screen.getByRole('button', { name: '新建工作流' }));
    await userEvent.type(screen.getByPlaceholderText('例如：工单巡检'), '无模型');
    await userEvent.click(screen.getByRole('button', { name: /知识问答/ }));

    // 明确告知用户模板降级，而不是保存时才报错
    expect(await screen.findByText(/还没有启用任何模型/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '创建并打开' }));

    await waitFor(() => expect(createdBody).not.toBeNull());
    const body = createdBody as unknown as { definition: { nodes: { kind: string }[] } };
    expect(body.definition.nodes.map((node) => node.kind)).toEqual(['start', 'knowledge', 'end']);
  });

  it('运行：必填变量为空时禁止提交，填好后校验再发运行请求', async () => {
    const definition = questionDefinition();
    let runBody: Record<string, unknown> | null = null;
    server.use(
      http.get(`${listURL}/12`, () =>
        HttpResponse.json({ ...newer, definition }),
      ),
      http.post(validateURL, () => HttpResponse.json({ ok: true, error: '', warnings: [] })),
      http.post(`${listURL}/12/runs/start`, async ({ request }) => {
        runBody = (await request.json()) as Record<string, unknown>;
        // 发起接口只返回流水号：执行在后台，前端立刻跳转
        return HttpResponse.json({ run_uid: 'run-abc', workflow_id: 12 });
      }),
    );

    renderPage();
    await screen.findByText('新画布');
    await userEvent.click(within(rowOf('新画布')).getByRole('button', { name: '运行' }));

    // 弹窗按开始节点声明渲染输入项，必填项没填时提交按钮是禁用的
    expect(await screen.findByText('运行「新画布」')).toBeInTheDocument();
    const submit = screen.getByRole('button', { name: '开始运行' });
    expect(submit).toBeDisabled();
    expect(screen.getByText('还需填写：question')).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText('question'), '磁盘告警怎么处理？');
    await waitFor(() => expect(submit).toBeEnabled());
    await userEvent.click(submit);

    await waitFor(() => expect(runBody).not.toBeNull());
    // 列表页跑的是已发布版本，不是正在编辑的草稿
    expect(runBody).toEqual({ values: { question: '磁盘告警怎么处理？' }, source: 'published' });
    // 运行发起后进入详情页
    expect(await screen.findByText('运行详情占位')).toBeInTheDocument();
  });

  it('未发布的工作流不给「运行」入口', async () => {
    server.use(
      http.get(listURL, () =>
        HttpResponse.json({
          items: [
            summary({ id: 31, name: '从未发布的', status: 'draft', has_published: false }),
            summary({ id: 32, name: '发布过的', status: 'published', has_published: true }),
          ],
          total: 2,
        }),
      ),
    );

    renderPage();
    await screen.findByText('从未发布的');

    // 没发布过就没有可运行的正式版本，入口整个不出现（而不是灰按钮）
    const draftRow = rowOf('从未发布的');
    expect(within(draftRow).queryByRole('button', { name: '运行' })).toBeNull();
    // 编排入口不受影响
    expect(within(draftRow).getByRole('button', { name: '编排' })).toBeInTheDocument();

    expect(within(rowOf('发布过的')).getByRole('button', { name: '运行' })).toBeInTheDocument();
  });

  it('运行记录：展示状态胶囊、完整时间与耗时', async () => {
    server.use(
      http.get(runsURL, () =>
        HttpResponse.json({
          items: [
            {
              id: 1,
              run_uid: 'run-abc',
              workflow_id: 12,
              workflow_name: '新画布',
              workflow_version: 3,
              workflow_status: 'published',
              status: 'succeeded',
              trigger: 'manual',
              triggered_by: 'admin',
              input: '{"question":"hi"}',
              output: '"ok"',
              error: '',
              duration_ms: 1234,
              created_at: '2026-08-02T03:04:00Z',
              finished_at: '2026-08-02T03:04:01Z',
            },
          ],
          total: 1,
        }),
      ),
    );

    renderPage('/workflows?tab=runs');
    expect(await screen.findByText('新画布')).toBeInTheDocument();
    // 「成功 / 失败」也是状态筛选按钮的文案，断言要落在表格里
    const table = screen.getByRole('table');
    expect(within(table).getByText('成功')).toBeInTheDocument();
    expect(within(table).getByText('v3')).toBeInTheDocument();
    expect(within(table).getByText('1.2 s')).toBeInTheDocument();
    expect(screen.getByText(fullDateTime('2026-08-02T03:04:00Z'))).toBeInTheDocument();
    expect(screen.getByText('共 1 次运行')).toBeInTheDocument();
  });

  it('删除：确认弹窗说明影响范围，确认后 DELETE 并刷新列表', async () => {
    let deleted = '';
    let listCalls = 0;
    server.use(
      http.get(listURL, () => {
        listCalls += 1;
        const items = listCalls === 1 ? [older, newer] : [newer];
        return HttpResponse.json({ items, total: items.length });
      }),
      http.delete(`${listURL}/11`, () => {
        deleted = '11';
        return HttpResponse.json({ deleted: 1 });
      }),
    );

    renderPage();
    await screen.findByText('旧画布');
    await userEvent.click(within(rowOf('旧画布')).getByRole('button', { name: '删除' }));

    expect(await screen.findByText('删除工作流')).toBeInTheDocument();
    expect(screen.getByText(/画布定义与历史运行记录会一并删除/)).toBeInTheDocument();
    // 行内也有「删除」按钮，确认动作必须限定在弹窗内
    const dialog = screen.getByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: '删除' }));

    await waitFor(() => expect(deleted).toBe('11'));
    await waitFor(() => expect(screen.queryByText('旧画布')).not.toBeInTheDocument());
  });
});
