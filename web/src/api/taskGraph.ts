// 任务图 API：快照/列表/编辑/执行控制 + 事件流订阅。
//
// 与旧排查/计划同款口径：fetch 流（不用 EventSource，带不上 Authorization），
// 后端连上先补发缓冲再持续推，graph_finished 收尾。

import { request } from './client';
import { readSseFrames } from './sse';
import { getToken } from '@/store/auth';
import { errorText } from '@/lib/errors';

export type GraphStatus = 'draft' | 'running' | 'paused' | 'succeeded' | 'failed' | 'stopped';
export type NodeStatus =
  | 'pending'
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'skipped'
  | 'stopped';

export type FindingEvidence = { call_id: string; tool: string; excerpt: string };

export type GraphFinding = {
  title: string;
  detail: string;
  evidence: FindingEvidence[];
};

export type TaskGraphNode = {
  node_uid: string;
  local_id: string;
  depends_on: string[];
  order_index: number;
  name: string;
  target: string;
  instruction: string;
  tools: string[];
  status: NodeStatus;
  attempt: number;
  summary: string;
  findings: GraphFinding[];
  tool_trace: Record<string, unknown>[];
  log_text: string;
  usage: Record<string, number>;
  error: string;
  duration_ms: number;
  started_at: string | null;
  finished_at: string | null;
};

export type GraphStats = {
  total: number;
  succeeded: number;
  failed: number;
  skipped: number;
  stopped: number;
  running: number;
  pending: number;
  tool_calls: number;
};

export type TaskGraph = {
  id: number;
  graph_uid: string;
  session_id: string;
  card_message_id: string;
  title: string;
  question: string;
  status: GraphStatus;
  config: {
    max_steps_per_node?: number;
    timeout_seconds_per_node?: number;
    token_budget_per_node?: number;
  };
  stats: GraphStats;
  used_tokens: number;
  nodes: TaskGraphNode[];
  findings: GraphFinding[];
  error: string;
  duration_ms: number;
  created_by: number | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
};

export type TaskGraphStreamEvent =
  | { event: 'graph_started'; graph_uid: string }
  | {
      event: 'node_started';
      graph_uid: string;
      node_uid: string;
      name: string;
      local_id: string;
      depends_on: string[];
    }
  | {
      event: 'node_log';
      graph_uid: string;
      node_uid: string;
      name: string;
      line: string;
      tool: string;
    }
  | {
      event: 'node_finished';
      graph_uid: string;
      node_uid: string;
      name: string;
      local_id: string;
      status: NodeStatus;
      summary: string;
      error: string;
      duration_ms: number;
    }
  | { event: 'graph_updated'; graph_uid: string; status: GraphStatus; stats: GraphStats }
  | {
      event: 'summary_delta';
      graph_uid: string;
      message_id: string;
      delta: string;
      partial: boolean;
    }
  | {
      event: 'graph_finished';
      graph_uid: string;
      status: GraphStatus;
      stats: GraphStats;
      summary_message_id?: string | null;
    };

const TERMINAL_EVENTS = new Set(['graph_finished']);

export function listTaskGraphs(params: {
  session_id?: string;
  limit?: number;
  offset?: number;
}): Promise<{ items: TaskGraph[]; total: number }> {
  const q = new URLSearchParams();
  if (params.session_id) q.set('session_id', params.session_id);
  if (typeof params.limit === 'number') q.set('limit', String(params.limit));
  if (typeof params.offset === 'number') q.set('offset', String(params.offset));
  const qs = q.toString();
  return request('GET', `/task-graphs${qs ? `?${qs}` : ''}`);
}

export function getTaskGraphDetail(graphUid: string): Promise<TaskGraph> {
  return request('GET', `/task-graphs/${encodeURIComponent(graphUid)}`);
}

export function updateGraphNodes(
  graphUid: string,
  nodesJson: string,
  edgesJson = '[]',
): Promise<TaskGraph> {
  return request('PATCH', `/task-graphs/${encodeURIComponent(graphUid)}/nodes`, {
    nodes_json: nodesJson,
    edges_json: edgesJson,
  });
}

export function confirmGraph(graphUid: string): Promise<{ started: boolean }> {
  return request('POST', `/task-graphs/${encodeURIComponent(graphUid)}/confirm`);
}

export function stopGraph(graphUid: string): Promise<{ stopped: boolean }> {
  return request('POST', `/task-graphs/${encodeURIComponent(graphUid)}/stop`);
}

export function retryGraphNode(
  graphUid: string,
  nodeUid: string,
): Promise<{ node_uid: string; status: NodeStatus; attempt: number }> {
  return request(
    'POST',
    `/task-graphs/${encodeURIComponent(graphUid)}/nodes/${encodeURIComponent(nodeUid)}/retry`,
  );
}

export function skipGraphNode(
  graphUid: string,
  nodeUid: string,
): Promise<{ node_uid: string; status: NodeStatus }> {
  return request(
    'POST',
    `/task-graphs/${encodeURIComponent(graphUid)}/nodes/${encodeURIComponent(nodeUid)}/skip`,
  );
}

export async function subscribeGraphEvents(
  graphUid: string,
  onEvent: (event: TaskGraphStreamEvent) => void,
  abortSignal?: AbortSignal,
): Promise<void> {
  const token = getToken();
  const response = await fetch(
    `/api/v1/task-graphs/${encodeURIComponent(graphUid)}/events`,
    {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      signal: abortSignal,
    },
  );

  await readSseFrames(response, (frame) => {
    let event: TaskGraphStreamEvent;
    try {
      event = JSON.parse(frame.data) as TaskGraphStreamEvent;
    } catch (err) {
      throw new Error(`任务图事件 JSON 非法: ${errorText(err)}`);
    }
    onEvent(event);
    return !TERMINAL_EVENTS.has(event.event);
  });
}
