// 规划执行 API：快照/列表/编辑/执行控制 + 事件流订阅。
//
// 与排查同款口径：fetch 流（不用 EventSource，带不上 Authorization），
// 后端连上先补发缓冲再持续推，plan_finished 收尾。

import { request } from './client';
import { readSseFrames } from './sse';
import { getToken } from '@/store/auth';
import { errorText } from '@/lib/errors';

export type PlanStatus = 'draft' | 'running' | 'paused' | 'succeeded' | 'stopped';
export type StepStatus =
  | 'pending'
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'skipped'
  | 'stopped';

export type PlanStep = {
  step_uid: string;
  order_index: number;
  name: string;
  instruction: string;
  tools: string[];
  status: StepStatus;
  attempt: number;
  summary: string;
  findings: { title: string; detail: string; evidence: { call_id: string; tool: string; excerpt: string }[] }[];
  log_text: string;
  usage: Record<string, number>;
  error: string;
  duration_ms: number;
  started_at: string | null;
  finished_at: string | null;
};

export type PlanStats = {
  total: number;
  succeeded: number;
  failed: number;
  skipped: number;
  stopped: number;
  running: number;
  pending: number;
  tool_calls: number;
};

export type PlanExecution = {
  id: number;
  plan_uid: string;
  session_id: string;
  title: string;
  question: string;
  status: PlanStatus;
  config: {
    max_steps_per_step?: number;
    timeout_seconds_per_step?: number;
    token_budget_per_step?: number;
  };
  stats: PlanStats;
  used_tokens: number;
  steps: PlanStep[];
  error: string;
  duration_ms: number;
  created_by: number | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
};

export type PlanStreamEvent =
  | { event: 'plan_started'; plan_uid: string }
  | { event: 'step_started'; plan_uid: string; step_uid: string; name: string; order_index: number }
  | { event: 'step_log'; plan_uid: string; step_uid: string; line: string; tool: string }
  | {
      event: 'step_finished';
      plan_uid: string;
      step_uid: string;
      status: StepStatus;
      summary: string;
      error: string;
      duration_ms: number;
    }
  | { event: 'plan_updated'; plan_uid: string; status: PlanStatus }
  | {
      /** 收尾汇总正文增量：转发进对话流逐字显示，落库后由 plan_finished 对账 */
      event: 'summary_delta';
      plan_uid: string;
      message_id: string;
      delta: string;
      partial: boolean;
    }
  | {
      event: 'plan_finished';
      plan_uid: string;
      status: PlanStatus;
      /** 自动汇总消息 id：前端据此重拉消息列表 */
      summary_message_id?: string | null;
    };

/** 终止事件：收到它就不再有后续了。 */
const TERMINAL_EVENTS = new Set(['plan_finished']);

export function listPlans(params: {
  session_id?: string;
  limit?: number;
  offset?: number;
}): Promise<{ items: PlanExecution[]; total: number }> {
  const q = new URLSearchParams();
  if (params.session_id) q.set('session_id', params.session_id);
  if (typeof params.limit === 'number') q.set('limit', String(params.limit));
  if (typeof params.offset === 'number') q.set('offset', String(params.offset));
  const qs = q.toString();
  return request('GET', `/plans${qs ? `?${qs}` : ''}`);
}

export function getPlan(planUid: string): Promise<PlanExecution> {
  return request('GET', `/plans/${encodeURIComponent(planUid)}`);
}

/** 整体替换步骤（排序/增删/改文案）；仅待确认状态可改。 */
export function updatePlanSteps(planUid: string, stepsJson: string): Promise<PlanExecution> {
  return request('PATCH', `/plans/${encodeURIComponent(planUid)}/steps`, { steps_json: stepsJson });
}

export function startPlan(planUid: string): Promise<{ started: boolean }> {
  return request('POST', `/plans/${encodeURIComponent(planUid)}/start`);
}

export function pausePlan(planUid: string): Promise<{ paused: boolean }> {
  return request('POST', `/plans/${encodeURIComponent(planUid)}/pause`);
}

export function stopPlan(planUid: string): Promise<{ stopped: boolean }> {
  return request('POST', `/plans/${encodeURIComponent(planUid)}/stop`);
}

export function retryPlanStep(
  planUid: string,
  stepUid: string,
): Promise<{ retried: boolean }> {
  return request(
    'POST',
    `/plans/${encodeURIComponent(planUid)}/steps/${encodeURIComponent(stepUid)}/retry`,
  );
}

export function skipPlanStep(
  planUid: string,
  stepUid: string,
): Promise<{ skipped: boolean }> {
  return request(
    'POST',
    `/plans/${encodeURIComponent(planUid)}/steps/${encodeURIComponent(stepUid)}/skip`,
  );
}

/**
 * 订阅一次计划执行的事件流。
 *
 * 后端连上就先补发已经发生的事件，暂停/重试/跳过后的续跑事件也走这条流，
 * 所以断线重连不缺步骤。
 */
export async function subscribePlanEvents(
  planUid: string,
  onEvent: (event: PlanStreamEvent) => void,
  abortSignal?: AbortSignal,
): Promise<void> {
  const token = getToken();
  const response = await fetch(`/api/v1/plans/${encodeURIComponent(planUid)}/events`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    signal: abortSignal,
  });

  await readSseFrames(response, (frame) => {
    let event: PlanStreamEvent;
    try {
      event = JSON.parse(frame.data) as PlanStreamEvent;
    } catch (err) {
      throw new Error(`计划事件 JSON 非法: ${errorText(err)}`);
    }
    onEvent(event);
    // 终止事件之后不会再有推送，主动收尾省掉一条长连接
    return !TERMINAL_EVENTS.has(event.event);
  });
}
