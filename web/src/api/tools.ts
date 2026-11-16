import { request } from './client';

/** 工具风险标签：三级危险度（safe / mutating / dangerous），内置与 MCP 工具共用同一词汇。 */
export type ToolRisk = 'safe' | 'mutating' | 'dangerous';

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
  risk: ToolRisk;
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
