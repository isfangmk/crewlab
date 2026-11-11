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
  group: string;
  description: string;
  risk: string;
  /** always = 随 SkillToolset 始终装配；per_turn = 由对话开关（如联网）决定 */
  mount: 'always' | 'per_turn';
  params: BuiltinToolParam[];
};

export type BuiltinToolGroup = {
  key: string;
  title: string;
  title_en: string;
};

export type BuiltinToolList = {
  items: BuiltinTool[];
  groups: BuiltinToolGroup[];
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
