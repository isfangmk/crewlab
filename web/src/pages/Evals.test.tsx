// 评测页：列表、用例维护、发起真实运行、结果与基线对比的界面契约。
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import EvalsPage from './Evals';
import EvalDatasetPage from './EvalDataset';
import { server } from '@/test/msw-server';
import { selectOption } from '@/test/select-option';

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
  baseline_run_uid: '',
  gate_max_regressions: 0,
  gate_min_pass_rate: 0,
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
    kind: 'normal',
    attack_type: '',
    safety_keywords: [],
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
    kind: 'redteam',
    attack_type: 'jailbreak',
    safety_keywords: ['DAN已激活'],
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
    baseline_run_uid: '',
    gate_max_regressions: 0,
    gate_min_pass_rate: 0,
    created_at: '2026-11-14T01:00:00Z',
    updated_at: '2026-11-14T02:00:00Z',
    cases,
    runs: [run],
    ...overrides,
  };
}

// 结果行夹具：judge / 标注 / 导出用例共用
const resultOne = {
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
};

const resultTwo = {
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
};

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

// 评分配置读写：运行弹窗会拉取配置并在发起时保存
function mockJudgeConfig(overrides: Record<string, unknown> = {}) {
  server.use(
    http.get('/api/v1/evals/datasets/1/judge-config', () =>
      HttpResponse.json({
        dataset_id: 1,
        enabled: false,
        rubric: '',
        judge_provider: '',
        judge_model: '',
        pass_score: 3,
        updated_at: null,
        ...overrides,
      }),
    ),
    http.put('/api/v1/evals/datasets/1/judge-config', () =>
      HttpResponse.json({
        dataset_id: 1,
        enabled: false,
        rubric: '',
        judge_provider: '',
        judge_model: '',
        pass_score: 3,
        updated_at: null,
        ...overrides,
      }),
    ),
  );
}

// 逐结果 judge 分：结果弹窗打开时批量拉取
function mockJudgeScores(scoresById: Record<string, unknown[]>) {
  server.use(
    ...Object.entries(scoresById).map(([resultId, items]) =>
      http.get(`/api/v1/evals/runs/run-1/results/${resultId}/judge-scores`, () =>
        HttpResponse.json({ items, total: items.length }),
      ),
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

  it('配置了门禁基线的数据集显示门禁徽章', async () => {
    server.use(
      http.get('/api/v1/evals/datasets', () =>
        HttpResponse.json({
          items: [
            {
              ...datasetSummary,
              baseline_run_uid: 'run-1',
              gate_max_regressions: 1,
              gate_min_pass_rate: 0.8,
            },
          ],
          total: 1,
        }),
      ),
    );
    render(
      <MemoryRouter initialEntries={['/evals']}>
        <Routes>
          <Route path="/evals" element={<EvalsPage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(
      await screen.findByText('门禁基线 · 回归≤1 · 通过率≥80%'),
    ).toBeInTheDocument();
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
    mockJudgeConfig();
    mockJudgeScores({ 'res-1': [], 'res-2': [] });
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
    // 运行记录在 Tabs 的第二屏，先切过去再点行内对比
    await userEvent.click(screen.getByRole('tab', { name: '运行记录' }));
    const compareButtons = screen.getAllByRole('button', { name: '与基线对比' });
    await userEvent.click(compareButtons[0]);
    const dialog = await screen.findByRole('dialog');
    expect((await within(dialog).findAllByText('改善')).length).toBeGreaterThan(0);
    expect(within(dialog).getByText('0 → 1')).toBeInTheDocument();
    expect(within(dialog).getByText('命中关键词')).toBeInTheDocument();
  });

  it('导入对抗用例按钮调用种子接口并提示结果', async () => {
    let seeded = 0;
    server.use(
      http.post('/api/v1/evals/datasets/1/redteam/seed', () => {
        seeded += 1;
        return HttpResponse.json({ created: 3, items: [] });
      }),
    );
    renderDetail();
    await screen.findByText('命中关键词');
    await userEvent.click(
      screen.getByRole('button', { name: /导入对抗用例/ }),
    );
    await waitFor(() => expect(seeded).toBe(1));
    expect(await screen.findByText('已导入 3 条对抗用例')).toBeInTheDocument();
  });

  it('门禁 Tab 保存阈值配置并执行门禁判定', async () => {
    let patched: unknown = null;
    server.use(
      http.get('/api/v1/evals/datasets/1', () =>
        HttpResponse.json(
          detail({ baseline_run_uid: 'run-1', gate_max_regressions: 0, gate_min_pass_rate: 0.5 }),
        ),
      ),
      http.patch('/api/v1/evals/datasets/1', async ({ request }) => {
        patched = await request.json();
        return HttpResponse.json({ ...datasetSummary, ...(patched as object) });
      }),
      http.post('/api/v1/evals/runs/run-1/gate', () =>
        HttpResponse.json({
          run_uid: 'run-1',
          baseline_run_uid: 'run-1',
          pass: false,
          pass_rate: 0.5,
          regressions: 1,
          improved: 0,
          max_regressions: 0,
          min_pass_rate: 0.5,
          reasons: ['回归用例 1 条，超过允许上限 0 条'],
          regressed_cases: [
            {
              case_id: 11,
              case_name: '命中关键词',
              baseline_status: 'passed',
              current_status: 'failed',
              change: 'regressed',
              baseline_reasons: [],
              current_reasons: ['缺少期望关键词: 收到'],
              baseline_duration_ms: 900,
              current_duration_ms: 900,
              baseline_total_tokens: 12,
              current_total_tokens: 12,
            },
          ],
        }),
      ),
    );
    renderDetail();
    await screen.findByText('命中关键词');
    await userEvent.click(screen.getByRole('tab', { name: '门禁' }));
    expect(await screen.findByText('门禁配置')).toBeInTheDocument();

    await userEvent.clear(screen.getByLabelText('允许回归数上限'));
    await userEvent.type(screen.getByLabelText('允许回归数上限'), '2');
    await userEvent.click(screen.getByRole('button', { name: '保存配置' }));
    await waitFor(() =>
      expect(patched).toMatchObject({
        baseline_run_uid: 'run-1',
        gate_max_regressions: 2,
        gate_min_pass_rate: 0.5,
      }),
    );

    await userEvent.click(screen.getByRole('button', { name: /执行门禁/ }));
    expect(await screen.findByText('门禁未通过')).toBeInTheDocument();
    expect(screen.getByText('回归用例 1 条，超过允许上限 0 条')).toBeInTheDocument();
    expect(screen.getAllByText('命中关键词').length).toBeGreaterThan(0);
  });

  it('标注队列认领后提交人工判定', async () => {
    let posted: unknown = null;
    server.use(
      http.get('/api/v1/evals/runs/run-1/annotations', () =>
        HttpResponse.json({
          items: [],
          total: 0,
          consistency: {
            results_total: 2,
            annotated_results: 0,
            multi_annotated: 0,
            agreement_rate: null,
            label_counts: { good: 0, needs_work: 0, bad: 0 },
          },
        }),
      ),
      http.get('/api/v1/evals/annotations/queue', () =>
        HttpResponse.json({
          result: {
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
          annotation_count: 0,
        }),
      ),
      http.post('/api/v1/evals/annotations', async ({ request }) => {
        posted = await request.json();
        return HttpResponse.json({
          id: 'ann-1',
          result_id: 'res-1',
          annotator_user_id: 1,
          label: 'good',
          comment: '判定正确',
          created_at: '2026-11-14T03:00:00Z',
        });
      }),
    );
    renderDetail();
    await screen.findByText('命中关键词');
    await userEvent.click(screen.getByRole('tab', { name: '标注队列' }));
    expect(await screen.findByText('一致率')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /认领下一条/ }));

    const dialog = await screen.findByRole('dialog', { name: '标注评测结果' });
    await selectOption(within(dialog).getByRole('combobox', { name: '判定' }), '好');
    await userEvent.type(within(dialog).getByLabelText('备注'), '判定正确');
    await userEvent.click(within(dialog).getByRole('button', { name: '提交标注' }));

    await waitFor(() =>
      expect(posted).toMatchObject({ result_id: 'res-1', label: 'good', comment: '判定正确' }),
    );
    expect(await screen.findByText('标注已提交')).toBeInTheDocument();
  });

  it('结果弹窗展示 judge 平均分与阈值徽章，可展开维度理由', async () => {
    mockJudgeConfig({ enabled: true, pass_score: 3 });
    mockJudgeScores({
      'res-1': [
        {
          id: 'j1',
          result_id: 'res-1',
          dimension: '正确性',
          score: 4,
          reason: '回答准确',
          judge_model: 'deepseek/deepseek-flash',
          created_at: '2026-11-14T02:01:00Z',
        },
      ],
      'res-2': [
        {
          id: 'j2',
          result_id: 'res-2',
          dimension: 'parse',
          score: 0,
          reason: '评分失败: ValueError',
          judge_model: 'parse_error',
          created_at: '2026-11-14T02:01:00Z',
        },
      ],
    });
    server.use(
      http.get('/api/v1/evals/runs/run-1', () =>
        HttpResponse.json({ ...run, cases_snapshot: [], results: [resultOne, resultTwo] }),
      ),
    );
    renderDetail();
    await screen.findByText('命中关键词');
    await userEvent.click(screen.getByRole('tab', { name: '运行记录' }));
    await userEvent.click(screen.getByRole('button', { name: '查看结果' }));

    const dialog = await screen.findByRole('dialog', { name: '评测结果' });
    expect(await within(dialog).findByText('4.0')).toBeInTheDocument();
    expect(within(dialog).getByText('过线')).toBeInTheDocument();
    expect(within(dialog).getByText('评分解析失败')).toBeInTheDocument();

    await userEvent.click(within(dialog).getAllByRole('button', { name: '评分详情' })[0]);
    expect(await within(dialog).findByText('正确性')).toBeInTheDocument();
    expect(within(dialog).getByText('回答准确')).toBeInTheDocument();
  });

  it('结果弹窗导出 CSV 触发文件下载', async () => {
    const exportReqs: URLSearchParams[] = [];
    server.use(
      http.get('/api/v1/evals/runs/run-1', () =>
        HttpResponse.json({ ...run, cases_snapshot: [], results: [resultOne, resultTwo] }),
      ),
      http.get('/api/v1/evals/runs/run-1/export', ({ request }) => {
        exportReqs.push(new URL(request.url).searchParams);
        return new HttpResponse('case_name,status\n命中关键词,passed', {
          headers: {
            'content-type': 'text/csv; charset=utf-8',
            'content-disposition': 'attachment; filename="eval-run-1.csv"',
          },
        });
      }),
    );
    // jsdom 没有 blob URL 实现，这里只验证导出链路被触发
    const createObjectURL = vi.fn(() => 'blob:eval');
    const revokeObjectURL = vi.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });

    renderDetail();
    await screen.findByText('命中关键词');
    await userEvent.click(screen.getByRole('tab', { name: '运行记录' }));
    await userEvent.click(screen.getByRole('button', { name: '查看结果' }));
    const dialog = await screen.findByRole('dialog', { name: '评测结果' });
    await userEvent.click(within(dialog).getByRole('button', { name: '导出 CSV' }));

    await waitFor(() => expect(exportReqs).toHaveLength(1));
    expect(exportReqs[0].get('format')).toBe('csv');
  });
});
