// 评测集与评测运行：真实会话跑分，结果保留 trace / 会话关联。
import { request } from './client';

export type EvalRunStatus = 'running' | 'completed' | 'failed';
export type EvalCaseStatus = 'passed' | 'failed' | 'error';

export type EvalRun = {
  run_uid: string;
  dataset_id: number;
  dataset_name: string;
  agent_id: string;
  provider: string;
  model: string;
  status: EvalRunStatus;
  total_cases: number;
  passed_cases: number;
  failed_cases: number;
  error: string;
  started_at: string | null;
  finished_at: string | null;
};

export type EvalDataset = {
  id: number;
  name: string;
  description: string;
  created_at: string | null;
  updated_at: string | null;
};

export type EvalDatasetSummary = EvalDataset & {
  case_count: number;
  enabled_case_count: number;
  last_run: EvalRun | null;
};

export type EvalCase = {
  id: number;
  dataset_id: number;
  name: string;
  input: string;
  expected_keywords: string[];
  forbidden_keywords: string[];
  expected_tools: string[];
  forbidden_tools: string[];
  max_duration_ms: number | null;
  max_total_tokens: number | null;
  enabled: boolean;
  sort_order: number;
  created_at: string | null;
  updated_at: string | null;
};

export type EvalDatasetDetail = EvalDataset & {
  cases: EvalCase[];
  runs: EvalRun[];
};

export type EvalCaseResult = {
  id: string;
  case_id: number | null;
  case_name: string;
  input: string;
  status: EvalCaseStatus;
  output: string;
  failure_reasons: string[];
  tool_names: string[];
  trace_id: string;
  session_id: string;
  duration_ms: number | null;
  total_tokens: number | null;
  created_at: string | null;
};

export type EvalCaseSnapshot = {
  case_id: number | null;
  name: string;
  input: string;
  expected_keywords: string[];
  forbidden_keywords: string[];
  expected_tools: string[];
  forbidden_tools: string[];
  max_duration_ms: number | null;
  max_total_tokens: number | null;
};

export type EvalRunDetail = EvalRun & {
  cases_snapshot: EvalCaseSnapshot[];
  results: EvalCaseResult[];
};

export type EvalCompareRow = {
  case_id: number | null;
  case_name: string;
  baseline_status: EvalCaseStatus | 'missing';
  current_status: EvalCaseStatus | 'missing';
  change: 'improved' | 'regressed' | 'same';
  baseline_reasons: string[];
  current_reasons: string[];
  baseline_duration_ms: number | null;
  current_duration_ms: number | null;
  baseline_total_tokens: number | null;
  current_total_tokens: number | null;
};

export type EvalCompare = {
  current: EvalRun;
  baseline: EvalRun & { cases_snapshot?: EvalCaseSnapshot[] };
  summary: {
    improved: number;
    regressed: number;
    unchanged: number;
    baseline_passed: number;
    current_passed: number;
    baseline_total_tokens: number;
    current_total_tokens: number;
  };
  cases: EvalCompareRow[];
};

export type EvalCaseInput = {
  name: string;
  input: string;
  expected_keywords: string[];
  forbidden_keywords: string[];
  expected_tools: string[];
  forbidden_tools: string[];
  max_duration_ms: number | null;
  max_total_tokens: number | null;
  enabled: boolean;
  sort_order?: number | null;
};

export function listEvalDatasets() {
  return request<{ items: EvalDatasetSummary[]; total: number }>(
    'GET',
    '/evals/datasets',
  );
}

export function createEvalDataset(input: { name: string; description: string }) {
  return request<EvalDataset>('POST', '/evals/datasets', input);
}

export function updateEvalDataset(
  id: number,
  input: { name?: string; description?: string },
) {
  return request<EvalDataset>('PATCH', `/evals/datasets/${id}`, input);
}

export function deleteEvalDataset(id: number) {
  return request<{ ok: boolean }>('DELETE', `/evals/datasets/${id}`);
}

export function getEvalDataset(id: number) {
  return request<EvalDatasetDetail>('GET', `/evals/datasets/${id}`);
}

export function createEvalCase(datasetId: number, input: EvalCaseInput) {
  return request<EvalCase>('POST', `/evals/datasets/${datasetId}/cases`, input);
}

export function updateEvalCase(id: number, input: Partial<EvalCaseInput>) {
  return request<EvalCase>('PATCH', `/evals/cases/${id}`, input);
}

export function deleteEvalCase(id: number) {
  return request<{ ok: boolean }>('DELETE', `/evals/cases/${id}`);
}

export function startEvalRun(
  datasetId: number,
  input: { agent_id: string; provider: string; model: string },
) {
  return request<EvalRun>('POST', `/evals/datasets/${datasetId}/runs`, input);
}

export function getEvalRun(runUid: string) {
  return request<EvalRunDetail>(
    'GET',
    `/evals/runs/${encodeURIComponent(runUid)}`,
  );
}

export function compareEvalRuns(runUid: string, baseline: string) {
  return request<EvalCompare>(
    'GET',
    `/evals/runs/${encodeURIComponent(runUid)}/compare?baseline=${encodeURIComponent(baseline)}`,
  );
}
