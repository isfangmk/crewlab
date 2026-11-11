// 工作流 API：画布定义（nodes / edges）、运行、人工节点恢复。
//
// 契约与后端 app/schemas/workflow.py 一一对应；节点字段按 kind 分权，
// 不属于该 kind 的字段必须保持空值，否则后端会显式拒绝保存。

import { request } from './client';

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
export type RunStatus = 'running' | 'succeeded' | 'failed' | 'waiting';

export type WorkflowPreview = {
  nodes: { id: string; kind: NodeKind; label: string }[];
  edges: { source: string; target: string; route: string }[];
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
  status: NodeRunStatus;
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

export function runWorkflow(id: number, values: Record<string, unknown>) {
  return request<WorkflowRun>('POST', `/workflows/${id}/runs`, { values });
}

/** 单节点试跑：后端从开始节点真实执行到该节点为止，返回这一步的结果。 */
export type NodeProbeResult = {
  node_id: string;
  node_kind: NodeKind;
  attempt: number;
  status: NodeRunStatus;
  /** JSON 文本，可能是纯文本预览。 */
  output: string;
  error: string;
  duration_ms: number;
  route: string;
};

export function runNode(workflowId: number, nodeId: string, values: Record<string, unknown>) {
  return request<NodeProbeResult>('POST', `/workflows/${workflowId}/nodes/${encodeURIComponent(nodeId)}/run`, { values });
}

export function listRuns(params: { workflowId?: number; limit?: number; offset?: number } = {}) {
  const q = new URLSearchParams();
  if (params.workflowId != null) q.set('workflow_id', String(params.workflowId));
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

/** 解析后端下发的 JSON 文本字段（input / output / node output）。 */
export function parseJsonText(raw: string): unknown {
  if (raw.trim() === '') return null;
  try {
    return JSON.parse(raw);
  } catch {
    // 运行输出允许是纯文本（预览被截断时会带省略号），原样返回。
    return raw;
  }
}
