// WorkflowRun 页面测试 — 运行详情是「顶部信息带 + 页级 Tabs（执行明细 / 画布）」：
//   顶部四张卡（状态 / 耗时 / 节点进度 / 触发）→ 执行明细 tab：左栏节点导航 + 右侧节点详情（人工确认 / 输入 / 输出）。
//   1. status=waiting 的人工输入门：填写后 POST resume（decision + values），成功后原地刷新
//   2. 节点详情：失败错误、输入变量、输出直接铺开
//   3. 运行中：事件到达后导航与详情立即可见
//   4. 运行记录不存在：给可恢复的空态，而不是空白页
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import WorkflowRunPage from './WorkflowRun';
import { subscribeRunEvents } from '@/api/workflows';
import { server } from '@/test/msw-server';
import { emptyNode } from '@/lib/workflowMeta';

// 事件流单独用假实现：jsdom 的 fetch 流在测试里并不稳定，
// 这里要验证的是「收到事件后详情写什么」，不是 SSE 分帧本身。
vi.mock('@/api/workflows', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/workflows')>()),
  subscribeRunEvents: vi.fn(),
}));

vi.mock('@/store/auth', () => ({
  useAuth: Object.assign(
    <T,>(selector: (s: { role: string }) => T): T => selector({ role: 'admin' }),
    { getState: () => ({ logout: () => {} }) },
  ),
  getToken: () => null,
}));

const runURL = '/api/v1/workflows/runs/run-1';
const workflowURL = '/api/v1/workflows/12';

function definition() {
  // 测试里的节点都给了 label：左栏导航按 label 展示，空 label 会退回 node_id
  const start = { ...emptyNode('start', 'start', { x: 0, y: 0 }), label: '开始' };
  start.variables = [{ name: 'question', type: 'string', description: '', required: true, default: null }];
  const agent = { ...emptyNode('agent', 'answer', { x: 300, y: 0 }), label: '回答告警' };
  agent.provider = 'openai';
  agent.model = 'gpt-4o';
  agent.instruction = '回答 {{question}}';
  const gate = { ...emptyNode('input', 'gate', { x: 600, y: 0 }), label: '补充结论' };
  gate.prompt = '请补充处理结论';
  gate.output_key = 'conclusion';
  const end = { ...emptyNode('end', 'end', { x: 900, y: 0 }), label: '结束' };
  return {
    nodes: [start, agent, gate, end],
    edges: [
      { id: 'e1', source: 'start', target: 'answer', route: '' },
      { id: 'e2', source: 'answer', target: 'gate', route: '' },
      { id: 'e3', source: 'gate', target: 'end', route: '' },
    ],
  };
}

function nodeRun(overrides: Record<string, unknown>) {
  return {
    id: 1,
    node_id: 'start',
    node_kind: 'start',
    attempt: 1,
    status: 'succeeded',
    input: '{"question":"磁盘告警"}',
    output: '{"question":"磁盘告警"}',
    error: '',
    duration_ms: 3,
    route: '',
    created_at: '2026-08-02T03:04:00Z',
    ...overrides,
  };
}

function runDetail(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    run_uid: 'run-1',
    workflow_id: 12,
    workflow_name: '磁盘告警处置',
    workflow_version: 3,
    workflow_status: 'published',
    has_published: true,
    status: 'waiting',
    trigger: 'manual',
    triggered_by: 'admin',
    input: '{"question":"磁盘告警"}',
    // waiting 时 output 是人工门信息
    output: '{"gate":"gate","kind":"input","prompt":"请补充处理结论"}',
    error: '',
    duration_ms: 2400,
    created_at: '2026-08-02T03:04:00Z',
    finished_at: null,
    nodes: [nodeRun({})],
    definition: definition(),
    ...overrides,
  };
}

function renderPage(uid = 'run-1') {
  return render(
    <MemoryRouter initialEntries={[`/workflows/runs/${uid}`]}>
      <Routes>
        <Route path="/workflows/runs/:runUid" element={<WorkflowRunPage />} />
        <Route path="/workflows/:id" element={<div>编排页占位</div>} />
        <Route path="/workflows" element={<div>工作流列表占位</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

/** 切换到另一个运行：模拟「再次运行」后的路由跳转。 */
function NavTo({ uid }: { uid: string }) {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate(`/workflows/runs/${uid}`)}>
      切到另一个运行
    </button>
  );
}

describe('WorkflowRunPage', () => {
  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
    server.use(
      http.get(workflowURL, () =>
        HttpResponse.json({
          id: 12,
          name: '磁盘告警处置',
          description: '',
          status: 'published',
          version: 3,
          validation_error: '',
          node_count: 4,
          edge_count: 3,
          warnings: [],
          last_run: null,
          created_at: '2026-06-01T00:00:00Z',
          updated_at: '2026-06-01T00:00:00Z',
          definition: definition(),
        }),
      ),
    );
  });

  it('人工输入门：提交内容随 resume 一起回传，成功后原地刷新', async () => {
    let resumeBody: Record<string, unknown> | null = null;
    server.use(
      http.get(runURL, () => HttpResponse.json(runDetail())),
      http.post(`${runURL}/resume`, async ({ request }) => {
        resumeBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          runDetail({
            status: 'succeeded',
            output: '"已确认"',
            finished_at: '2026-08-02T03:05:00Z',
          }),
        );
      }),
    );

    renderPage();

    // 顶部信息带给出结论：状态卡点名卡在哪个节点，不再有单独的横幅
    const overview = await screen.findByRole('region', { name: '运行概况' });
    expect(within(overview).getByText('等待人工填写')).toBeInTheDocument();
    expect(within(overview).getByText('停在「补充结论」，处理后继续')).toBeInTheDocument();
    // 中栏详情给出可操作的表单
    expect(screen.getByText('等待人工输入')).toBeInTheDocument();
    expect(screen.getByText('请补充处理结论')).toBeInTheDocument();
    expect(screen.getByText('conclusion')).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText('人工输入内容'), '已重启 exporter');
    await userEvent.click(screen.getByRole('button', { name: /提交并继续/ }));

    await waitFor(() => expect(resumeBody).not.toBeNull());
    // 提交值挂到人工节点的 output_key 上，decision 决定继续还是终止
    expect(resumeBody).toEqual({ decision: 'approve', values: { conclusion: '已重启 exporter' } });

    // 原地刷新：门消失，状态变成成功
    await waitFor(() => expect(screen.queryByText('等待人工输入')).not.toBeInTheDocument());
    expect(screen.getByText('已确认，继续执行')).toBeInTheDocument();
  });

  it('顶部信息带：状态 / 耗时 / 节点进度 / 触发只剩这一处', async () => {
    server.use(http.get(runURL, () => HttpResponse.json(runDetail())));

    renderPage();

    const overview = await screen.findByRole('region', { name: '运行概况' });
    expect(within(overview).getByText('当前状态')).toBeInTheDocument();
    expect(within(overview).getByText('2.4 s')).toBeInTheDocument();
    expect(within(overview).getByText('节点进度')).toBeInTheDocument();
    expect(within(overview).getByText('触发方式')).toBeInTheDocument();
    expect(within(overview).getByText('手动')).toBeInTheDocument();

    // 运行级输入输出不再展示：节点级已经覆盖排查需求，不再重复一份
    expect(screen.queryByRole('heading', { name: '运行概况' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '运行输入' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '运行输出' })).not.toBeInTheDocument();
  });

  it('输入与输出字段：字段名在外、复制按钮与字段同行', async () => {
    server.use(
      http.get(runURL, () =>
        HttpResponse.json(
          runDetail({
            status: 'succeeded',
            output: '"已确认"',
            finished_at: '2026-08-02T03:05:00Z',
          }),
        ),
      ),
    );

    renderPage();

    // 输入按变量逐个列，每个变量一块，字段名在内容框外面
    const copyInput = await screen.findByRole('button', { name: '复制 question' });
    expect(copyInput).toBeInTheDocument();

    // 输出是整个 JSON 文本，不按字段拆开：只有一块，字段名用工作流定义的输出变量名
    const copyOutput = screen.getByRole('button', { name: '复制 start' });
    expect(copyOutput).toBeInTheDocument();
    // 输出里不再出现按字段拆出来的复制按钮
    expect(screen.getAllByRole('button', { name: '复制 question' })).toHaveLength(1);
  });

  it('画布视图：提供去编排页的入口', async () => {
    server.use(http.get(runURL, () => HttpResponse.json(runDetail())));

    renderPage();

    const tabs = await screen.findByRole('tablist', { name: '运行详情视图' });
    await userEvent.click(within(tabs).getByRole('tab', { name: '画布' }));

    // 画布是只读诊断，要改画布得回编辑器
    const edit = await screen.findByRole('button', { name: /去编排/ });
    expect(edit).toBeInTheDocument();
  });

  it('页级视图：执行明细 / 画布 两个 tab 互斥', async () => {
    server.use(http.get(runURL, () => HttpResponse.json(runDetail())));

    renderPage();

    const tabs = await screen.findByRole('tablist', { name: '运行详情视图' });
    expect(within(tabs).getAllByRole('tab')).toHaveLength(2);

    // 默认停在「执行明细」：节点导航可见
    const nav = await screen.findByRole('navigation', { name: '节点列表' });
    expect(nav).toBeInTheDocument();

    // 切到「画布」：节点导航收起，画布独占剩余空间
    await userEvent.click(within(tabs).getByRole('tab', { name: '画布' }));
    await waitFor(() => expect(screen.queryByRole('navigation', { name: '节点列表' })).not.toBeInTheDocument());
  });

  it('左栏导航列出全部节点，未执行的节点只占位不可点', async () => {
    server.use(http.get(runURL, () => HttpResponse.json(runDetail())));

    renderPage();

    const nav = await screen.findByRole('navigation', { name: '节点列表' });
    const items = within(nav).getAllByRole('button');
    // 4 个定义节点：只有 start 执行过，其余 3 个是未执行的占位
    expect(items).toHaveLength(4);
    expect(within(nav).getByText('开始')).toBeInTheDocument();
    expect(within(nav).getByRole('button', { name: /开始/ })).toBeEnabled();
    // 未执行的节点只占位：能看到名字，但点不开
    expect(within(nav).getByRole('button', { name: /补充结论/ })).toBeDisabled();
    expect(within(nav).getByRole('button', { name: /结束/ })).toBeDisabled();
  });

  it('节点详情：输入按变量展示，输出默认收起可展开', async () => {
    server.use(
      http.get(runURL, () =>
        HttpResponse.json(
          runDetail({
            status: 'failed',
            output: '',
            error: '节点 http 返回 500',
            finished_at: '2026-08-02T03:04:30Z',
            nodes: [
              nodeRun({}),
              nodeRun({
                id: 2,
                node_id: 'answer',
                node_kind: 'agent',
                attempt: 2,
                status: 'failed',
                output: '定位到 exporter 故障',
                error: 'provider 超时',
                duration_ms: 1500,
              }),
            ],
          }),
        ),
      ),
    );

    renderPage();

    // 顶部条给出运行级错误
    expect(await screen.findByText('节点 http 返回 500')).toBeInTheDocument();

    // 默认选中最后一条执行记录（失败节点）
    await waitFor(() => expect(screen.getByText('provider 超时')).toBeInTheDocument());
    // 输入直接按变量行给出，不需要展开
    expect(screen.getAllByText('question').length).toBeGreaterThan(0);
    expect(screen.getAllByText('磁盘告警').length).toBeGreaterThan(0);
    // 重试过的节点标出轮次（attempt 2）
    expect(screen.getByText('第 2 轮')).toBeInTheDocument();

    // 输出直接铺开（三栏布局里中栏是主区域，缩起来反而要多点一次）
    expect(document.body.textContent).toContain('定位到 exporter 故障');
  });

  it('点左栏节点：中栏切到该节点的输入与输出', async () => {
    server.use(
      http.get(runURL, () =>
        HttpResponse.json(
          runDetail({
            status: 'succeeded',
            output: '"已确认"',
            finished_at: '2026-08-02T03:05:00Z',
            nodes: [
              nodeRun({}),
              nodeRun({
                id: 2,
                node_id: 'answer',
                node_kind: 'agent',
                status: 'succeeded',
                output: '已定位到 exporter 故障',
                duration_ms: 1200,
              }),
            ],
          }),
        ),
      ),
    );

    renderPage();

    const nav = await screen.findByRole('navigation', { name: '节点列表' });
    await userEvent.click(within(nav).getByRole('button', { name: /开始/ }));

    // 开始节点：输入 1 个变量
    await waitFor(() => expect(screen.getByText('1 个变量')).toBeInTheDocument());
    // 切到第二个节点：详情换成它的输出（输出直接铺开，不用展开）
    await userEvent.click(within(nav).getByRole('button', { name: /回答告警/ }));
    await waitFor(() => expect(screen.getByText('1 个变量')).toBeInTheDocument());
    expect(document.body.textContent).toContain('已定位到 exporter 故障');
    // 后续节点不在中栏重复列出：左栏已经能看到整条流程
    expect(screen.queryByText('这一步之后')).not.toBeInTheDocument();
  });

  it('运行中：节点详情随事件带出输入输出，不必等运行结束落库', async () => {
    // 一条只发两个事件的流：节点开始（带输入）→ 节点结束（带输入与输出）
    const frames = [
      { event: 'node_started', node_id: 'answer', node_kind: 'agent', label: '回答', attempt: 1, input: { question: '磁盘告警' } },
      {
        event: 'node_finished',
        node_id: 'answer',
        node_kind: 'agent',
        status: 'succeeded',
        input: { question: '磁盘告警' },
        output: '已定位到 exporter 故障',
        duration_ms: 1200,
      },
      { event: 'run_finished', run_uid: 'run-1', status: 'succeeded', error: '', output: '', duration_ms: 1300, gate: null, gate_kind: null },
    ] as const;
    vi.mocked(subscribeRunEvents).mockImplementation(async (_uid, onEvent, signal) => {
      for (const frame of frames) {
        if (signal?.aborted === true) return;
        onEvent(frame);
      }
    });
    server.use(
      http.get(runURL, () =>
        HttpResponse.json(runDetail({ status: 'running', output: '', finished_at: null, nodes: [] })),
      ),
    );

    renderPage();

    // 运行中库里还没有这条记录：导航行与详情完全来自事件
    await waitFor(() => expect(subscribeRunEvents).toHaveBeenCalled());
    const nav = await screen.findByRole('navigation', { name: '节点列表' });
    // 事件里的节点出现在导航中
    expect(within(nav).getByRole('button', { name: /回答告警/ })).toBeEnabled();

    // 点它：输入变量直接列出；输出随事件到达
    await userEvent.click(within(nav).getByRole('button', { name: /回答告警/ }));
    expect((await screen.findAllByText('question')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('磁盘告警').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/已定位到 exporter 故障/).length).toBeGreaterThan(0);
  });

  it('运行记录不存在：给出空态与返回入口', async () => {
    server.use(http.get(runURL, () => HttpResponse.json({ error: 'run not found' }, { status: 404 })));

    renderPage();

    expect(await screen.findByText('找不到这次运行')).toBeInTheDocument();
    expect(screen.getByText('run not found')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /返回运行记录/ })).toHaveAttribute('href', '/workflows?tab=runs');
  });

  it('顶部返回按钮回到运行记录列表', async () => {
    server.use(http.get(runURL, () => HttpResponse.json(runDetail())));

    renderPage();

    // 返回的是运行记录分页列表，不是工作流画布
    expect(await screen.findByRole('link', { name: /返回运行记录/ })).toHaveAttribute(
      'href',
      '/workflows?tab=runs',
    );
    // 详情页不给进编辑器的入口：要改画布从工作流列表进去
    expect(screen.queryByRole('link', { name: '磁盘告警处置' })).not.toBeInTheDocument();
  });

  it('换一次运行（run_uid 变化）：重新加载，不停留在上一次的数据', async () => {
    const otherURL = '/api/v1/workflows/runs/run-2';
    server.use(
      http.get(runURL, () => HttpResponse.json(runDetail())),
      http.get(otherURL, () =>
        HttpResponse.json(
          runDetail({
            run_uid: 'run-2',
            workflow_name: '另一个工作流',
            status: 'succeeded',
            output: '"第二个运行的输出"',
          }),
        ),
      ),
    );

    render(
      <MemoryRouter initialEntries={['/workflows/runs/run-1']}>
        <Routes>
          <Route path="/workflows/runs/:runUid" element={<WorkflowRunPage />} />
          <Route path="/workflows/:id" element={<div>编排页占位</div>} />
          <Route path="/workflows" element={<div>工作流列表占位</div>} />
        </Routes>
        <NavTo uid="run-2" />
      </MemoryRouter>,
    );

    expect(await screen.findByText('磁盘告警处置')).toBeInTheDocument();

    // 路由切到另一个运行：页面要跟着换，不能还显示上一次的内容
    await userEvent.click(screen.getByRole('button', { name: '切到另一个运行' }));

    expect(await screen.findByText('另一个工作流')).toBeInTheDocument();
    expect(screen.queryByText('磁盘告警处置')).not.toBeInTheDocument();
  });

  it('停止运行：后端说已经结束就如实提示，不谎报成功', async () => {
    server.use(
      http.get(runURL, () =>
        HttpResponse.json(runDetail({ status: 'running', finished_at: null, output: '' })),
      ),
      http.post(`${runURL}/stop`, () => HttpResponse.json({ stopped: false })),
    );

    renderPage();

    const stopButton = await screen.findByRole('button', { name: /停止运行/ });
    await userEvent.click(stopButton);

    expect(await screen.findByText('这次运行已经结束，无法停止')).toBeInTheDocument();
  });

  it('停止运行：后端确认停止后提示成功', async () => {
    server.use(
      http.get(runURL, () =>
        HttpResponse.json(runDetail({ status: 'running', finished_at: null, output: '' })),
      ),
      http.post(`${runURL}/stop`, () => HttpResponse.json({ stopped: true })),
    );

    renderPage();

    const stopButton = await screen.findByRole('button', { name: /停止运行/ });
    await userEvent.click(stopButton);

    expect(await screen.findByText('已停止运行')).toBeInTheDocument();
  });

  it('导航把已执行与未执行分成两段', async () => {
    server.use(http.get(runURL, () => HttpResponse.json(runDetail())));

    renderPage();

    const nav = await screen.findByRole('navigation', { name: '节点列表' });
    expect(within(nav).getByText('未执行 · 3')).toBeInTheDocument();
    // 未执行的节点点不开
    expect(within(nav).getByRole('button', { name: /补充结论/ })).toBeDisabled();
  });
});
