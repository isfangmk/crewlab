// 自动化触发器 API：CRUD / 手动触发 / 触发记录。
import { request } from './client';

export interface TriggerRuleItem {
  id: number;
  name: string;
  kind: 'cron' | 'webhook';
  cron_expr: string;
  target_kind: 'workflow' | 'agent';
  workflow_id: number | null;
  agent_id: string | null;
  prompt: string;
  enabled: boolean;
  max_retries: number;
  has_webhook: boolean;
  next_fire_at: string | null;
  last_fired_at: string | null;
  created_at: string | null;
}

export interface TriggerCreateInput {
  name: string;
  kind: 'cron' | 'webhook';
  target_kind: 'workflow' | 'agent';
  cron_expr?: string;
  workflow_id?: number;
  agent_id?: string;
  prompt?: string;
  enabled?: boolean;
  max_retries?: number;
}

export interface TriggerRunItem {
  id: number;
  status: 'running' | 'waiting' | 'succeeded' | 'failed';
  attempt: number;
  error: string;
  ref_id: string;
  started_at: string | null;
  finished_at: string | null;
}

export function listTriggers() {
  return request<{ items: TriggerRuleItem[]; total: number }>('GET', '/triggers');
}

export function createTrigger(input: TriggerCreateInput) {
  return request<Record<string, unknown>>('POST', '/triggers', input);
}

export function updateTrigger(id: number, patch: Partial<TriggerCreateInput>) {
  return request<TriggerRuleItem>('PUT', `/triggers/${id}`, patch);
}

export function deleteTrigger(id: string | number) {
  return request<{ ok: boolean }>('DELETE', `/triggers/${id}`);
}

export function listTriggerRuns(id: number) {
  return request<{ items: TriggerRunItem[]; total: number }>(
    'GET',
    `/triggers/${id}/runs`,
  );
}

export function fireTrigger(id: number) {
  return request<TriggerRunItem>('POST', `/triggers/${id}/fire`);
}
