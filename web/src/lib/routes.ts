// 命令面板（⌘P）用的工作台路由目录；只保留 App.tsx 已挂载路径。
import { tr as trInline } from '@/i18n/locale';

export type AppRouteGroup = '主页' | 'Agent' | '知识库' | '设置';

export type AppRoute = {
  path: string;
  label: string;
  keywords?: string[];
  group: AppRouteGroup;
};

type RouteDef = { path: string; zh: string; en: string; keywords?: string[]; group: AppRouteGroup };

const ROUTE_DEFS: RouteDef[] = [
  { path: '/', zh: '首页', en: 'Home', keywords: ['home', 'shouye'], group: '主页' },

  { path: '/agents', zh: '助理', en: 'Assistants', keywords: ['agents', 'assistant', 'bot', 'zhuli'], group: 'Agent' },
  { path: '/tools', zh: '工具', en: 'Tools', keywords: ['tools', 'builtin', 'gongju'], group: 'Agent' },
  { path: '/skills', zh: '技能', en: 'Skills', keywords: ['skills', 'jineng'], group: 'Agent' },
  { path: '/mcp', zh: 'MCP', en: 'MCP', keywords: ['mcp', 'server'], group: 'Agent' },
  { path: '/approvals', zh: '审批', en: 'Approvals', keywords: ['approvals', 'shenpi', 'confirm'], group: 'Agent' },

  { path: '/knowledge', zh: '知识库', en: 'Knowledge', keywords: ['knowledge', 'kb', 'docs', 'rag', 'zhishiku', 'upload'], group: '知识库' },

  { path: '/settings/llm', zh: '设置 / 模型', en: 'Settings / LLM', keywords: ['llm', 'model', 'moxing'], group: '设置' },
  { path: '/settings/secrets', zh: '设置 / 凭证', en: 'Settings / Credentials', keywords: ['secrets', 'credentials', 'pingzheng'], group: '设置' },
  { path: '/settings/agent', zh: '设置 / 助理', en: 'Settings / Agent', keywords: ['agent', 'timeout', 'locale'], group: '设置' },
  { path: '/settings/preferences', zh: '设置 / 偏好', en: 'Settings / Preferences', keywords: ['preferences', 'pianhao', 'timezone', 'shiqu'], group: '设置' },
  { path: '/settings/about', zh: '设置 / 关于', en: 'Settings / About', keywords: ['about', 'version', 'guanyu'], group: '设置' },
];

export const APP_ROUTES: AppRoute[] = ROUTE_DEFS.map((r) => Object.defineProperty(
  { path: r.path, keywords: r.keywords, group: r.group, label: '' } as AppRoute,
  'label',
  { get: () => trInline(r.zh, r.en), enumerable: true },
));

export function fuzzyMatchScore(query: string, target: string): number {
  if (!query) return 0;
  const q = query.toLowerCase();
  const t = target.toLowerCase();
  let qi = 0;
  let score = 0;
  let prevMatch = -2;
  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] === q[qi]) {
      if (ti === prevMatch + 1) score += 2;
      else if (ti === 0 || /[\s/_-]/.test(t[ti - 1])) score += 2;
      else score += 1;
      prevMatch = ti;
      qi++;
    }
  }
  if (qi < q.length) return -1;
  return score - t.length * 0.01;
}

export function scoreRoute(query: string, route: AppRoute): number {
  if (!query) return 0;
  const candidates = [route.label, route.path, ...(route.keywords ?? [])];
  let best = -1;
  for (const c of candidates) {
    const s = fuzzyMatchScore(query, c);
    if (s > best) best = s;
  }
  return best;
}
