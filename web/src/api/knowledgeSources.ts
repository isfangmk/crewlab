// 外部知识源：URL / Git 接入配置、同步状态与触发同步。
import { request } from './client';

export type KnowledgeSourceKind = 'url' | 'git';
export type KnowledgeSourceSyncStatus = 'idle' | 'syncing' | 'ok' | 'error';

export type KnowledgeSource = {
  id: string;
  name: string;
  kind: KnowledgeSourceKind;
  location: string;
  branch: string;
  path_prefix: string;
  knowledge_path: string;
  enabled: boolean;
  sync_status: KnowledgeSourceSyncStatus;
  last_error: string;
  last_synced_at: string | null;
  last_duration_ms: number | null;
  document_count: number;
  created_at: string | null;
  updated_at: string | null;
};

export type KnowledgeSourceInput = {
  name: string;
  kind: KnowledgeSourceKind;
  location: string;
  branch: string;
  path_prefix: string;
  knowledge_path: string;
  enabled: boolean;
};

export function listKnowledgeSources(params?: {
  q?: string;
  kind?: KnowledgeSourceKind;
}) {
  const query = new URLSearchParams();
  if (params?.q) query.set('q', params.q);
  if (params?.kind) query.set('kind', params.kind);
  const qs = query.toString();
  return request<{ items: KnowledgeSource[]; total: number }>(
    'GET',
    `/knowledge/sources${qs ? `?${qs}` : ''}`,
  );
}

export function createKnowledgeSource(input: KnowledgeSourceInput) {
  return request<KnowledgeSource>('POST', '/knowledge/sources', input);
}

export function updateKnowledgeSource(
  id: string,
  input: Partial<KnowledgeSourceInput>,
) {
  return request<KnowledgeSource>('PATCH', `/knowledge/sources/${id}`, input);
}

export function deleteKnowledgeSource(id: string) {
  return request<{ ok: boolean }>('DELETE', `/knowledge/sources/${id}`);
}

export function syncKnowledgeSource(id: string) {
  return request<KnowledgeSource>(
    'POST',
    `/knowledge/sources/${id}/sync`,
  );
}
