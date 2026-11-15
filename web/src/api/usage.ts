import { request } from './client';

/** 一个分组（日 / 模型 / 助理）的用量与成本。cost=null = 有未计价回合，不给半真合计。 */
export type UsageRow = {
  key: string;
  turns: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  duration_avg_ms: number | null;
  cost: number | null;
  unpriced_turns: number;
};

export type UsageStats = UsageRow & {
  unpriced_tokens: number;
};

export type UsageFacets = {
  models: string[];
  agents: string[];
  channels: string[];
};

export type UsageSummary = {
  stats: UsageStats;
  /** key = YYYY-MM-DD（配置时区日界），升序 */
  by_day: UsageRow[];
  /** key = 模型名，按总 token 降序 */
  by_model: UsageRow[];
  /** key = 助理 id，按总 token 降序 */
  by_agent: UsageRow[];
  /** 筛选下拉候选值：当前结果集里出现过的维度值 */
  facets: UsageFacets;
  /** 日 token 软限额；null = 不限。超限只提醒不拦截 */
  soft_limit_tokens: number | null;
  today_tokens: number;
};

export type UsageFilters = {
  started_from?: string;
  started_to?: string;
  agent_id?: string;
  model?: string;
  channel?: string;
};

export function getUsageSummary(filters: UsageFilters = {}): Promise<UsageSummary> {
  const params = new URLSearchParams();
  if (filters.started_from) params.set('started_from', filters.started_from);
  if (filters.started_to) params.set('started_to', filters.started_to);
  if (filters.agent_id) params.set('agent_id', filters.agent_id);
  if (filters.model) params.set('model', filters.model);
  if (filters.channel) params.set('channel', filters.channel);
  const qs = params.toString();
  return request<UsageSummary>('GET', `/usage/summary${qs ? `?${qs}` : ''}`);
}
