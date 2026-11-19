import { request, requestFile } from './client';
import { saveBlob } from '@/lib/download';
import { tr as trInline } from '@/i18n/locale';

export type SkillClass = 'safe' | 'mutating' | 'dangerous';

/** 技能来源类型：builtin = 随包发布；created = 平台内新建；imported = zip 导入。 */
export type SkillOrigin = 'builtin' | 'created' | 'imported';

export const SKILL_CLASSES: SkillClass[] = ['safe', 'mutating', 'dangerous'];

/**
 * 技能分组：固定枚举，与内置工具分组同一套语义。
 * 技能目录页的筛选条与分组表都按它渲染，不再有自由文本分类。
 */
export type SkillGroup =
  | 'observe'
  | 'device'
  | 'fleet'
  | 'incident'
  | 'knowledge'
  | 'messaging'
  | 'cloud'
  | 'chart'
  | 'other';

export const SKILL_GROUPS: SkillGroup[] = [
  'observe',
  'device',
  'fleet',
  'incident',
  'knowledge',
  'messaging',
  'cloud',
  'chart',
  'other',
];

/** 分组目录项：key + 中英文标题（标题跟随 UI 语言）。 */
export type SkillGroupMeta = { key: SkillGroup; title: string; title_en: string };

/** 技能包里的一个资源文件；详情/编辑接口会带上 text 或 base64。 */
export type SkillFile = {
  path: string;
  size: number;
  binary: boolean;
  text?: string;
  base64?: string;
};

export type SkillSummary = {
  key: string;
  name: string;
  description: string;
  class: SkillClass;
  // 分组（固定枚举）：驱动技能目录页的筛选条与分组表
  group?: SkillGroup;
  // disk = backend/skills 内置；user = 平台内创建 / 导入
  source?: 'disk' | 'user';
  // 来源类型：内置 / 平台内新建 / zip 导入
  origin?: SkillOrigin;
  // frontmatter 的 allowed-tools（ADK 实验字段，本平台不据此放行审批）
  preapproved_tools?: string;
  inject_state?: boolean;
  // SKILL.md 正文
  instructions?: string;
  references?: SkillFile[];
  assets?: SkillFile[];
  scripts?: SkillFile[];
};

export type SkillListResp = { items: SkillSummary[]; total: number };

/** 新建 / 更新技能包的请求体，对齐 ADK 的 SKILL.md 目录结构。 */
export type SkillPayloadInput = {
  key: string;
  description: string;
  instructions: string;
  preapproved_tools: string;
  risk: SkillClass;
  group: SkillGroup;
  inject_state: boolean;
  references: { path: string; text?: string; base64?: string }[];
  assets: { path: string; text?: string; base64?: string }[];
  scripts: { path: string; text?: string; base64?: string }[];
};

export type SkillZipPreview = {
  key: string;
  description: string;
  instructions: string;
  preapproved_tools: string;
  risk: SkillClass | null;
  group: SkillGroup | null;
  inject_state: boolean;
  references: SkillFile[];
  assets: SkillFile[];
  scripts: SkillFile[];
};

export function listSkills() {
  return request<SkillListResp>('GET', '/skills');
}

/** 分组目录：后端给固定枚举与中英文标题。 */
export function listSkillGroups() {
  return request<{ groups: SkillGroupMeta[] }>('GET', '/skills/groups');
}

export function getSkill(key: string) {
  return request<SkillSummary>('GET', `/skills/${encodeURIComponent(key)}`);
}

export function createSkill(input: SkillPayloadInput) {
  return request<SkillSummary>('POST', '/skills', input);
}

export function updateSkill(key: string, input: SkillPayloadInput) {
  return request<SkillSummary>('PATCH', `/skills/${encodeURIComponent(key)}`, input);
}

export function deleteSkill(key: string) {
  return request<void>('DELETE', `/skills/${encodeURIComponent(key)}`);
}

export function reloadSkills() {
  return request<SkillListResp>('POST', '/skills/reload');
}

// request() 已支持 FormData（multipart），这里只做一层语义封装。
function uploadZip<T>(path: string, file: File, fields?: Record<string, string>) {
  const fd = new FormData();
  fd.append('file', file);
  for (const [k, v] of Object.entries(fields ?? {})) fd.append(k, v);
  return request<T>('POST', path, fd);
}

/** 解析 zip 技能包，只回预览，不写库。 */
export function parseSkillZip(file: File) {
  return uploadZip<SkillZipPreview>('/skills/parse', file);
}

/** 导入 zip 技能包；包内自带 risk/group 时以包内为准。 */
export function importSkillZip(file: File, opts?: { risk?: SkillClass; group?: SkillGroup }) {
  return uploadZip<SkillSummary>('/skills/import', file, {
    ...(opts?.risk ? { risk: opts.risk } : {}),
    ...(opts?.group ? { group: opts.group } : {}),
  });
}

/** 导出技能包为 zip 并触发浏览器下载。 */
export async function exportSkillZip(key: string): Promise<string> {
  const { blob, filename } = await requestFile(`/skills/${encodeURIComponent(key)}/export`);
  saveBlob(blob, filename);
  return filename;
}

// BUILTIN_SKILL_I18N: per-skill overrides for the skills shipped with
// ongrid. `name` is single-string and intentionally always English —
// skill keys are technical identifiers (knowledge_search etc.) and
// Chinese localisations of the name field made the table
// inconsistent (some rows in zh, some in en) and the LLM tool routing
// schema is English-keyed anyway. `description` still follows the user's
// locale so the human-readable explanation flips.
// 分组（group）不在这里翻译：它由后端 /skills/groups 给中英文标题。
const BUILTIN_SKILL_I18N: Record<string, { name: string; desc: { zh: string; en: string } }> = {
  'knowledge-search': {
    name: 'knowledge-search',
    desc: {
      zh: '检索组织知识库。用户问手册、故障排查、内部约定或已上传文档内容时使用 query_knowledge。',
      en: 'Search the organizational knowledge base. Use query_knowledge when the user asks about runbooks, troubleshooting steps, internal conventions, or uploaded documents.',
    },
  },
};

/** Returns a localized copy of a skill summary for the current locale. */
export function localizedSkill(s: SkillSummary): SkillSummary {
  const m = BUILTIN_SKILL_I18N[s.key];
  if (!m) return s;
  return {
    ...s,
    // Name is always English — see BUILTIN_SKILL_I18N comment for rationale.
    name: m.name,
    description: trInline(m.desc.zh, m.desc.en),
  };
}
