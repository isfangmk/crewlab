import { request } from './client';

export type BuiltinTool = {
  name: string;
  description: string;
  risk: string;
};

export type BuiltinToolList = {
  items: BuiltinTool[];
  total: number;
};

/** 拉取平台自带 Tool 列表（不是 Skill）。 */
export function listBuiltinTools() {
  return request<BuiltinToolList>('GET', '/tools/builtin');
}
