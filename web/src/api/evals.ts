// 评测集与评测运行：真实会话跑分，结果保留 trace / 会话关联。
import { request, requestFile } from './client';

export type EvalRunStatus = 'running' | 'completed' | 'failed';
export type EvalCaseStatus = 'passed' | 'failed' | 'error';
export type EvalCaseKind = 'normal' | 'redteam';
export type EvalAnnotationLabel = 'good' | 'needs_work' | 'bad';

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
  baseline_run_uid: string;
  gate_max_regressions: number;
  gate_min_pass_rate: number;
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
  kind: EvalCaseKind;
  attack_type: string;
  safety_keywords: string[];
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
  kind?: EvalCaseKind;
  attack_type?: string;
  safety_keywords?: string[];
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
  kind?: EvalCaseKind;
  attack_type?: string;
  safety_keywords?: string[];
  expected_keywords: string[];
  forbidden_keywords: string[];
  expected_tools: string[];
  forbidden_tools: string[];
  max_duration_ms: number | null;
  max_total_tokens: number | null;
  enabled: boolean;
  sort_order?: number | null;
};

export type EvalJudgeConfig = {
  dataset_id: number;
  enabled: boolean;
  rubric: string;
  judge_provider: string;
  judge_model: string;
  pass_score: number;
  updated_at: string | null;
};

export type EvalJudgeScore = {
  id: string;
  result_id: string;
  dimension: string;
  score: number;
  reason: string;
  judge_model: string;
  created_at: string | null;
};

export type EvalAnnotation = {
  id: string;
  result_id: string;
  annotator_user_id: number | null;
  label: EvalAnnotationLabel;
  comment: string;
  created_at: string | null;
  case_name?: string;
  run_uid?: string;
  result_status?: EvalCaseStatus;
};

export type EvalConsistency = {
  results_total: number;
  annotated_results: number;
  multi_annotated: number;
  agreement_rate: number | null;
  label_counts: Record<string, number>;
};

export type EvalGateResult = {
  run_uid: string;
  baseline_run_uid: string;
  pass: boolean;
  pass_rate: number;
  regressions: number;
  improved: number;
  max_regressions: number;
  min_pass_rate: number;
  reasons: string[];
  regressed_cases: EvalCompareRow[];
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
  input: {
    name?: string;
    description?: string;
    baseline_run_uid?: string;
    gate_max_regressions?: number;
    gate_min_pass_rate?: number;
  },
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

export function getJudgeConfig(datasetId: number) {
  return request<EvalJudgeConfig>('GET', `/evals/datasets/${datasetId}/judge-config`);
}

export function saveJudgeConfig(
  datasetId: number,
  input: {
    enabled: boolean;
    rubric: string;
    judge_provider: string;
    judge_model: string;
    pass_score: number;
  },
) {
  return request<EvalJudgeConfig>(
    'PUT',
    `/evals/datasets/${datasetId}/judge-config`,
    input,
  );
}

export function listJudgeScores(runUid: string, resultId: string) {
  return request<{ items: EvalJudgeScore[]; total: number }>(
    'GET',
    `/evals/runs/${encodeURIComponent(runUid)}/results/${encodeURIComponent(resultId)}/judge-scores`,
  );
}

export function listAnnotations(runUid: string) {
  return request<{
    items: EvalAnnotation[];
    total: number;
    consistency: EvalConsistency;
  }>('GET', `/evals/runs/${encodeURIComponent(runUid)}/annotations`);
}

export function claimNextAnnotation(runUid: string) {
  return request<{ result: EvalCaseResult | null; annotation_count: number }>(
    'GET',
    `/evals/annotations/queue?run_uid=${encodeURIComponent(runUid)}`,
  );
}

export function submitAnnotation(input: {
  result_id: string;
  label: EvalAnnotationLabel;
  comment: string;
}) {
  return request<EvalAnnotation>('POST', '/evals/annotations', input);
}

export function updateAnnotation(
  id: string,
  input: { label: EvalAnnotationLabel; comment: string },
) {
  return request<EvalAnnotation>(
    'POST',
    `/evals/annotations/${encodeURIComponent(id)}`,
    input,
  );
}

export function evaluateGate(runUid: string) {
  return request<EvalGateResult>(
    'POST',
    `/evals/runs/${encodeURIComponent(runUid)}/gate`,
  );
}

export function exportEvalRun(runUid: string, format: 'json' | 'csv') {
  return requestFile(
    `/evals/runs/${encodeURIComponent(runUid)}/export`,
    new URLSearchParams({ format }),
  );
}

export function seedRedteamCases(datasetId: number) {
  return request<{ created: number; items: EvalCase[] }>(
    'POST',
    `/evals/datasets/${datasetId}/redteam/seed`,
  );
}

/** 创建运行只读分享的响应；明文 token 只此一次返回。 */
export type EvalShareResp = {
  token: string;
  expires_at: string;
  run_uid: string;
};

/** 免登录分享报告载荷：评分内嵌，附门禁结论与标注一致性。 */
export type EvalShareReport = {
  expires_at: string;
  dataset: EvalDataset;
  run: EvalRun;
  cases_snapshot: EvalCaseSnapshot[];
  results: Array<EvalCaseResult & { judge_scores: EvalJudgeScore[] }>;
  gate: EvalGateResult | null;
  consistency: EvalConsistency | null;
};

export function createEvalRunShare(runUid: string, hours: number): Promise<EvalShareResp> {
  return request<EvalShareResp>(
    'POST',
    `/evals/runs/${encodeURIComponent(runUid)}/share`,
    { expires_in_hours: hours },
  );
}

/** 公开报告用原生 fetch：带 Authorization 的请求客户端会把匿名 401 当强制登出。 */
export async function fetchEvalShare(token: string): Promise<EvalShareReport> {
  const res = await fetch(`/api/v1/evals/shares/${encodeURIComponent(token)}`);
  if (!res.ok) {
    throw new Error(`status=${res.status}`);
  }
  return (await res.json()) as EvalShareReport;
}
