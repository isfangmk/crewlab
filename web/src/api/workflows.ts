// 工作流 API：画布定义（nodes / edges）、运行、人工节点恢复。
//
// 契约与后端 app/schemas/workflow.py 一一对应；节点字段按 kind 分权，
// 不属于该 kind 的字段必须保持空值，否则后端会显式拒绝保存。

import { request } from './client';
import { readSseFrames } from './sse';
import { getToken } from '@/store/auth';

export type NodeKind =
  | 'start'
  | 'end'
  | 'join'
  | 'switch'
  | 'loop'
  | 'agent'
  | 'knowledge'
  | 'search'
  | 'mcp'
  | 'http'
  | 'code'
  | 'assign'
  | 'approval'
  | 'input';

export type VariableType = 'string' | 'number' | 'boolean' | 'json';

export type WorkflowVariable = {
  name: string;
  type: VariableType;
  description: string;
  required: boolean;
  /** null 表示未设置默认值；必填变量与默认值互斥。 */
  default: unknown;
};

export type WorkflowNode = {
  id: string;
  kind: NodeKind;
  label: string;
  x: number;
  y: number;
  output_key: string;

  // start
  variables: WorkflowVariable[];

  // agent
  provider: string;
  model: string;
  instruction: string;

  // knowledge / search / http
  query: string;
  limit: number;
  paths: string[];

  // mcp
  mcp_server: string;
  mcp_tool: string;
  arguments: Record<string, string>;

  // http
  method: string;
  url: string;
  headers: Record<string, string>;
  body: string;

  // code
  code: string;

  // assign
  assignments: Record<string, string>;

  // switch
  branches: string[];
  expression: string;
  branch_mode: 'expr' | 'model';

  // loop
  max_iterations: number;

  // approval / input
  prompt: string;

  timeout_seconds: number | null;
  retry_count: number;
};

export type WorkflowEdge = {
  id: string;
  source: string;
  target: string;
  /** 空串表示无条件边；switch / loop 的出边必须落在分支或 loop/exit 上。 */
  route: string;
};

export type WorkflowDefinition = {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
};

export type WorkflowStatus = 'draft' | 'published';
export type RunStatus = 'running' | 'succeeded' | 'failed' | 'waiting' | 'stopped';

export type WorkflowPreview = {
  nodes: { id: string; kind: NodeKind; label: string; x?: number; y?: number }[];
  edges: { id?: string; source: string; target: string; route: string }[];
};

/** 工作流最近一次运行：列表用它的状态显示「运行中 / 成功 / 失败」。 */
export type WorkflowLastRun = {
  run_uid: string;
  status: RunStatus;
  created_at: string;
};

export type WorkflowSummary = {
  id: number;
  name: string;
  description: string;
  status: WorkflowStatus;
  version: number;
  validation_error: string;
  /** 是否真的发布过：false 表示没有可运行的已发布版本，列表页不给运行入口。 */
  has_published: boolean;
  node_count: number;
  edge_count: number;
  warnings: string[];
  /** 从未运行时为 null。 */
  last_run: WorkflowLastRun | null;
  created_at: string;
  updated_at: string;
};

export type WorkflowDetail = WorkflowSummary & { definition: WorkflowDefinition };

export type CatalogKind = {
  key: NodeKind;
  title: string;
  title_en: string;
  group: string;
};

export type CatalogGroup = { key: string; title: string; title_en: string };

export type WorkflowCatalog = {
  node_groups: CatalogGroup[];
  node_kinds: CatalogKind[];
  models: { provider: string; model: string; title: string }[];
  mcp_tools: { server: string; tool: string; title: string }[];
};

export type WorkflowRun = {
  id: number;
  run_uid: string;
  workflow_id: number;
  /** 列表/详情都带上工作流名，列表分页后不依赖本地映射。 */
  workflow_name: string;
  workflow_version: number;
  /** 发起这次运行时使用的定义来源：draft 说明跑的是未发布的改动。 */
  workflow_status: WorkflowStatus;
  /** 所属工作流当前是否已发布：false 时不给「再次运行」入口。 */
  has_published: boolean;
  status: RunStatus;
  trigger: string;
  triggered_by: string;
  /** JSON 文本：实际注入开始节点的初始状态。 */
  input: string;
  /** JSON 文本：运行输出；waiting 时是 {gate, kind, prompt}。 */
  output: string;
  error: string;
  duration_ms: number;
  created_at: string;
  finished_at: string | null;
};

export type NodeRunStatus = 'succeeded' | 'failed';

export type WorkflowNodeRun = {
  id: number;
  node_id: string;
  node_kind: NodeKind;
  attempt: number;
  /** running 只在运行中的实时时间线上出现，落库时只会是终态。 */
  status: NodeRunStatus | 'running';
  /** 节点执行前看到的状态快照，面板上的「输入」。 */
  input: string;
  output: string;
  error: string;
  duration_ms: number;
  route: string;
  created_at: string;
};

export type WorkflowRunDetail = WorkflowRun & {
  nodes: WorkflowNodeRun[];
  definition: WorkflowDefinition;
};

export type ValidationResult = { ok: boolean; error: string; warnings: string[] };

/** waiting 运行里停在的人工节点信息（后端把这段 JSON 放在 output）。 */
export type WaitingGate = { gate: string; kind: 'approval' | 'input'; prompt: string };

export function getCatalog() {
  return request<WorkflowCatalog>('GET', '/workflows/catalog');
}

/** 新建工作流的初始画布：只有一个开始节点，其余节点由用户在画布上添加。 */
export function blankDefinition(): WorkflowDefinition {
  return {
    nodes: [
      {
        id: 'start',
        kind: 'start',
        label: '开始',
        x: 120,
        y: 160,
        output_key: '',
        variables: [
          { name: 'text', type: 'string', description: '本次运行的输入', required: true, default: null },
        ],
        provider: '',
        model: '',
        instruction: '',
        query: '',
        limit: 5,
        paths: [],
        mcp_server: '',
        mcp_tool: '',
        arguments: {},
        method: 'GET',
        url: '',
        headers: {},
        body: '',
        code: '',
        assignments: {},
        branches: [],
        expression: '',
        branch_mode: 'expr',
        max_iterations: 5,
        prompt: '',
        timeout_seconds: null,
        retry_count: 0,
      },
    ],
    edges: [],
  };
}

export function listWorkflows(
  params: {
    q?: string;
    status?: '' | 'draft' | 'published';
    sort?: 'asc' | 'desc';
    limit?: number;
    offset?: number;
  } = {},
) {
  const query = new URLSearchParams();
  if (params.q) query.set('q', params.q);
  if (params.status) query.set('status', params.status);
  query.set('sort', params.sort ?? 'desc');
  query.set('limit', String(params.limit ?? 20));
  query.set('offset', String(params.offset ?? 0));
  return request<{ items: WorkflowSummary[]; total: number }>('GET', `/workflows?${query.toString()}`);
}

export function getWorkflow(id: number) {
  return request<WorkflowDetail>('GET', `/workflows/${id}`);
}

export function createWorkflow(payload: {
  name: string;
  description: string;
  definition: WorkflowDefinition;
}) {
  return request<WorkflowDetail>('POST', '/workflows', payload);
}

export function updateWorkflow(
  id: number,
  payload: {
    name: string;
    description: string;
    definition: WorkflowDefinition;
  },
) {
  return request<WorkflowDetail>('PUT', `/workflows/${id}`, payload);
}

/** 发布当前草稿：后端校验通过后才会升版本、把状态切成已发布。 */
export function publishWorkflow(id: number) {
  return request<WorkflowDetail>('POST', `/workflows/${id}/publish`);
}

export function deleteWorkflow(id: number) {
  return request<{ deleted: number }>('DELETE', `/workflows/${id}`);
}

export function validateDefinition(definition: WorkflowDefinition) {
  return request<ValidationResult>('POST', '/workflows/validate', { definition });
}

/** 跑哪一份定义：published 是已发布快照（列表页 / 再次运行），draft 是当前草稿（编排页）。 */
export type RunSource = 'published' | 'draft';

/**
 * 发起运行：后端只建运行记录就返回 run_uid，执行在后台继续。
 *
 * 因此这里很快就回来，调用方拿到 run_uid 立刻跳转即可，进度靠 subscribeRunEvents。
 */
export function startWorkflowRun(id: number, values: Record<string, unknown>, source: RunSource = 'published') {
  return request<{ run_uid: string; workflow_id: number }>('POST', `/workflows/${id}/runs/start`, {
    values,
    source,
  });
}

/** 运行事件：与后端 SSE 的 event 字段一一对应。 */
export type RunStreamEvent =
  | { event: 'run_started'; run_uid: string; workflow_id: number; values: Record<string, unknown> }
  | {
      event: 'node_started';
      node_id: string;
      node_kind: NodeKind;
      label: string;
      attempt: number;
      /** 这一步读到的变量：面板在运行中就能展示输入。 */
      input?: Record<string, unknown>;
    }
  | {
      event: 'node_finished';
      node_id: string;
      node_kind: NodeKind;
      status: NodeRunStatus;
      /** 节点真正读到的变量，与落库的 input_json 同口径。 */
      input?: Record<string, unknown>;
      output?: unknown;
      route?: string;
      error?: string;
      duration_ms: number;
    }
  | {
      /** 运行停在人工节点：通道保持打开，等用户确认后继续推后续事件。 */
      event: 'run_waiting';
      run_uid: string;
      status: 'waiting';
      output: string;
      duration_ms: number;
      gate: string | null;
      /** 区分审批与填写，节点上要显示不同文案。 */
      gate_kind: 'approval' | 'input' | null;
    }
  | {
      /** 运行真正结束：收到它就关闭订阅。 */
      event: 'run_finished';
      run_uid: string;
      status: RunStatus;
      error: string;
      output: string;
      duration_ms: number;
      gate: string | null;
      gate_kind: 'approval' | 'input' | null;
    }
  | { event: 'run_failed'; error: string };

/** 终止事件：收到这些就不再有后续了。 */
const TERMINAL_EVENTS = new Set(['run_finished', 'run_failed']);

/**
 * 订阅一次运行的事件流。
 *
 * 后端连上就先补发已经发生的事件，所以运行早就开始也接得住，不会缺步骤。
 * 不用 EventSource：它带不上 Authorization 头，鉴权走 fetch 流。
 */
export async function subscribeRunEvents(
  runUid: string,
  onEvent: (event: RunStreamEvent) => void,
  abortSignal?: AbortSignal,
): Promise<void> {
  const token = getToken();
  const response = await fetch(`/api/v1/workflows/runs/${encodeURIComponent(runUid)}/events`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    signal: abortSignal,
  });

  await readSseFrames(response, (frame) => {
    let event: RunStreamEvent;
    try {
      event = JSON.parse(frame.data) as RunStreamEvent;
    } catch (err) {
      throw new Error(`运行事件 JSON 非法: ${err instanceof Error ? err.message : String(err)}`);
    }
    onEvent(event);
    // 终止事件之后不会再有推送，主动收尾省掉一条长连接
    return !TERMINAL_EVENTS.has(event.event);
  });
}

/** 单节点试跑：后端从开始节点真实执行到该节点为止，返回这一步的结果。 */
export type NodeProbeResult = {
  node_id: string;
  node_kind: NodeKind;
  attempt: number;
  /** 试跑同步返回，落库时已经是终态。 */
  status: NodeRunStatus;
  /** 节点执行前看到的状态快照（JSON 文本）。 */
  input: string;
  /** JSON 文本，可能是纯文本预览。 */
  output: string;
  error: string;
  duration_ms: number;
  route: string;
};

/**
 * 运行中的节点结果：与试跑结果同形，但状态可能还是 running。
 *
 * 实时事件里 node_started 只带输入，跑完才补输出与耗时。
 */
export type LiveNodeRun = Omit<NodeProbeResult, 'status'> & {
  status: NodeRunStatus | 'running';
};

export function runNode(workflowId: number, nodeId: string, values: Record<string, unknown>) {
  return request<NodeProbeResult>('POST', `/workflows/${workflowId}/nodes/${encodeURIComponent(nodeId)}/run`, { values });
}

export function listRuns(
  params: { workflowId?: number; status?: RunStatus | ''; query?: string; limit?: number; offset?: number } = {},
) {
  const q = new URLSearchParams();
  if (params.workflowId != null) q.set('workflow_id', String(params.workflowId));
  if (params.status != null && params.status !== '') q.set('status', params.status);
  if (params.query != null && params.query.trim() !== '') q.set('q', params.query.trim());
  q.set('limit', String(params.limit ?? 20));
  q.set('offset', String(params.offset ?? 0));
  return request<{ items: WorkflowRun[]; total: number }>('GET', `/workflows/runs/list?${q.toString()}`);
}

export function getRun(runUid: string) {
  return request<WorkflowRunDetail>('GET', `/workflows/runs/${runUid}`);
}

export function resumeRun(runUid: string, decision: 'approve' | 'reject', values: Record<string, unknown> = {}) {
  return request<WorkflowRunDetail>('POST', `/workflows/runs/${runUid}/resume`, { decision, values });
}

/** 最近一次运行里执行过的节点：画布进入时按它恢复每个节点的状态。 */
export type LatestRunNode = {
  node_id: string;
  status: NodeRunStatus;
};

/** 最近一次运行快照：只带节点状态与停住的人工节点，不带输入输出。 */
export type LatestRunSnapshot = {
  run_uid: string;
  status: RunStatus;
  trigger: string;
  /** 发起时的版本号与选用的定义来源。 */
  workflow_version: number;
  workflow_status: WorkflowStatus;
  /** 停在人工节点时是节点 ID；gate_kind 决定显示「待审批」还是「待填写」。 */
  gate: string | null;
  gate_kind: 'approval' | 'input' | null;
  created_at: string;
  finished_at: string | null;
  nodes: LatestRunNode[];
};

/** 画布首屏恢复运行状态：从没跑过时 run 为 null。 */
export function getLatestRun(workflowId: number) {
  return request<{ run: LatestRunSnapshot | null }>('GET', `/workflows/${workflowId}/runs/latest`);
}

/**
 * 真正中断后端那一次运行。
 *
 * 前端 abort 只断开 SSE，后端还会继续跑；必须再发这一枪，否则用户点了停止，
 * 数据库里的运行却仍然在推进。
 */
export function stopWorkflowRun(runUid: string) {
  return request<{ stopped: boolean }>('POST', `/workflows/runs/${runUid}/stop`);
}

/** 解析后端下发的 JSON 文本字段（input / output / node output）。 */
export function parseJsonText(raw: string): unknown;
/** 已在内存里的运行事件值：JSON 文本照常解析，结构化结果原样返回。 */
export function parseJsonText(raw: unknown): unknown;
export function parseJsonText(raw: unknown): unknown {
  if (typeof raw !== 'string') return raw ?? null;
  if (raw.trim() === '') return null;
  try {
    return JSON.parse(raw);
  } catch {
    // 运行输出允许是纯文本（预览被截断时会带省略号），原样返回。
    return raw;
  }
}
