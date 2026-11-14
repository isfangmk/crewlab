// 会话 Artifact：列表 / 详情 / 下载。
import { request, requestFile } from './client';

export type ArtifactKind = 'chart' | 'table' | 'file' | 'text';

export interface ArtifactItem {
  id: string;
  session_id: string | null;
  tool_call_id: string;
  kind: ArtifactKind;
  title: string;
  mime: string;
  payload: unknown;
  file_name: string;
  size: number;
  created_at: string | null;
}

export function listSessionArtifacts(sessionId: string) {
  return request<{ items: ArtifactItem[] }>(
    'GET',
    `/chat/sessions/${sessionId}/artifacts`,
  );
}

export function downloadArtifact(id: string, shareToken?: string) {
  if (shareToken) {
    return requestFile(
      `/chat/shares/${encodeURIComponent(shareToken)}/artifacts/${encodeURIComponent(id)}/download`,
    );
  }
  return requestFile(`/artifacts/${id}/download`);
}

export function getArtifact(id: string) {
  return request<ArtifactItem>('GET', `/artifacts/${id}`);
}
