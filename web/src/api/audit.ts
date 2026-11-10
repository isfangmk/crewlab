// audit.ts — 审计日志查询与导出（/v1/audit，仅管理员）。
import { getToken } from '@/store/auth';
import { request } from './client';

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

export function listAudit(query: AuditListQuery = {}): Promise<AuditListResult> {
  const qs = buildQuery(query);
  return request<AuditListResult>('GET', `/audit${qs ? `?${qs}` : ''}`);
}

function buildQuery(query: AuditListQuery): string {
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
  return params.toString();
}

// 导出 CSV：走同一条筛选口径，文件名由后端 Content-Disposition 给出。
export async function exportAudit(query: Omit<AuditListQuery, 'offset'> = {}): Promise<Blob> {
  const qs = buildQuery(query);
  const token = getToken();
  const res = await fetch(`/api/v1/audit/export${qs ? `?${qs}` : ''}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error(`导出失败: HTTP ${res.status}`);
  return res.blob();
}
