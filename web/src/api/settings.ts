import { request } from './client';

// 系统设置一行；sensitive=true 时列表里的 value 已脱敏，明文只走 reveal。
export type SystemSetting = {
  category: string;
  key: string;
  value: string;
  sensitive: boolean;
  updated_at?: string;
};

export type SystemSettingListResp = {
  items: SystemSetting[];
  total: number;
};

export function listSettings(category?: string): Promise<SystemSettingListResp> {
  const qs = category ? `?category=${encodeURIComponent(category)}` : '';
  return request<SystemSettingListResp>('GET', `/system-settings${qs}`);
}

export function setSetting(
  category: string,
  key: string,
  value: string,
  sensitive?: boolean
): Promise<SystemSetting> {
  const body: { value: string; sensitive?: boolean } = { value };
  if (typeof sensitive === 'boolean') body.sensitive = sensitive;
  return request<SystemSetting>(
    'PUT',
    `/system-settings/${encodeURIComponent(category)}/${encodeURIComponent(key)}`,
    body
  );
}

// 读取敏感配置明文（用于输入框显隐切换）。
export function revealSetting(category: string, key: string): Promise<{ value: string }> {
  return request<{ value: string }>(
    'GET',
    `/system-settings/${encodeURIComponent(category)}/${encodeURIComponent(key)}/reveal`
  );
}

export function fetchLLMModels(
  input: LLMConfigurationProbeInput,
): Promise<{ models: string[] }> {
  return request<{ models: string[] }>('POST', '/integrations/llm/models', input);
}

export type LLMConfigurationProbeInput = {
  tls_insecure?: boolean;
  provider: string;
  api_key: string;
  base_url: string;
  default_model: string;
  models: string[];
  // litellm 模型名前缀（如 openai/）；空串表示不加前缀
  model_prefix?: string;
  // 厂商专属请求体（JSON 对象文本）
  extra_body_json?: string;
};

export type LLMConfigurationProbeResult = {
  valid: boolean;
  code: string;
  provider: string;
  model: string;
  detail?: string;
  latency_ms: number;
  saved: boolean;
  disabled: boolean;
};

// testLLMConfiguration validates the current unsaved draft. The API key is
// used only for this request; the manager does not persist or echo it.
export function testLLMConfiguration(
  input: LLMConfigurationProbeInput,
): Promise<LLMConfigurationProbeResult> {
  return request<LLMConfigurationProbeResult>('POST', '/integrations/llm/test', input);
}

// saveLLMConfiguration asks the Manager to validate every exposed model and
// atomically persist the exact same draft. Empty api_key is stored as an
// explicit provider-disable override and does not call the upstream.
export function saveLLMConfiguration(
  input: LLMConfigurationProbeInput,
): Promise<LLMConfigurationProbeResult> {
  return request<LLMConfigurationProbeResult>('POST', '/integrations/llm/validate-and-save', input);
}
