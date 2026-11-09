import { request } from './client';
import { tr as trInline } from '@/i18n/locale';

export type AgentSource = 'builtin' | 'disk' | 'user';

/** 助理列表/详情响应字段。 */
export type AgentSummary = {
  name: string;
  description: string;
  when_to_use: string;
  tools: string[];
  disallowed_tools: string[];
  permission_mode: string;
  model: string;
  max_turns: number;
  system_prompt: string;
  critical_reminder: string;
  source: AgentSource;
};

/** 创建/更新用户自定义助理的请求体。 */
export type UserAgentInput = {
  name?: string;
  description: string;
  when_to_use: string;
  system_prompt: string;
  critical_reminder: string;
  allowed_tools: string[];
  disallowed_tools: string[];
  permission_mode: string;
  model: string;
  max_turns: number;
};

export type AgentListResp = { items: AgentSummary[]; total: number };

const BUILTIN_AGENT_I18N: Record<string, { desc: { zh: string; en: string }; whenToUse?: { zh: string; en: string } }> = {
  default: {
    desc: {
      zh: '默认助理，适合通用问答与临时排查。',
      en: 'Default assistant for general Q&A and ad-hoc troubleshooting.',
    },
  },
};

/** 按当前语言覆盖内置助理文案。 */
export function localizedAgent(a: AgentSummary): AgentSummary {
  const m = BUILTIN_AGENT_I18N[a.name];
  if (!m) return a;
  return {
    ...a,
    description: trInline(m.desc.zh, m.desc.en),
    when_to_use: m.whenToUse ? trInline(m.whenToUse.zh, m.whenToUse.en) : a.when_to_use,
  };
}

export function listAgents() {
  return request<AgentListResp>('GET', '/agents');
}

export function getAgent(name: string) {
  return request<AgentSummary>('GET', `/agents/${encodeURIComponent(name)}`);
}

export function createUserAgent(input: UserAgentInput) {
  return request<AgentSummary>('POST', '/agents/custom', input);
}

export function updateUserAgent(name: string, input: UserAgentInput) {
  return request<AgentSummary>(
    'PATCH',
    `/agents/custom/${encodeURIComponent(name)}`,
    input,
  );
}

export function deleteUserAgent(name: string) {
  return request<void>('DELETE', `/agents/custom/${encodeURIComponent(name)}`);
}

export function deleteAgent(name: string) {
  return request<void>('DELETE', `/agents/${encodeURIComponent(name)}`);
}
