// 评测页：列表、用例维护、发起真实运行、结果与基线对比的界面契约。
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import EvalsPage from './Evals';
import EvalDatasetPage from './EvalDataset';
import { server } from '@/test/msw-server';

vi.mock('@/store/auth', () => ({
  useAuth: Object.assign(
    <T,>(selector: (s: { role: string }) => T): T => selector({ role: 'admin' }),
    { getState: () => ({ logout: () => {} }) },
  ),
  getToken: () => null,
}));

const run = {
  run_uid: 'run-1',
  dataset_id: 1,
  dataset_name: '运维回归',
  agent_id: 'default',
  provider: 'deepseek',
  model: 'deepseek-flash',
  status: 'completed' as const,
  total_cases: 2,
  passed_cases: 1,
  failed_cases: 1,
  error: '',
  started_at: '2026-11-14T02:00:00Z',
  finished_at: '2026-11-14T02:00:20Z',
};

const datasetSummary = {
  id: 1,
  name: '运维回归',
  description: '改人设或知识范围后先跑这组',
  case_count: 2,
  enabled_case_count: 2,
  created_at: '2026-11-14T01:00:00Z',
  updated_at: '2026-11-14T02:00:00Z',
  last_run: run,
};

const cases = [
  {
    id: 11,
    dataset_id: 1,
    name: '命中关键词',
    input: '只回复：收到',
    expected_keywords: ['收到'],
    forbidden_keywords: [],
    expected_tools: [],
    forbidden_tools: [],
    max_duration_ms: 60000,
    max_total_tokens: 4000,
    enabled: true,
    sort_order: 10,
    created_at: '2026-11-14T01:00:00Z',
    updated_at: '2026-11-14T01:00:00Z',
  },
  {
    id: 12,
    dataset_id: 1,
    name: '停用用例',
    input: '解释备份策略',
    expected_keywords: [],
    forbidden_keywords: ['删除'],
    expected_tools: ['query_knowledge'],
    forbidden_tools: [],
    max_duration_ms: null,
    max_total_tokens: null,
    enabled: false,
    sort_order: 20,
    created_at: '2026-11-14T01:00:00Z',
    updated_at: '2026-11-14T01:00:00Z',
  },
];

function detail(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    name: '运维回归',
    description: '改人设或知识范围后先跑这组',
    created_at: '2026-11-14T01:00:00Z',
    updated_at: '2026-11-14T02:00:00Z',
    cases,
    runs: [run],
    ...overrides,
  };
}

function renderDetail() {
  render(
    <MemoryRouter initialEntries={['/evals/1']}>
      <Routes>
        <Route path="/evals/:datasetId" element={<EvalDatasetPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function mockAgentAndModels() {
  server.use(
    http.get('/api/v1/agents', () =>
      HttpResponse.json({
        items: [
          {
            name: 'default',
            description: '默认助理',
            when_to_use: '',
            tools: [],
            disallowed_tools: [],
            permission_mode: '',
            model: '',
            max_turns: 0,
            system_prompt: '',
            critical_reminder: '',
            output_format: 'free',
            examples: [],
            knowledge_scope: { paths: [], doc_ids: [] },
            skills: null,
            sub_agents: [],
            source: 'builtin',
          },
        ],
        total: 1,
      }),
    ),
    http.get('/api/v1/aiops/models', () =>
      HttpResponse.json({
        providers: [
          {
            id: 'deepseek',
            label: 'DeepSeek',
            models: ['deepseek-flash'],
            model: 'deepseek-flash',
          },
        ],
        default: { provider: 'deepseek', model: 'deepseek-flash' },
      }),
    ),
  );
}

describe('评测页面', () => {
  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
    server.use(
      http.get('/api/v1/evals/datasets', () =>
        HttpResponse.json({ items: [datasetSummary], total: 1 }),
      ),
      http.get('/api/v1/evals/datasets/1', () => HttpResponse.json(detail())),
    );
  });

  it('列表展示最近运行、用例计数，并支持名称筛选', async () => {
    render(
      <MemoryRouter initialEntries={['/evals']}>
        <Routes>
          <Route path="/evals" element={<EvalsPage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText('运维回归')).toBeInTheDocument();
    expect(screen.getByText('已完成')).toBeInTheDocument();
    expect(screen.getByText('1/2')).toBeInTheDocument();
    expect(screen.getByText('2 条 · 2 启用')).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText('搜索评测集'), '不存在');
    expect(await screen.findByText('没有匹配的评测集')).toBeInTheDocument();
  });

  it('按更新时间排序评测集', async () => {
    const olderDataset = {
      ...datasetSummary,
      id: 2,
      name: '旧评测集',
      updated_at: '2026-11-13T01:00:00Z',
      last_run: null,
    };
    server.use(
      http.get('/api/v1/evals/datasets', () =>
        HttpResponse.json({ items: [olderDataset, datasetSummary], total: 2 }),
      ),
    );
    render(
      <MemoryRouter initialEntries={['/evals']}>
        <Routes>
          <Route path="/evals" element={<EvalsPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText('运维回归');
    const header = screen.getByRole('columnheader', { name: '更新时间' });
    const rows = () => screen.getAllByRole('row').slice(1);
    // 默认最新在前
    expect(within(rows()[0]).getByText('运维回归')).toBeInTheDocument();

    await userEvent.click(within(header).getByRole('button', { name: '更新时间' }));
    await waitFor(() =>
      expect(within(rows()[0]).getByText('旧评测集')).toBeInTheDocument(),
    );
  });

  it('创建评测集后进入详情页', async () => {
    let posted: unknown = null;
    server.use(
      http.post('/api/v1/evals/datasets', async ({ request }) => {
        posted = await request.json();
        return HttpResponse.json({
          id: 2,
          name: '客服黄金问答',
          description: '首响',
          created_at: null,
          updated_at: null,
        });
      }),
    );
    render(
      <MemoryRouter initialEntries={['/evals']}>
        <Routes>
          <Route path="/evals" element={<EvalsPage />} />
          <Route path="/evals/:datasetId" element={<div>详情占位</div>} />
        </Routes>
      </MemoryRouter>,
    );
    await screen.findByText('运维回归');
    await userEvent.click(screen.getByRole('button', { name: /新建评测集/ }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.type(within(dialog).getByLabelText('名称'), '客服黄金问答');
    await userEvent.type(within(dialog).getByLabelText('说明'), '首响');
    await userEvent.click(within(dialog).getByRole('button', { name: '创建' }));

    await waitFor(() =>
      expect(posted).toMatchObject({
        name: '客服黄金问答',
        description: '首响',
      }),
    );
    expect(await screen.findByText('详情占位')).toBeInTheDocument();
  });

  it('详情页展示用例断言与运行记录', async () => {
    renderDetail();
    expect(await screen.findByText('命中关键词')).toBeInTheDocument();
    expect(screen.getByText('含「收到」')).toBeInTheDocument();
    expect(screen.getByText('≤ 60000ms')).toBeInTheDocument();
    expect(screen.getByText('≤ 4000 tokens')).toBeInTheDocument();
    expect(screen.getByText('不含「删除」')).toBeInTheDocument();
    expect(screen.getByText('调用 query_knowledge')).toBeInTheDocument();
    expect(screen.getByText('启用')).toBeInTheDocument();
    expect(screen.getByText('停用')).toBeInTheDocument();
    expect(screen.getByText('1/2')).toBeInTheDocument();
  });

  it('新增用例提交归一化后的断言列表', async () => {
    let posted: unknown = null;
    server.use(
      http.post('/api/v1/evals/datasets/1/cases', async ({ request }) => {
        posted = await request.json();
        return HttpResponse.json({
          ...cases[0],
          id: 13,
          name: '新增用例',
        });
      }),
    );
    renderDetail();
    await screen.findByText('命中关键词');
    await userEvent.click(screen.getByRole('button', { name: /新增用例/ }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.type(within(dialog).getByLabelText('用例名称'), '新增用例');
    await userEvent.type(within(dialog).getByLabelText('输入'), '只回复：收到');
    await userEvent.type(
      within(dialog).getByLabelText('期望关键词'),
      '收到\nok',
    );
    await userEvent.click(within(dialog).getByRole('button', { name: '保存用例' }));

    await waitFor(() =>
      expect(posted).toMatchObject({
        name: '新增用例',
        input: '只回复：收到',
        expected_keywords: ['收到', 'ok'],
        enabled: true,
      }),
    );
    expect(await screen.findByText('用例已保存')).toBeInTheDocument();
  });

  it('发起运行后轮询结果，展示通过/失败与 trace 入口', async () => {
    let posted: unknown = null;
    mockAgentAndModels();
    server.use(
      http.post('/api/v1/evals/datasets/1/runs', async ({ request }) => {
        posted = await request.json();
        return HttpResponse.json({ ...run, status: 'running', passed_cases: 0, failed_cases: 0 });
      }),
      http.get('/api/v1/evals/runs/run-1', () =>
        HttpResponse.json({
          ...run,
          cases_snapshot: [],
          results: [
            {
              id: 'res-1',
              case_id: 11,
              case_name: '命中关键词',
              input: '只回复：收到',
              status: 'passed',
              output: '收到',
              failure_reasons: [],
              tool_names: [],
              trace_id: 'trace-1',
              session_id: 'sess-1',
              duration_ms: 900,
              total_tokens: 12,
              created_at: '2026-11-14T02:00:10Z',
            },
            {
              id: 'res-2',
              case_id: 12,
              case_name: '停用用例',
              input: '解释备份策略',
              status: 'failed',
              output: '备份策略',
              failure_reasons: ['缺少期望关键词: 快照'],
              tool_names: ['query_knowledge'],
              trace_id: 'trace-2',
              session_id: 'sess-2',
              duration_ms: 1200,
              total_tokens: 21,
              created_at: '2026-11-14T02:00:20Z',
            },
          ],
        }),
      ),
    );
    renderDetail();
    await screen.findByText('命中关键词');
    await userEvent.click(screen.getByRole('button', { name: /运行评测/ }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText(/默认助理/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: '开始运行' }));

    await waitFor(() => expect(posted).toEqual({ agent_id: 'default', provider: '', model: '' }));
    const resultDialog = await screen.findByRole('dialog');
    expect(await within(resultDialog).findByText('评测结果')).toBeInTheDocument();
    expect(await within(resultDialog).findByText('缺少期望关键词: 快照')).toBeInTheDocument();
    expect(within(resultDialog).getByText('收到')).toBeInTheDocument();
    expect(within(resultDialog).getAllByRole('link', { name: '查看 trace' })).toHaveLength(2);
  });

  it('选择基线后展示改善/回退汇总', async () => {
    const baseline = { ...run, run_uid: 'run-0', passed_cases: 0, failed_cases: 2 };
    server.use(
      http.get('/api/v1/evals/datasets/1', () =>
        HttpResponse.json(detail({ runs: [run, baseline] })),
      ),
      http.get('/api/v1/evals/runs/run-1/compare', () =>
        HttpResponse.json({
          current: run,
          baseline,
          summary: {
            improved: 1,
            regressed: 0,
            unchanged: 1,
            baseline_passed: 0,
            current_passed: 1,
            baseline_total_tokens: 30,
            current_total_tokens: 33,
          },
          cases: [
            {
              case_id: 11,
              case_name: '命中关键词',
              baseline_status: 'failed',
              current_status: 'passed',
              change: 'improved',
              baseline_reasons: ['缺少期望关键词: 收到'],
              current_reasons: [],
              baseline_duration_ms: 1000,
              current_duration_ms: 900,
              baseline_total_tokens: 10,
              current_total_tokens: 12,
            },
          ],
        }),
      ),
    );
    renderDetail();
    await screen.findByText('命中关键词');
    const compareButtons = screen.getAllByRole('button', { name: '与基线对比' });
    await userEvent.click(compareButtons[0]);
    const dialog = await screen.findByRole('dialog');
    expect((await within(dialog).findAllByText('改善')).length).toBeGreaterThan(0);
    expect(within(dialog).getByText('0 → 1')).toBeInTheDocument();
    expect(within(dialog).getByText('命中关键词')).toBeInTheDocument();
  });
});
