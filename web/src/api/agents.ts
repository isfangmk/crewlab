import { request } from './client';
import { tr as trInline } from '@/i18n/locale';
import { getToken } from '@/store/auth';
import { consumeSseResponse, type StreamCallbacks } from './chat';

export type AgentSource = 'builtin' | 'disk' | 'user';

/** 输出格式约束：free = 不限制；json / checklist 由服务端生成固定文案。 */
export type OutputFormat = 'free' | 'json' | 'checklist';

export const OUTPUT_FORMATS: OutputFormat[] = ['free', 'json', 'checklist'];

/** 少样本示例（最多 3 条，服务端校验）。 */
export type AgentExample = { input: string; output: string };

/** 助理可检索的知识范围；两者都为空表示继承全部知识库。 */
export type KnowledgeScope = { paths: string[]; doc_ids: string[] };

/** 助理列表/详情响应字段。 */
export type AgentSummary = {
  /** 助理唯一标识（Agent ID），用于接口、会话与委托引用 */
  name: string;
  /** 面向用户的显示名称（Display Name），可使用中文 */
  display_name: string;
  description: string;
  when_to_use: string;
  tools: string[];
  disallowed_tools: string[];
  permission_mode: string;
  model: string;
  max_turns: number;
  /** 工具结果回喂模型的字符上限；null = 继承全局默认 */
  max_tool_result_chars: number | null;
  /** 上下文压缩触发 token 阈值；null = 继承全局默认，0 = 关闭 */
  context_compaction_threshold: number | null;
  system_prompt: string;
  critical_reminder: string;
  output_format: OutputFormat;
  examples: AgentExample[];
  knowledge_scope: KnowledgeScope;
  /** 勾选的技能 key；null = 继承全部技能 */
  skills: string[] | null;
  /** 可委托的子助理 name 列表；空 = 不委托 */
  sub_agents: string[];
  source: AgentSource;
  /** 当前配置版本号；内置助理或未快照时为 0 */
  version: number;
  updated_at: string | null;
};

/** 创建/更新用户自定义助理的请求体。 */
export type UserAgentInput = {
  name?: string;
  display_name: string;
  description: string;
  when_to_use: string;
  system_prompt: string;
  critical_reminder: string;
  allowed_tools: string[];
  disallowed_tools: string[];
  permission_mode: string;
  model: string;
  max_turns: number;
  /** 工具结果上限；null/缺省 = 继承全局默认 */
  max_tool_result_chars?: number | null;
  /** 上下文压缩阈值；null/缺省 = 继承全局默认，0 = 关闭 */
  context_compaction_threshold?: number | null;
  output_format: OutputFormat;
  examples: AgentExample[];
  knowledge_scope: KnowledgeScope;
  /** null = 继承全部技能；数组 = 只装配这些（空数组 = 不装配任何技能） */
  skills: string[] | null;
  /** 子助理委托：name 列表；空数组 = 不委托 */
  sub_agents: string[];
  /** 本次保存的变更摘要，只进版本快照，不进助理配置本身 */
  change_summary?: string;
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
  if (a.name === 'default') {
    return {
      ...a,
      display_name: trInline('默认助理', 'Default'),
      description: trInline(
        BUILTIN_AGENT_I18N.default.desc.zh,
        BUILTIN_AGENT_I18N.default.desc.en,
      ),
    };
  }
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

/** 版本列表项（不含快照正文）。 */
export type AgentVersionMeta = {
  version: number;
  change_summary: string;
  created_by: number | null;
  created_at: string | null;
  restored_from_version: number | null;
};

/** 版本详情（含完整配置快照）。 */
export type AgentVersionDetail = AgentVersionMeta & {
  snapshot: UserAgentInput;
};

export function listAgentVersions(name: string) {
  return request<{ items: AgentVersionMeta[]; total: number }>(
    'GET',
    `/agents/custom/${encodeURIComponent(name)}/versions`,
  );
}

export function getAgentVersion(name: string, version: number) {
  return request<AgentVersionDetail>(
    'GET',
    `/agents/custom/${encodeURIComponent(name)}/versions/${version}`,
  );
}

export function rollbackAgent(name: string, version: number, changeSummary = '') {
  return request<AgentSummary>(
    'POST',
    `/agents/custom/${encodeURIComponent(name)}/rollback`,
    { version, change_summary: changeSummary },
  );
}

/** Playground 试跑入参：草稿助理 + 一轮输入 + 运行开关。 */
export type PlaygroundRunInput = {
  agent: UserAgentInput;
  content: string;
  provider?: string;
  model?: string;
  thinkingEnabled?: boolean;
  webSearchEnabled?: boolean;
  locale?: string;
};

/** 草稿助理试跑：不落会话、不落观测，SSE 帧与正式会话同构。 */
export async function playgroundStream(
  input: PlaygroundRunInput,
  cbs: StreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const headers: Record<string, string> = {
    Accept: 'text/event-stream',
    'Content-Type': 'application/json',
  };
  const token = getToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch('/api/v1/agents/playground/stream', {
    method: 'POST',
    headers,
    body: JSON.stringify({
      agent: input.agent,
      content: input.content,
      provider: input.provider ?? '',
      model: input.model ?? '',
      thinking_enabled: input.thinkingEnabled ?? false,
      web_search_enabled: input.webSearchEnabled ?? false,
      locale: input.locale ?? null,
    }),
    signal,
  });
  await consumeSseResponse(res, cbs);
}
