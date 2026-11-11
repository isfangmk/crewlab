// WorkflowRun 页面测试 — 运行详情是一次运行的完整诊断视图：
//   1. status=waiting 的人工输入门：填写后 POST resume（decision + values），成功后原地刷新
//   2. 执行时间线：节点状态、失败错误、输出可展开
//   3. 运行记录不存在：给可恢复的空态，而不是空白页
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import WorkflowRunPage from './WorkflowRun';
import { server } from '@/test/msw-server';
import { emptyNode } from '@/lib/workflowMeta';

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
  const start = emptyNode('start', 'start', { x: 0, y: 0 });
  start.variables = [{ name: 'question', type: 'string', description: '', required: true, default: null }];
  const agent = emptyNode('agent', 'answer', { x: 300, y: 0 });
  agent.provider = 'openai';
  agent.model = 'gpt-4o';
  agent.instruction = '回答 {{question}}';
  const gate = emptyNode('input', 'gate', { x: 600, y: 0 });
  gate.prompt = '请补充处理结论';
  gate.output_key = 'conclusion';
  const end = emptyNode('end', 'end', { x: 900, y: 0 });
  return {
    nodes: [start, agent, gate, end],
    edges: [
      { id: 'e1', source: 'start', target: 'answer', route: '' },
      { id: 'e2', source: 'answer', target: 'gate', route: '' },
      { id: 'e3', source: 'gate', target: 'end', route: '' },
    ],
  };
}

function runDetail(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    run_uid: 'run-1',
    workflow_id: 12,
    workflow_name: '磁盘告警处置',
    workflow_version: 3,
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
    nodes: [
      {
        id: 1,
        node_id: 'start',
        node_kind: 'start',
        attempt: 1,
        status: 'succeeded',
        output: '{"question":"磁盘告警"}',
        error: '',
        duration_ms: 3,
        route: '',
        created_at: '2026-08-02T03:04:00Z',
      },
    ],
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

    // 等待人工输入：阻塞在人工节点上，页面给出可操作的卡片
    expect(await screen.findByText('等待人工输入')).toBeInTheDocument();
    expect(screen.getByText('请补充处理结论')).toBeInTheDocument();
    expect(screen.getByText('conclusion')).toBeInTheDocument();
    expect(screen.getByText('运行停在人工节点，确认后继续')).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText('人工输入内容'), '已重启 exporter');
    await userEvent.click(screen.getByRole('button', { name: /提交并继续/ }));

    await waitFor(() => expect(resumeBody).not.toBeNull());
    // 提交值挂到人工节点的 output_key 上，decision 决定继续还是终止
    expect(resumeBody).toEqual({ decision: 'approve', values: { conclusion: '已重启 exporter' } });

    // 原地刷新：门消失，状态变成成功
    await waitFor(() => expect(screen.queryByText('等待人工输入')).not.toBeInTheDocument());
    expect(screen.getAllByText('成功').length).toBeGreaterThan(0);
    expect(screen.getByText('已确认，继续执行')).toBeInTheDocument();
  });

  it('时间线：展示节点状态与错误，输出默认收起可展开', async () => {
    server.use(
      http.get(runURL, () =>
        HttpResponse.json(
          runDetail({
            status: 'failed',
            output: '',
            error: '节点 http 返回 500',
            finished_at: '2026-08-02T03:04:30Z',
            nodes: [
              {
                id: 1,
                node_id: 'start',
                node_kind: 'start',
                attempt: 1,
                status: 'succeeded',
                output: '{"question":"磁盘告警"}',
                error: '',
                duration_ms: 3,
                route: '',
                created_at: '2026-08-02T03:04:00Z',
              },
              {
                id: 2,
                node_id: 'answer',
                node_kind: 'agent',
                attempt: 2,
                status: 'failed',
                output: '',
                error: 'provider 超时',
                duration_ms: 1500,
                route: '',
                created_at: '2026-08-02T03:04:02Z',
              },
            ],
          }),
        ),
      ),
    );

    renderPage();

    // 失败态：顶部状态胶囊 + 错误卡片 + 节点级错误
    expect((await screen.findAllByText('失败')).length).toBeGreaterThan(0);
    expect(screen.getByText('运行失败，见下方错误输出')).toBeInTheDocument();
    expect(screen.getByText('provider 超时')).toBeInTheDocument();
    expect(screen.getByText('节点 http 返回 500')).toBeInTheDocument();
    // 重试过的节点标出轮次（attempt 2）
    expect(screen.getByText('第 2 轮')).toBeInTheDocument();

    // 输出默认收起，点开后展示完整文本（JSON 会格式化，不比较整串）
    expect(screen.queryByText('收起输出')).not.toBeInTheDocument();
    await userEvent.click(screen.getAllByRole('button', { name: /展开输出/ })[0]);
    expect(await screen.findByRole('button', { name: /收起输出/ })).toBeInTheDocument();
    expect(document.body.textContent).toContain('"question": "磁盘告警"');
  });

  it('运行记录不存在：给出空态与返回入口', async () => {
    server.use(
      http.get(runURL, () => HttpResponse.json({ error: 'run not found' }, { status: 404 })),
    );

    renderPage();

    expect(await screen.findByText('找不到这次运行')).toBeInTheDocument();
    expect(screen.getByText('run not found')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /返回运行记录/ })).toHaveAttribute('href', '/workflows?tab=runs');
  });
});
