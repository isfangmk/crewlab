// 分享链接管理：统一会话分享与评测分享的列表与撤销。
// 安全口径：库里只存 token 哈希，原链接无法回显——要链接就
// 用创建端点再生成一条新分享（旧链接可单独撤销）。

import { ApiError, request } from './client';

export type ShareKind = 'session' | 'eval';

export type ShareItem = {
  id: number;
  kind: ShareKind;
  target_id: string;
  title: string;
  created_by: number | null;
  creator: string | null;
  created_at: string;
  expires_at: string;
  expired: boolean;
};

export function listShares(params?: {
  kind?: 'all' | ShareKind;
  status?: 'all' | 'active' | 'expired';
  q?: string;
  sort_by?: 'created_at' | 'expires_at';
  sort_dir?: 'asc' | 'desc';
  limit?: number;
  offset?: number;
}) {
  const sp = new URLSearchParams();
  if (params?.kind != null && params.kind !== 'all') sp.set('kind', params.kind);
  if (params?.status != null && params.status !== 'all') sp.set('status', params.status);
  if (params?.q != null && params.q !== '') sp.set('q', params.q);
  if (params?.sort_by != null) sp.set('sort_by', params.sort_by);
  if (params?.sort_dir != null) sp.set('sort_dir', params.sort_dir);
  if (params?.limit != null) sp.set('limit', String(params.limit));
  if (params?.offset != null) sp.set('offset', String(params.offset));
  const qs = sp.toString();
  return request<{ items: ShareItem[]; total: number }>('GET', `/shares${qs ? `?${qs}` : ''}`);
}

export function revokeShare(kind: ShareKind, shareId: number) {
  if (!Number.isInteger(shareId) || shareId < 1) {
    return Promise.reject(new ApiError('shareId 非法', 422));
  }
  return request<{ ok: boolean }>('DELETE', `/shares/${kind}/${shareId}`);
}
