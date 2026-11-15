// 命令面板（⌘P）路由目录：分组与侧栏 IA 一致，只收录已上线路由。
// 侧栏里的规划中占位（Playground、评测、IM 渠道等）不进命令面板。
import { tr as trInline } from '@/i18n/locale';

export type AppRouteGroup = '主页' | '对话' | '助理' | '能力' | '知识库' | '工作流' | '治理' | '设置';

export type AppRoute = {
  path: string;
  label: string;
  keywords?: string[];
  group: AppRouteGroup;
};

type RouteDef = { path: string; zh: string; en: string; keywords?: string[]; group: AppRouteGroup };

const ROUTE_DEFS: RouteDef[] = [
  { path: '/', zh: '首页', en: 'Home', keywords: ['home', 'shouye'], group: '主页' },

  // 对话：会话管理与分享管理（不放助理能力）
  { path: '/sessions', zh: '会话', en: 'Sessions', keywords: ['sessions', 'chat', 'huihua', 'duihua'], group: '对话' },
  { path: '/share-export', zh: '导出与分享', en: 'Export & share', keywords: ['share', 'export', 'link', 'fenxiang', 'daochu', 'fenxianglianjie'], group: '对话' },

  // 助理：人设与装配；记忆是助理的跨会话上下文
  { path: '/agents', zh: '助理', en: 'Assistants', keywords: ['agents', 'assistant', 'bot', 'zhuli'], group: '助理' },
  { path: '/memory', zh: '记忆', en: 'Memory', keywords: ['memory', 'jiyi', 'recall', 'remember'], group: '助理' },

  // 能力：可被助理装配的执行面
  { path: '/tools', zh: '工具', en: 'Tools', keywords: ['tools', 'builtin', 'gongju'], group: '能力' },
  { path: '/skills', zh: '技能', en: 'Skills', keywords: ['skills', 'jineng'], group: '能力' },
  { path: '/mcp', zh: 'MCP', en: 'MCP', keywords: ['mcp', 'server'], group: '能力' },
  { path: '/sandbox', zh: '沙箱', en: 'Sandbox', keywords: ['sandbox', 'shaxiang', 'code', 'exec', 'python'], group: '能力' },

  // 知识库：文档、外部来源与检索试跑
  { path: '/knowledge', zh: '知识库', en: 'Knowledge', keywords: ['knowledge', 'kb', 'docs', 'rag', 'zhishiku', 'upload'], group: '知识库' },
  { path: '/knowledge/sources', zh: '外部知识源', en: 'External sources', keywords: ['sources', 'external', 'url', 'git', 'sync', 'waibuyuan'], group: '知识库' },
  { path: '/knowledge/playground', zh: '检索试跑', en: 'Retrieval playground', keywords: ['search', 'retrieval', 'rrf', 'scope', 'jiansuo', 'shiyao'], group: '知识库' },

  // 工作流：编排、运行记录与自动化触发
  { path: '/workflows', zh: '工作流', en: 'Workflows', keywords: ['workflows', 'flow', 'canvas', 'gongzuoliu'], group: '工作流' },
  { path: '/workflows?tab=runs', zh: '运行记录', en: 'Run history', keywords: ['runs', 'history', 'yunxing', 'jilu'], group: '工作流' },
  { path: '/triggers', zh: '触发器', en: 'Triggers', keywords: ['triggers', 'cron', 'webhook', 'chufaqi'], group: '工作流' },

  // 治理：审批闸门与安全；后续可挂评测（不放助理菜单）
  { path: '/approvals', zh: '审批', en: 'Approvals', keywords: ['approvals', 'shenpi', 'confirm'], group: '治理' },
  { path: '/approval-rules', zh: '审批规则', en: 'Approval rules', keywords: ['rules', 'approval', 'guize', 'shenpi'], group: '治理' },
  { path: '/audit', zh: '审计日志', en: 'Audit logs', keywords: ['audit', 'logs', 'shenji'], group: '治理' },
  { path: '/observability', zh: '可观测', en: 'Observability', keywords: ['observability', 'trace', 'token', 'keguance'], group: '治理' },
  { path: '/usage', zh: '用量与成本', en: 'Usage & cost', keywords: ['usage', 'cost', 'tokens', 'yongliang', 'chengben'], group: '治理' },
  { path: '/publish', zh: '发布管理', en: 'Publishing', keywords: ['publish', 'openai', 'api', 'key', 'fabu'], group: '治理' },
  { path: '/evals', zh: '评测', en: 'Evals', keywords: ['evals', 'eval', 'regression', 'test', 'pingce'], group: '治理' },
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
