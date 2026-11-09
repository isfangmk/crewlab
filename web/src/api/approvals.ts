import { getToken } from '@/store/auth';
import { request } from './client';
import { consumeSseResponse, type StreamCallbacks } from './chat';

// 审批收件箱 API：/v1/approvals/*。
// Agent 提出的危险工具调用在此等待管理员批准或拒绝。

export interface Approval {
  id: string;
  kind: string;
  title: string;
  summary: string;
  payload: string;
  source: string;
  session_id?: string;
  status: 'pending' | 'approved' | 'rejected' | 'executed' | 'failed' | 'expired';
  proposed_by: number;
  approved_by?: number;
  reason?: string;
  decision_reason?: string;
  result?: string;
  created_at: string;
  decided_at?: string;
  executed_at?: string;
}

export type ApprovalStatus = Approval['status'];

/** 审批收件箱的状态分布：与列表共用搜索/时间条件，但覆盖全部状态。 */
export type ApprovalCounts = Record<ApprovalStatus, number>;

export interface ApprovalListQuery {
  status?: ApprovalStatus;
  /** 关键字：标题 / 摘要 / 类型 / 工具名 / 会话 ID。 */
  q?: string;
  /** 提交时间范围（ISO）。 */
  created_from?: string;
  created_to?: string;
  /** 排序方向，只按提交时间；默认倒序。 */
  sort_dir?: 'asc' | 'desc';
  limit?: number;
  offset?: number;
}

export interface ApprovalListResult {
  items: Approval[];
  total: number;
  counts: ApprovalCounts;
}

export function listApprovals(params: ApprovalListQuery = {}) {
  const sp = new URLSearchParams();
  if (params.status) sp.set('status', params.status);
  if (params.q) sp.set('q', params.q);
  if (params.created_from) sp.set('created_from', params.created_from);
  if (params.created_to) sp.set('created_to', params.created_to);
  if (params.sort_dir) sp.set('sort_dir', params.sort_dir);
  if (params.limit != null) sp.set('limit', String(params.limit));
  if (params.offset != null) sp.set('offset', String(params.offset));
  const qs = sp.toString();
  return request<ApprovalListResult>('GET', `/approvals${qs === '' ? '' : `?${qs}`}`);
}

export function approvalsPendingCount() {
  return request<{ pending: number }>('GET', '/approvals/count');
}

export function getApproval(id: string) {
  return request<Approval>('GET', `/approvals/${id}`);
}

export function approveApproval(id: string, reason = '') {
  return request<Approval>('POST', `/approvals/${id}/approve`, { reason });
}

export function batchDecideApprovals(ids: string[], confirmed: boolean, reason = '') {
  if (!confirmed && reason === '') {
    throw new Error('拒绝原因 reason 必填');
  }
  return request<{ items: Approval[] }>('POST', '/approvals/batch', {
    ids,
    confirmed,
    reason,
  });
}

/** 批准并续跑：SSE 增量，供会话内打字机渲染。 */
export async function streamApproveApproval(
  id: string,
  reason: string,
  cbs: StreamCallbacks,
  signal?: AbortSignal,
): Promise<Approval | null> {
  const headers: Record<string, string> = {
    Accept: 'text/event-stream',
    'Content-Type': 'application/json',
  };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let approval: Approval | null = null;
  const res = await fetch(`/api/v1/approvals/${encodeURIComponent(id)}/approve/stream`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ reason }),
    signal,
  });
  await consumeSseResponse(res, {
    ...cbs,
    onApprovalDone: (payload) => {
      approval = payload as Approval;
      cbs.onApprovalDone?.(payload);
    },
  });
  return approval;
}

export function rejectApproval(id: string, reason: string) {
  return request<{ ok: boolean }>('POST', `/approvals/${id}/reject`, { reason });
}
