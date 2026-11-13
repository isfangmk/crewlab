import { request } from './client';

/** 沙盒执行来源：技能脚本 / 工作流代码节点 / 手动试跑。 */
export type SandboxSource = 'skill' | 'workflow' | 'manual';

/** 执行结果：成功 / 失败 / 超时。 */
export type SandboxStatus = 'succeeded' | 'failed' | 'timeout';

export type SandboxExecution = {
  id: string;
  source: SandboxSource;
  /** 触发方的具体标识：技能名 / 节点 id / 手动试跑。 */
  source_ref: string;
  session_id: string;
  workflow_run_id: string;
  language: string;
  image: string;
  network_enabled: boolean;
  code: string;
  stdout: string;
  stderr: string;
  exit_code: number | null;
  status: SandboxStatus;
  error: string | null;
  duration_ms: number;
  started_at: string | null;
  finished_at: string | null;
};

export type SandboxExecutionList = {
  items: SandboxExecution[];
  total: number;
};

export type SandboxSummary = {
  total: number;
  by_source: Partial<Record<SandboxSource, number>>;
  by_status: Partial<Record<SandboxStatus, number>>;
  avg_duration_ms: number;
};

export type SandboxRunResult = {
  execution_id: string;
  stdout: string;
  stderr: string;
  exit_code: number | null;
  status: SandboxStatus;
  error: string;
  duration_ms: number;
};

export type SandboxListParams = {
  query?: string;
  source?: SandboxSource | '';
  status?: SandboxStatus | '';
  limit?: number;
  offset?: number;
};

/** 执行记录列表：筛选与分页都在服务端做，记录涨得快，前端不拉全表。 */
export function listSandboxExecutions(params: SandboxListParams = {}) {
  const q = new URLSearchParams();
  if (params.query != null && params.query.trim() !== '') q.set('q', params.query.trim());
  if (params.source != null && params.source !== '') q.set('source', params.source);
  if (params.status != null && params.status !== '') q.set('status', params.status);
  q.set('limit', String(params.limit ?? 20));
  q.set('offset', String(params.offset ?? 0));
  return request<SandboxExecutionList>('GET', `/sandbox/executions?${q.toString()}`);
}

export function getSandboxExecution(executionId: string) {
  return request<SandboxExecution>('GET', `/sandbox/executions/${encodeURIComponent(executionId)}`);
}

/** 概览：各来源 / 各状态的数量与平均耗时。 */
export function getSandboxSummary() {
  return request<SandboxSummary>('GET', '/sandbox/summary');
}

/**
 * 手动跑一段代码。
 *
 * 沙盒默认断网，此时传 libraries 会被后端明确拒绝：
 * pip 装依赖需要联网，与其在容器里报一堆网络错误，不如在提交前说清楚。
 */
export function runSandboxCode(code: string, libraries: string[] = []) {
  return request<SandboxRunResult>('POST', '/sandbox/run', { code, libraries });
}
