// 评测报告只读分享页：免登录渲染报告区块，坏 token 显式报错。
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it } from 'vitest';

import EvalShareViewPage from './EvalShareView';
import { server } from '@/test/msw-server';

const report = {
  expires_at: '2026-11-15T02:00:00Z',
  dataset: {
    id: 1,
    name: '运维回归',
    description: '',
    baseline_run_uid: 'run-0',
    gate_max_regressions: 0,
    gate_min_pass_rate: 0.8,
    created_at: '2026-11-14T01:00:00Z',
    updated_at: '2026-11-14T02:00:00Z',
  },
  run: {
    run_uid: 'run-1',
    dataset_id: 1,
    dataset_name: '运维回归',
    agent_id: 'default',
    provider: 'deepseek',
    model: 'deepseek-flash',
    status: 'completed',
    total_cases: 2,
    passed_cases: 1,
    failed_cases: 1,
    error: '',
    started_at: '2026-11-14T02:00:00Z',
    finished_at: '2026-11-14T02:00:20Z',
  },
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
      judge_scores: [
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
      judge_scores: [],
    },
  ],
  gate: {
    run_uid: 'run-1',
    baseline_run_uid: 'run-0',
    pass: false,
    pass_rate: 0.5,
    regressions: 1,
    improved: 0,
    max_regressions: 0,
    min_pass_rate: 0.8,
    reasons: ['回归用例 1 条超过上限 0'],
    regressed_cases: [
      {
        case_id: 12,
        case_name: '停用用例',
        baseline_status: 'passed',
        current_status: 'failed',
        change: 'regressed',
        baseline_reasons: [],
        current_reasons: ['缺少期望关键词: 快照'],
        baseline_duration_ms: 800,
        current_duration_ms: 1200,
        baseline_total_tokens: 10,
        current_total_tokens: 21,
      },
    ],
  },
  consistency: {
    results_total: 2,
    annotated_results: 1,
    multi_annotated: 0,
    agreement_rate: null,
    label_counts: { good: 1, needs_work: 0, bad: 0 },
  },
};

function renderShare() {
  render(
    <MemoryRouter initialEntries={['/eval-share/abc']}>
      <Routes>
        <Route path="/eval-share/:token" element={<EvalShareViewPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.setItem('ongrid-locale', 'zh-CN');
});

describe('评测报告分享页', () => {
  it('渲染概要、门禁结论、结果明细与标注一致性', async () => {
    server.use(
      http.get('/api/v1/evals/shares/abc', () => HttpResponse.json(report)),
    );
    renderShare();

    expect(await screen.findByText('运维回归')).toBeInTheDocument();
    expect(screen.getByText('run-1')).toBeInTheDocument();
    // 概要指标：通过率 1/2
    expect(screen.getByText('50%')).toBeInTheDocument();
    // 门禁结论：未通过 + 原因 + 回归用例（限定区块内断言，避免与结果表徽章撞文本）
    const gateSection = screen.getByText('门禁结论').closest('section')!;
    expect(within(gateSection).getByText('未通过')).toBeInTheDocument();
    expect(within(gateSection).getByText('回归用例 1 条超过上限 0')).toBeInTheDocument();
    // 结果明细：用例与评分
    expect(screen.getByText('命中关键词')).toBeInTheDocument();
    expect(screen.getByText('4.0')).toBeInTheDocument();
    expect(screen.getByText('缺少期望关键词: 快照')).toBeInTheDocument();
    // 标注一致性
    expect(screen.getByText('已标注 1/2 条')).toBeInTheDocument();
  });

  it('无门禁与标注时省略对应区块', async () => {
    server.use(
      http.get('/api/v1/evals/shares/abc', () =>
        HttpResponse.json({ ...report, gate: null, consistency: null }),
      ),
    );
    renderShare();
    expect(await screen.findByText('命中关键词')).toBeInTheDocument();
    expect(screen.queryByText('门禁结论')).not.toBeInTheDocument();
    expect(screen.queryByText('人工标注一致性')).not.toBeInTheDocument();
  });

  it('链接无效或已过期时展示错误', async () => {
    server.use(
      http.get('/api/v1/evals/shares/abc', () =>
        HttpResponse.json({ detail: '分享令牌已过期' }, { status: 404 }),
      ),
    );
    renderShare();
    expect(await screen.findByText(/报告加载失败/)).toBeInTheDocument();
  });
});
