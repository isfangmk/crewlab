import { request } from './client';

export type ObservationTurn = {
  trace_id: string;
  session_id: string;
  agent_id: string;
  provider: string;
  model: string;
  status: string;
  request_id: string;
  input_summary: string;
  error_message: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  duration_ms: number | null;
  tool_call_count: number;
  skill_call_count: number;
  started_at: string | null;
  ended_at: string | null;
};

export type ObservationToolCall = {
  tool_call_id: string;
  name: string;
  /** skill | mcp | tool */
  kind: string;
  detail: string;
  status: string;
  duration_ms: number | null;
  error: string | null;
  started_at: string | null;
};

export type ObservationTurnDetail = ObservationTurn & {
  tool_calls: ObservationToolCall[];
};

export type ObservationStats = {
  total: number;
  error_count: number;
  running_count: number;
  avg_duration_ms: number | null;
  p95_duration_ms: number | null;
  total_tokens: number;
  tool_call_total: number;
  skill_call_total: number;
};

export type ObservationOverview = {
  stats: ObservationStats;
  agents: string[];
  models: string[];
};

/** 列表与汇总共用的筛选条件。 */
export type ObservationFilters = {
  q?: string;
  session_id?: string;
  status?: string;
  agent_id?: string;
  model?: string;
  started_from?: string;
  started_to?: string;
  min_duration_ms?: number;
};

function filtersToQuery(params: ObservationFilters): URLSearchParams {
  const sp = new URLSearchParams();
  if (params.q) sp.set('q', params.q);
  if (params.session_id) sp.set('session_id', params.session_id);
  if (params.status) sp.set('status', params.status);
  if (params.agent_id) sp.set('agent_id', params.agent_id);
  if (params.model) sp.set('model', params.model);
  if (params.started_from) sp.set('started_from', params.started_from);
  if (params.started_to) sp.set('started_to', params.started_to);
  if (params.min_duration_ms != null) sp.set('min_duration_ms', String(params.min_duration_ms));
  return sp;
}

export function listObservationTurns(
  params: ObservationFilters & { limit: number; offset: number },
) {
  const sp = filtersToQuery(params);
  sp.set('limit', String(params.limit));
  sp.set('offset', String(params.offset));
  return request<{ items: ObservationTurn[]; total: number }>(
    'GET',
    `/observability/turns?${sp.toString()}`,
  );
}

export function getObservationOverview(params: ObservationFilters) {
  const qs = filtersToQuery(params).toString();
  return request<ObservationOverview>(
    'GET',
    `/observability/overview${qs === '' ? '' : `?${qs}`}`,
  );
}

export function getObservationTurn(traceId: string) {
  if (traceId === '') {
    throw new Error('trace_id 不能为空');
  }
  return request<ObservationTurnDetail>('GET', `/observability/turns/${encodeURIComponent(traceId)}`);
}
