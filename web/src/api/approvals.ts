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
  status: 'pending' | 'approved' | 'rejected' | 'executed' | 'failed';
  proposed_by: number;
  approved_by?: number;
  reason?: string;
  result?: string;
  created_at: string;
  decided_at?: string;
  executed_at?: string;
}

export function listApprovals(status?: string) {
  const qs = status ? `?status=${encodeURIComponent(status)}` : '';
  return request<{ items: Approval[] }>('GET', `/approvals${qs}`);
}

export function approvalsPendingCount() {
  return request<{ pending: number }>('GET', '/approvals/count');
}

export function getApproval(id: string) {
  return request<Approval>('GET', `/approvals/${id}`);
}

export function approveApproval(id: string) {
  return request<Approval>('POST', `/approvals/${id}/approve`);
}

/** 批准并续跑：SSE 增量，供会话内打字机渲染。 */
export async function streamApproveApproval(
  id: string,
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
