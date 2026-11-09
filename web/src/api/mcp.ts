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
  trusted: boolean;
  approval_policy: McpApprovalPolicy;
  enabled: boolean;
  status: string;
  last_error: string;
  tools_cache_json: string;
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
  trusted: boolean;
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
