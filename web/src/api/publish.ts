// 对外发布渠道：OpenAI 兼容 API 的 Key 与用量。
import { request } from './client';

export type ChannelUsage = {
  turns: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  last_used_at: string | null;
};

export type PublishedChannel = {
  id: string;
  name: string;
  agent_id: string;
  provider: string;
  model: string;
  /** 列表里只展示前缀 + 省略号 */
  key_masked: string;
  enabled: boolean;
  expires_at: string | null;
  session_id: string | null;
  created_at: string | null;
  usage: ChannelUsage;
};

export type ChannelCreateInput = {
  name: string;
  agent_id: string;
  /** 留空 = 跟随全局默认模型 */
  provider: string;
  model: string;
  /** 0 = 永不过期 */
  expires_in_hours: number;
};

export type ChannelCreateResult = PublishedChannel & { key: string };

export function listChannels() {
  return request<{ items: PublishedChannel[]; total: number }>('GET', '/publish/channels');
}

export function createChannel(input: ChannelCreateInput) {
  return request<ChannelCreateResult>('POST', '/publish/channels', input);
}

export function rotateChannelKey(id: string) {
  return request<{ key: string; channel: PublishedChannel }>(
    'POST',
    `/publish/channels/${encodeURIComponent(id)}/rotate`,
  );
}

export function setChannelEnabled(id: string, enabled: boolean) {
  return request<PublishedChannel>(
    'PATCH',
    `/publish/channels/${encodeURIComponent(id)}`,
    { enabled },
  );
}

export function deleteChannel(id: string) {
  return request<{ ok: boolean }>('DELETE', `/publish/channels/${encodeURIComponent(id)}`);
}
