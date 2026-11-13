// 命令面板（⌘P）路由目录：分组与侧栏 IA 一致，只收录已上线路由。
// 规划中菜单（工作流/Playground/审计等）仅出现在侧栏占位，不进命令面板。
import { tr as trInline } from '@/i18n/locale';

export type AppRouteGroup = '主页' | '对话' | '助理' | '能力' | '知识库' | '治理' | '设置';

export type AppRoute = {
  path: string;
  label: string;
  keywords?: string[];
  group: AppRouteGroup;
};

type RouteDef = { path: string; zh: string; en: string; keywords?: string[]; group: AppRouteGroup };

const ROUTE_DEFS: RouteDef[] = [
  { path: '/', zh: '首页', en: 'Home', keywords: ['home', 'shouye'], group: '主页' },

  // 对话：会话管理；后续可挂导出中心等（不放助理能力）
  { path: '/sessions', zh: '会话', en: 'Sessions', keywords: ['sessions', 'chat', 'huihua', 'duihua'], group: '对话' },

  // 助理：人设与装配；后续可挂 Memory / Playground
  { path: '/agents', zh: '助理', en: 'Assistants', keywords: ['agents', 'assistant', 'bot', 'zhuli'], group: '助理' },

  // 能力：可被助理装配的执行面
  { path: '/tools', zh: '工具', en: 'Tools', keywords: ['tools', 'builtin', 'gongju'], group: '能力' },
  { path: '/skills', zh: '技能', en: 'Skills', keywords: ['skills', 'jineng'], group: '能力' },
  { path: '/mcp', zh: 'MCP', en: 'MCP', keywords: ['mcp', 'server'], group: '能力' },
  { path: '/sandbox', zh: '沙箱', en: 'Sandbox', keywords: ['sandbox', 'shaxiang', 'code', 'exec', 'python'], group: '能力' },

  // 知识库：文档与检索；后续可挂检索试跑子页
  { path: '/knowledge', zh: '知识库', en: 'Knowledge', keywords: ['knowledge', 'kb', 'docs', 'rag', 'zhishiku', 'upload'], group: '知识库' },

  // 治理：审批闸门与安全；后续可挂评测（不放助理菜单）
  { path: '/approvals', zh: '审批', en: 'Approvals', keywords: ['approvals', 'shenpi', 'confirm'], group: '治理' },
  { path: '/audit', zh: '审计日志', en: 'Audit logs', keywords: ['audit', 'logs', 'shenji'], group: '治理' },
  { path: '/observability', zh: '可观测', en: 'Observability', keywords: ['observability', 'trace', 'token', 'keguance'], group: '治理' },
  { path: '/users', zh: '用户与权限', en: 'Users & roles', keywords: ['users', 'roles', 'permissions', 'yonghu'], group: '治理' },

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
