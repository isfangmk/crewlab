// audit.ts — 审计日志查询与脱敏导出（/v1/audit，仅管理员）。
import { request, requestFile } from './client';

export type AuditItem = {
  id: number;
  actor_user_id: number | null;
  actor_email: string | null;
  action: string;
  resource_type: string;
  resource_id: string;
  detail: Record<string, unknown>;
  request_id: string | null;
  created_at: string;
};

export type AuditListQuery = {
  action?: string;
  resource_type?: string;
  resource_id?: string;
  actor_user_id?: number;
  created_from?: string;
  created_to?: string;
  sort_dir?: 'asc' | 'desc';
  limit?: number;
  offset?: number;
};

export type AuditListResult = {
  items: AuditItem[];
  total: number;
  actions: string[];
  resource_types: string[];
};

function buildQuery(query: AuditListQuery): URLSearchParams {
  const params = new URLSearchParams();
  if (query.action) params.set('action', query.action);
  if (query.resource_type) params.set('resource_type', query.resource_type);
  if (query.resource_id) params.set('resource_id', query.resource_id);
  if (query.actor_user_id !== undefined) params.set('actor_user_id', String(query.actor_user_id));
  if (query.created_from) params.set('created_from', query.created_from);
  if (query.created_to) params.set('created_to', query.created_to);
  if (query.sort_dir) params.set('sort_dir', query.sort_dir);
  if (query.limit !== undefined) params.set('limit', String(query.limit));
  if (query.offset !== undefined) params.set('offset', String(query.offset));
  return params;
}

export function listAudit(query: AuditListQuery = {}): Promise<AuditListResult> {
  const qs = buildQuery(query).toString();
  return request<AuditListResult>('GET', `/audit${qs ? `?${qs}` : ''}`);
}

// 导出 CSV：与列表同一套筛选口径；后端恒为脱敏导出（密钥类字段与 URL 凭证不出现在文件里）。
export function exportAudit(query: Omit<AuditListQuery, 'offset'> = {}): Promise<{
  blob: Blob;
  filename: string;
}> {
  const params = buildQuery(query);
  return requestFile('/audit/export', params);
}
