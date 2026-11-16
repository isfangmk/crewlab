// 并行排查 API：快照/列表/停止/重试 + 事件流订阅。
//
// 事件流与工作流运行同款口径：fetch 流（不用 EventSource，带不上 Authorization），
// 后端连上先补发缓冲再持续推，dispatch_finished 收尾。

import { request } from './client';
import { readSseFrames } from './sse';
import { getToken } from '@/store/auth';
import { errorText } from '@/lib/errors';

export type WorkerStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'stopped';
export type DispatchStatus = 'running' | 'succeeded' | 'failed' | 'stopped';

export type FindingEvidence = {
  call_id: string;
  tool: string;
  excerpt: string;
};

/** 全局编号发现：n 一经分配不复用；重试旧发现转 retired。 */
export type InvestigationFinding = {
  n: number;
  worker_uid: string;
  retired: boolean;
  title: string;
  detail: string;
  evidence: FindingEvidence[];
};

export type WorkerBudgetInfo = {
  token_budget?: number;
  max_steps?: number;
  timeout_seconds?: number;
  used_tokens?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
  };
};

export type InvestigationWorker = {
  worker_uid: string;
  name: string;
  target: string;
  attempt: number;
  status: WorkerStatus;
  conclusion: string;
  findings: { title: string; detail: string; evidence: FindingEvidence[] }[];
  log_text: string;
  error: string;
  budget: WorkerBudgetInfo;
  duration_ms: number;
  created_at: string | null;
  finished_at: string | null;
};

export type InvestigationStats = {
  total: number;
  succeeded: number;
  failed: number;
  stopped: number;
  running: number;
  findings: number;
  tool_calls: number;
};

export type InvestigationDispatch = {
  id: number;
  dispatch_uid: string;
  session_id: string;
  question: string;
  status: DispatchStatus;
  config: {
    workers?: { name: string; target: string; instruction?: string }[];
    token_budget_per_worker?: number;
    max_steps_per_worker?: number;
    timeout_seconds_per_worker?: number;
  };
  budget: Record<string, unknown>;
  findings: InvestigationFinding[];
  stats: InvestigationStats;
  workers: InvestigationWorker[];
  error: string;
  duration_ms: number;
  created_by: number | null;
  created_at: string | null;
  finished_at: string | null;
};

export type InvestigationStreamEvent =
  | { event: 'worker_started'; dispatch_uid: string; worker_uid: string; name: string; target: string }
  | { event: 'worker_log'; dispatch_uid: string; worker_uid: string; line: string; tool: string }
  | { event: 'worker_findings'; dispatch_uid: string; worker_uid: string; conclusion: string; findings: unknown[] }
  | {
      event: 'worker_finished';
      dispatch_uid: string;
      worker_uid: string;
      status: WorkerStatus;
      error: string;
      duration_ms: number;
      usage: Record<string, number>;
    }
  | { event: 'dispatch_updated'; dispatch_uid: string; status: DispatchStatus; stats: InvestigationStats }
  | {
      event: 'dispatch_finished';
      dispatch_uid: string;
      status: DispatchStatus;
      stats: InvestigationStats;
      /** 自动汇总消息 id：前端据此重拉消息列表 */
      summary_message_id?: string | null;
    };

/** 终止事件：收到它就不再有后续了。 */
const TERMINAL_EVENTS = new Set(['dispatch_finished']);

export function listInvestigations(params: {
  session_id?: string;
  limit?: number;
  offset?: number;
}): Promise<{ items: InvestigationDispatch[]; total: number }> {
  const q = new URLSearchParams();
  if (params.session_id) q.set('session_id', params.session_id);
  if (typeof params.limit === 'number') q.set('limit', String(params.limit));
  if (typeof params.offset === 'number') q.set('offset', String(params.offset));
  const qs = q.toString();
  return request('GET', `/investigations${qs ? `?${qs}` : ''}`);
}

export function getInvestigation(dispatchUid: string): Promise<InvestigationDispatch> {
  return request('GET', `/investigations/${encodeURIComponent(dispatchUid)}`);
}

export function stopInvestigation(dispatchUid: string): Promise<{ stopped: boolean }> {
  return request('POST', `/investigations/${encodeURIComponent(dispatchUid)}/stop`);
}

export function retryWorker(
  dispatchUid: string,
  workerUid: string,
): Promise<{ worker_uid: string; status: WorkerStatus; attempt: number }> {
  return request(
    'POST',
    `/investigations/${encodeURIComponent(dispatchUid)}/workers/${encodeURIComponent(workerUid)}/retry`,
  );
}

/**
 * 订阅一次排查的事件流。
 *
 * 后端连上就先补发已经发生的事件，所以排查早就开始也接得住，不会缺步骤；
 * 软截止后后台续跑的事件也走这条流。
 */
export async function subscribeDispatchEvents(
  dispatchUid: string,
  onEvent: (event: InvestigationStreamEvent) => void,
  abortSignal?: AbortSignal,
): Promise<void> {
  const token = getToken();
  const response = await fetch(
    `/api/v1/investigations/${encodeURIComponent(dispatchUid)}/events`,
    {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      signal: abortSignal,
    },
  );

  await readSseFrames(response, (frame) => {
    let event: InvestigationStreamEvent;
    try {
      event = JSON.parse(frame.data) as InvestigationStreamEvent;
    } catch (err) {
      throw new Error(`排查事件 JSON 非法: ${errorText(err)}`);
    }
    onEvent(event);
    // 终止事件之后不会再有推送，主动收尾省掉一条长连接
    return !TERMINAL_EVENTS.has(event.event);
  });
}
