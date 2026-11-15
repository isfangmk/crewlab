import { request } from './client';

/** MCP Server 列表/详情响应字段。 */
export type McpApprovalPolicy = 'never' | 'non_readonly' | 'always';

export interface McpServer {
  id: number;
  name: string;
  transport: 'http' | 'sse';
  endpoint: string;
  credential: string;
  header_template_json: string;
  approval_policy: McpApprovalPolicy;
  enabled: boolean;
  status: string;
  updated_at?: string | null;
  last_error: string;
  tools_cache_json: string;
  resources_count?: number;
  prompts_count?: number;
  last_probed_at?: string | null;
  /** OAuth/手动 Token 授权态：none | authorized | expired。 */
  oauth_status?: 'none' | 'authorized' | 'expired';
  embedding_sync_status?: string;
  embedding_sync_count?: number;
  embedding_sync_error?: string;
  embedding_synced_at?: string | null;
}

/** JSON Schema 属性（MCP input_schema.properties 单项）。 */
export type McpJsonSchema = {
  type?: string | string[];
  description?: string;
  default?: unknown;
  items?: McpJsonSchema;
  anyOf?: McpJsonSchema[];
  properties?: Record<string, McpJsonSchema>;
  required?: string[];
  [key: string]: unknown;
};

export interface McpTool {
  name: string;
  description: string;
  input_schema: McpJsonSchema;
}

/** 创建/更新 MCP Server 的请求体。 */
export interface McpServerInput {
  name: string;
  transport: 'http' | 'sse';
  endpoint: string;
  credential: string;
  header_template_json: string;
  approval_policy: McpApprovalPolicy;
  enabled: boolean;
}

export type McpServerListResp = {
  items: McpServer[];
  total: number;
};

export type McpTestResp = {
  tools: McpTool[];
  count: number;
};

export type McpToolRunResp = {
  tool: string;
  is_error: boolean;
  result_text: string;
};

/** 解析 tools_cache_json；结构不符则显式抛错。 */
export function parseToolsCache(raw: string): McpTool[] {
  const arr = JSON.parse(raw) as unknown;
  if (!Array.isArray(arr)) {
    throw new Error('tools_cache_json 必须是数组');
  }
  return arr.map((item, index) => {
    if (!item || typeof item !== 'object') {
      throw new Error(`tools_cache_json[${index}] 必须是对象`);
    }
    const row = item as Record<string, unknown>;
    if (typeof row.name !== 'string' || row.name === '') {
      throw new Error(`tools_cache_json[${index}].name 无效`);
    }
    if (typeof row.description !== 'string') {
      throw new Error(`tools_cache_json[${index}].description 必须是字符串`);
    }
    if (!row.input_schema || typeof row.input_schema !== 'object' || Array.isArray(row.input_schema)) {
      throw new Error(`tools_cache_json[${index}].input_schema 必须是对象（请重新「测试连接」）`);
    }
    return {
      name: row.name,
      description: row.description,
      input_schema: row.input_schema as McpJsonSchema,
    };
  });
}

export async function listMcpServers(): Promise<McpServer[]> {
  const r = await request<McpServerListResp>('GET', '/mcp/servers');
  if (!Array.isArray(r.items)) {
    throw new Error('MCP 列表缺少 items');
  }
  return r.items;
}

export async function createMcpServer(input: McpServerInput): Promise<McpServer> {
  return request<McpServer>('POST', '/mcp/servers', input);
}

export async function updateMcpServer(id: number, input: McpServerInput): Promise<void> {
  await request<{ ok: boolean }>('PUT', `/mcp/servers/${id}`, input);
}

export async function deleteMcpServer(id: number): Promise<void> {
  await request<void>('DELETE', `/mcp/servers/${id}`);
}

export async function testMcpServer(id: number): Promise<McpTool[]> {
  const r = await request<McpTestResp>('POST', `/mcp/servers/${id}/test`);
  if (!Array.isArray(r.tools)) {
    throw new Error('MCP 探测响应缺少 tools');
  }
  return r.tools.map((item, index) => {
    if (!item || typeof item !== 'object') {
      throw new Error(`tools[${index}] 必须是对象`);
    }
    if (typeof item.name !== 'string' || item.name === '') {
      throw new Error(`tools[${index}].name 无效`);
    }
    if (typeof item.description !== 'string') {
      throw new Error(`tools[${index}].description 必须是字符串`);
    }
    if (!item.input_schema || typeof item.input_schema !== 'object' || Array.isArray(item.input_schema)) {
      throw new Error(`tools[${index}].input_schema 必须是对象`);
    }
    return item;
  });
}

export async function runMcpTool(
  serverId: number,
  toolName: string,
  arguments_: Record<string, unknown>,
): Promise<McpToolRunResp> {
  const r = await request<McpToolRunResp>(
    'POST',
    `/mcp/servers/${serverId}/tools/${encodeURIComponent(toolName)}/run`,
    { arguments: arguments_ },
  );
  if (typeof r.tool !== 'string' || typeof r.result_text !== 'string' || typeof r.is_error !== 'boolean') {
    throw new Error('MCP 运行响应字段不完整');
  }
  return r;
}

/** MCP 资源清单项（resources/list）。 */
export interface McpResource {
  uri: string;
  name: string;
  description: string;
  mime_type: string;
}

/** MCP 资源模板项（resources/templates/list）。 */
export interface McpResourceTemplate {
  uri_template: string;
  name: string;
  description: string;
  mime_type: string;
}

/** MCP 提示词参数定义。 */
export interface McpPromptArgument {
  name: string;
  description: string;
  required: boolean;
}

/** MCP 提示词清单项（prompts/list）。 */
export interface McpPrompt {
  name: string;
  description: string;
  arguments: McpPromptArgument[];
}

/** 资源读取内容块；文本走 text，二进制走 blob（base64）。 */
export interface McpResourceContent {
  uri: string;
  mime_type: string;
  text?: string;
  blob?: string;
}

export interface McpResourceReadResp {
  uri: string;
  contents: McpResourceContent[];
}

/** 提示词展开后的单条消息。 */
export interface McpPromptMessage {
  role: string;
  text: string;
}

export interface McpPromptGetResp {
  name: string;
  description: string;
  messages: McpPromptMessage[];
}

/** Registry 搜索结果项：已映射为登记表单预填结构。 */
export interface McpRegistryItem {
  name: string;
  description: string;
  transport: 'http' | 'sse';
  endpoint: string;
  hint: string;
}

export interface McpRegistrySearchResp {
  items: McpRegistryItem[];
  next_cursor: string;
  total: number;
}

export async function listMcpResources(serverId: number): Promise<McpResource[]> {
  const r = await request<{ items: McpResource[]; total: number }>(
    'GET',
    `/mcp/servers/${serverId}/resources`,
  );
  if (!Array.isArray(r.items)) {
    throw new Error('MCP 资源响应缺少 items');
  }
  return r.items;
}

export async function readMcpResource(serverId: number, uri: string): Promise<McpResourceReadResp> {
  const r = await request<McpResourceReadResp>(
    'GET',
    `/mcp/servers/${serverId}/resources/read?uri=${encodeURIComponent(uri)}`,
  );
  if (!Array.isArray(r.contents)) {
    throw new Error('MCP 资源读取响应缺少 contents');
  }
  return r;
}

export async function listMcpResourceTemplates(serverId: number): Promise<McpResourceTemplate[]> {
  const r = await request<{ items: McpResourceTemplate[]; total: number }>(
    'GET',
    `/mcp/servers/${serverId}/resource-templates`,
  );
  if (!Array.isArray(r.items)) {
    throw new Error('MCP 资源模板响应缺少 items');
  }
  return r.items;
}

export async function listMcpPrompts(serverId: number): Promise<McpPrompt[]> {
  const r = await request<{ items: McpPrompt[]; total: number }>(
    'GET',
    `/mcp/servers/${serverId}/prompts`,
  );
  if (!Array.isArray(r.items)) {
    throw new Error('MCP 提示词响应缺少 items');
  }
  return r.items;
}

export async function getMcpPrompt(
  serverId: number,
  name: string,
  args: Record<string, string>,
): Promise<McpPromptGetResp> {
  const r = await request<McpPromptGetResp>(
    'POST',
    `/mcp/servers/${serverId}/prompts/get`,
    { name, arguments: args },
  );
  if (!Array.isArray(r.messages)) {
    throw new Error('MCP 提示词响应缺少 messages');
  }
  return r;
}

export async function searchMcpRegistry(
  search: string,
  limit = 50,
  cursor = '',
): Promise<McpRegistrySearchResp> {
  const params = new URLSearchParams();
  if (search !== '') params.set('search', search);
  params.set('limit', String(limit));
  if (cursor !== '') params.set('cursor', cursor);
  const r = await request<McpRegistrySearchResp>('GET', `/mcp/registry/servers?${params.toString()}`);
  if (!Array.isArray(r.items)) {
    throw new Error('Registry 搜索响应缺少 items');
  }
  return r;
}

/** OAuth/手动 Token 授权状态。 */
export interface McpOauthStatus {
  authorized: boolean;
  auth_source: 'oauth' | 'manual' | null;
  expires_at: string | null;
  expired: boolean;
}

export async function startMcpOauth(serverId: number): Promise<{ authorize_url: string }> {
  const r = await request<{ authorize_url: string }>(
    'POST',
    `/mcp/servers/${serverId}/oauth/start`,
  );
  if (typeof r.authorize_url !== 'string' || r.authorize_url === '') {
    throw new Error('OAuth 授权响应缺少 authorize_url');
  }
  return r;
}

export async function getMcpOauthStatus(serverId: number): Promise<McpOauthStatus> {
  return request<McpOauthStatus>('GET', `/mcp/servers/${serverId}/oauth/status`);
}

export async function saveMcpOauthToken(
  serverId: number,
  input: { access_token: string; refresh_token?: string; expires_in?: number },
): Promise<McpOauthStatus> {
  return request<McpOauthStatus>('POST', `/mcp/servers/${serverId}/oauth/token`, input);
}

export async function revokeMcpOauth(serverId: number): Promise<void> {
  await request<{ ok: boolean }>('DELETE', `/mcp/servers/${serverId}/oauth`);
}
