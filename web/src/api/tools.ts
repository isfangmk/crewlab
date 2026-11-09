import { request } from './client';

export type BuiltinToolParam = {
  name: string;
  type: string;
  required?: boolean;
  default?: unknown;
  desc?: string;
  enum?: string[];
};

export type BuiltinTool = {
  name: string;
  description: string;
  risk: string;
  params: BuiltinToolParam[];
};

export type BuiltinToolList = {
  items: BuiltinTool[];
  total: number;
};

export type BuiltinToolRunResult = {
  tool: string;
  result: unknown;
};

/** 拉取平台自带 Tool 列表（不是 Skill）。 */
export function listBuiltinTools() {
  return request<BuiltinToolList>('GET', '/tools/builtin');
}

/** 手动运行内置 Tool（探测用）。 */
export function runBuiltinTool(name: string, params: Record<string, unknown>) {
  return request<BuiltinToolRunResult>(
    'POST',
    `/tools/builtin/${encodeURIComponent(name)}/run`,
    { params },
  );
}
