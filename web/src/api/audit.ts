// audit.ts — 审计日志查询（/v1/audit，仅管理员）。
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
  const qs = params.toString();
  return request<AuditListResult>('GET', `/audit${qs ? `?${qs}` : ''}`);
}
