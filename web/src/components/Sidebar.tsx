import { Label } from '@/components/ui';
import { Hint } from '@/components/ui/Tooltip';
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from '@/components/ui/DropdownMenu';
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/Popover';
import { Checkbox } from '@/components/ui/Checkbox';
import { useEffect, useState } from 'react';
import { NavLink, Link, useLocation, useNavigate } from 'react-router-dom';
import {
  Bell,
  Search,
  PanelLeftClose,
  PanelLeftOpen,
  Home,
  Bot,
  LogOut,
  Settings,
  Wrench,
  Hammer,
  BookOpen,
  ChevronRight,
  Plug,
  PinOff,
  Settings2,
  ShieldCheck,
  Sun,
  Moon,
  Monitor,
  Languages,
  MessageSquare,
  Share2,
  FlaskConical,
  Brain,
  Package,
  Terminal,
  FolderGit2,
  SearchCheck,
  Workflow,
  Rocket,
  Clock,
  MessagesSquare,
  ScrollText,
  Activity,
  TestTube2,
  Users,
} from 'lucide-react';
import { Avatar } from './Avatar';
import { OngridLogo } from './OngridLogo';
import { Button } from './ui';
import { useI18n } from '@/i18n/locale';
import { useThemeMode } from '@/store/mode';
import { cn } from '@/lib/cn';
import type { IconType } from '@/lib/icon';
import { useAuth } from '@/store/auth';
import { useUi } from '@/store/ui';
import { useMe, usePermissions } from '@/store/me';

type SidebarSectionItem = {
  key: string;
  /** 已上线路由；规划中条目可省略 */
  to?: string;
  icon: IconType;
  iconSize?: number;
  label: string;
  exact?: boolean;
  exactQuery?: boolean;
  badge?: number;
  /** 迭代计划已立项、页面未上线：侧栏占位，禁止跳转 */
  planned?: boolean;
};

export function Sidebar() {
  const { sidebarCollapsed, toggleSidebar } = useUi();
  const setPaletteOpen = useUi((s) => s.setPaletteOpen);
  const { email, role, logout } = useAuth();
  const { tr, toggleLocale, locale } = useI18n();
  // Prefer display_name when present — Bootstrap 时设的"admin"或后续
  // /v1/users/{id} 改的，比 email 在 sidebar / user menu 里更友好。
  const { me } = useMe();
  const { isAdmin } = usePermissions();
  const displayName = (me?.display_name?.trim() || email) ?? tr('Ongrid 用户', 'Ongrid user');
  const navigate = useNavigate();
  const location = useLocation();
  const [narrow, setNarrow] = useState(() => window.matchMedia?.('(max-width: 767px)').matches ?? false);
  const [mobileExpanded, setMobileExpanded] = useState(false);
  useEffect(() => {
    const media = window.matchMedia?.('(max-width: 767px)');
    if (!media) return;
    const update = () => { setNarrow(media.matches); setMobileExpanded(false); };
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  useEffect(() => { setMobileExpanded(false); }, [location.pathname]);
  const toggleNavigation = () => narrow ? setMobileExpanded(value => !value) : toggleSidebar();

  const [userMenuOpen, setUserMenuOpen] = useState(false);

  const handleLogout = () => {
    setUserMenuOpen(false);
    logout();
    navigate('/login');
  };

  const { preference: themePref, resolved: themeMode, cycle: cycleTheme } = useThemeMode();
  const themeLabel =
    themePref === 'system'
      ? tr('跟随系统', 'System')
      : themePref === 'dark'
        ? tr('深色', 'Dark')
        : tr('浅色', 'Light');
  const ThemeIcon =
    themePref === 'system' ? Monitor : themeMode === 'dark' ? Moon : Sun;

  const renderUserMenuPanel = (variant: 'expanded' | 'collapsed') => (
    <DropdownMenuContent side={variant === 'expanded' ? 'bottom' : 'right'} className="w-56">
      <div className="px-3 py-2">
        <div className="truncate text-[13px] font-semibold text-zinc-100">
          {displayName}
        </div>
        {/* Show login email as secondary line when display_name is set
            (otherwise it'd duplicate the row above). */}
        {me?.display_name && email && (
          <div className="mt-0.5 truncate text-[11px] text-zinc-500">{email}</div>
        )}
        <div className="mt-0.5 text-[11px] text-zinc-500">
          {role || 'developer'}
        </div>
      </div>
      <div className="my-1 h-px bg-zinc-800" />
      {/* Language toggle — zh-CN ↔ en-US. tr() updates everywhere
          immediately because components subscribe via useI18n(). */}
      <DropdownMenuItem



        onClick={toggleLocale}
        className="flex w-full items-center justify-between gap-2 rounded-md px-3 py-1.5 text-[13px] text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100"
      >
        <span className="flex items-center gap-2">
          <Languages size={14} />
          <span>{tr('语言', 'Language')}</span>
        </span>
        <span className="text-[11px] text-zinc-500">
          {locale === 'zh-CN' ? '中 / EN' : 'EN / 中'}
        </span>
      </DropdownMenuItem>
      {/* Theme — cycle through system / light / dark */}
      <DropdownMenuItem


        onClick={cycleTheme}
        className="flex w-full items-center justify-between gap-2 rounded-md px-3 py-1.5 text-[13px] text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100"
      >
        <span className="flex items-center gap-2">
          <ThemeIcon size={14} />
          <span>{tr('主题', 'Theme')}</span>
        </span>
        <span className="text-[11px] text-zinc-500">{themeLabel}</span>
      </DropdownMenuItem>
      <div className="my-1 h-px bg-zinc-800" />
      <DropdownMenuItem


        onClick={handleLogout}
        className="flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-[13px] text-zinc-300 hover:bg-zinc-800 hover:text-red-400"
      >
        <LogOut size={14} />
        <span>{tr('退出登录', 'Log out')}</span>
      </DropdownMenuItem>
    </DropdownMenuContent>
  );

  if (narrow ? !mobileExpanded : sidebarCollapsed) {
    return (
      <aside className="flex h-full min-h-0 w-14 shrink-0 flex-col items-center gap-2 overflow-hidden border-r border-zinc-800/60 bg-zinc-900 py-3">
        {/* Brand mark + expand toggle: logo doubles as the expand
            affordance — saves a row in the narrow column. */}
        <Hint content={tr('Ongrid · 点击展开', 'Ongrid · click to expand')}><Button variant="subtle" size="sm"
          type="button"
          onClick={toggleNavigation}
          aria-label={tr('展开侧边栏', 'Expand sidebar')}

          className="p-1"
        >
          <OngridLogo size={34} />
        </Button></Hint>
        <Button variant="subtle" size="sm"
          type="button"
          onClick={toggleNavigation}
          aria-label={tr('展开侧边栏', 'Expand sidebar')}
          className="p-2"
        >
          <PanelLeftOpen size={16} />
        </Button>
        {/* 折叠轨：每业务分区一个入口，展开侧栏可见完整菜单 */}
        <Link
          to="/"
          aria-label={tr('首页', 'Home')}
          className="rounded-lg p-2 text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100"
        >
          <Home size={16} />
        </Link>
        <Link
          to="/sessions"
          aria-label={tr('对话', 'Chat')}
          className="rounded-lg p-2 text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100"
        >
          <MessageSquare size={16} />
        </Link>
        <Link
          to="/agents"
          aria-label={tr('助理', 'Assistants')}
          className="rounded-lg p-2 text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100"
        >
          <Bot size={16} />
        </Link>
        <Link
          to="/tools"
          aria-label={tr('能力', 'Capabilities')}
          className="rounded-lg p-2 text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100"
        >
          <Hammer size={16} />
        </Link>
        <Link
          to="/knowledge"
          aria-label={tr('知识库', 'Knowledge')}
          className="rounded-lg p-2 text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100"
        >
          <BookOpen size={16} />
        </Link>
        <span
          aria-disabled
          aria-label={tr('工作流（规划中）', 'Workflows (planned)')}
          className="rounded-lg p-2 text-zinc-600"
          title={tr('工作流（规划中）', 'Workflows (planned)')}
        >
          <Workflow size={16} />
        </span>
        <Link
          to="/approvals"
          aria-label={tr('治理', 'Governance')}
          className="rounded-lg p-2 text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100"
        >
          <ShieldCheck size={16} />
        </Link>
        <div className="mt-auto flex flex-col items-center gap-2">
          <Link
            to="/settings/llm"
            aria-label={tr('设置', 'Settings')}
            className="rounded-lg p-2 text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100"
          >
            <Settings size={16} />
          </Link>
          <DropdownMenu open={userMenuOpen} onOpenChange={setUserMenuOpen}>
            <DropdownMenuTrigger aria-label={tr('用户菜单', 'User menu')}
              aria-haspopup="menu"
              aria-expanded={userMenuOpen}
              className="rounded-full ring-1 ring-transparent transition hover:ring-zinc-700 focus:outline-none focus:ring-zinc-600"
            >
              <Avatar email={email} size={28} />
            </DropdownMenuTrigger>
            {renderUserMenuPanel('collapsed')}
          </DropdownMenu>
        </div>
      </aside>
    );
  }

  return (
    <aside className="flex h-full min-h-0 w-[232px] shrink-0 flex-col overflow-hidden border-r border-zinc-800/60 bg-zinc-900">
      {/* Brand row — keeps the product identity visible while the user
          row below still owns the avatar + menu. Clicking the wordmark
          goes home. */}
      <div className="flex items-center gap-1.5 border-b border-zinc-800/60 px-3 py-3">
        <Link
          to="/"
          aria-label={tr('Ongrid 首页', 'Ongrid home')}
          className="flex min-w-0 items-center gap-1.5 rounded-lg px-1 py-1 -ml-1 hover:bg-zinc-800/40"
        >
          <OngridLogo size={32} className="-mr-0.5 shrink-0" />
          <span className="text-[16px] font-semibold tracking-tight text-zinc-100">Ongrid</span>
          {/* Version moved to Settings → About (the brand mark stays clean). */}
        </Link>
        {/* Version + upgrade CTA moved to Settings → About / Upgrade — the brand
            mark stays clean. */}
      </div>

      {/* user / collapse / bell */}
      <DropdownMenu open={userMenuOpen} onOpenChange={setUserMenuOpen}>
      <div className="relative flex items-center gap-2 px-3 py-3">
        <DropdownMenuTrigger aria-label={tr('用户菜单', 'User menu')}
          aria-haspopup="menu"
          aria-expanded={userMenuOpen}
          className="flex min-w-0 flex-1 items-center gap-2 rounded-lg p-1 text-left transition hover:bg-zinc-800/60 focus:outline-none focus:ring-1 focus:ring-zinc-700"
        >
          <Avatar email={email} size={28} />
          <div className="min-w-0 flex-1">
            <div className="truncate text-[13px] font-medium text-zinc-100">
              {displayName}
            </div>
            <div className="truncate text-[11px] text-zinc-500">{tr('AI 工作台', 'AI Workbench')}</div>
          </div>
        </DropdownMenuTrigger>
        <Button variant="subtle" size="sm"
          type="button"
          onClick={toggleNavigation}
          aria-label={tr('折叠侧边栏', 'Collapse sidebar')}
          className="p-1.5"
        >
          <PanelLeftClose size={15} />
        </Button>
        <Button variant="subtle" size="sm"
          type="button"
          aria-label={tr('通知', 'Notifications')}
          className="p-1.5"
        >
          <Bell size={15} />
        </Button>
        {renderUserMenuPanel('expanded')}
      </div>
      </DropdownMenu>

      {/* search — opens the command palette (⌘P). The visual is still
          a search-input lookalike so users get the affordance, but the
          actual UI lives in CommandPalette so we don't have to ship
          two search code paths. */}
      <div className="px-3 pb-2">
        <button
          type="button"
          onClick={() => setPaletteOpen(true)}
          aria-label={tr('打开命令面板', 'Open command palette')}
          className="flex w-full items-center gap-2 rounded-md border border-zinc-800/60 bg-zinc-950/40 px-2.5 py-1.5 text-left text-[12px] text-zinc-500 hover:border-zinc-700 hover:bg-zinc-900 hover:text-zinc-300"
        >
          <Search size={13} className="text-zinc-500" />
          <span className="flex-1 truncate">{tr('搜索路由 / 会话', 'Search routes / sessions')}</span>
          <kbd className="rounded bg-zinc-800 px-1 py-0.5 text-[10px] text-zinc-500">⌘P</kbd>
        </button>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 pb-3">
        <div className="mt-1 space-y-0.5">
          <SidebarNavItem to="/" icon={Home} label={tr('首页', 'Home')} exact level={1} />
        </div>

        {/*
          侧栏 IA：已上线可跳转；planned 为迭代计划占位（不可跳转）。
          工作流 = ADK 图编排（画布 + 运行记录），定时/触发与发布仍为占位。
          命令面板 routes.ts 仅收录已上线路由。
        */}
        <CollapsibleSection
          storageKey="chat"
          title={tr('对话', 'Chat')}
          defaultOpen
          items={[
            { key: 'sessions', to: '/sessions', icon: MessageSquare, label: tr('会话', 'Sessions') },
            { key: 'share-export', icon: Share2, label: tr('导出与分享', 'Export & share'), planned: true },
          ]}
        />

        <CollapsibleSection
          storageKey="assistants"
          title={tr('助理', 'Assistants')}
          defaultOpen
          items={[
            { key: 'assistants', to: '/agents', icon: Bot, label: tr('助理', 'Assistants') },
            { key: 'playground', icon: FlaskConical, label: tr('试玩台', 'Playground'), planned: true },
            { key: 'memory', icon: Brain, label: tr('记忆', 'Memory'), planned: true },
          ]}
        />

        <CollapsibleSection
          storageKey="capabilities"
          title={tr('能力', 'Capabilities')}
          defaultOpen
          items={[
            { key: 'tools', to: '/tools', icon: Hammer, label: tr('工具', 'Tools') },
            { key: 'skills', to: '/skills', icon: Wrench, label: tr('技能', 'Skills') },
            { key: 'mcp', to: '/mcp', icon: Plug, label: 'MCP' },
            { key: 'packages', icon: Package, label: tr('插件包', 'Plugin packs'), planned: true },
            { key: 'sandbox', icon: Terminal, label: tr('沙箱', 'Sandbox'), planned: true },
          ]}
        />

        <CollapsibleSection
          storageKey="knowledge"
          title={tr('知识库', 'Knowledge')}
          defaultOpen
          items={[
            { key: 'knowledge', to: '/knowledge', icon: BookOpen, label: tr('知识库', 'Knowledge') },
            { key: 'knowledge-sources', icon: FolderGit2, label: tr('外部源', 'External sources'), planned: true },
            { key: 'knowledge-playground', icon: SearchCheck, label: tr('检索试跑', 'Retrieval playground'), planned: true },
          ]}
        />

        <CollapsibleSection
          storageKey="workflows"
          title={tr('工作流', 'Workflows')}
          defaultOpen
          items={[
            { key: 'workflows', to: '/workflows', icon: Workflow, label: tr('工作流编排', 'Workflow canvas') },
            { key: 'workflow-runs', to: '/workflows?tab=runs', icon: Rocket, label: tr('运行记录', 'Run history') },
            { key: 'schedules', icon: Clock, label: tr('定时触发', 'Schedules'), planned: true },
            { key: 'channels', icon: MessagesSquare, label: tr('IM 渠道', 'IM channels'), planned: true },
          ]}
        />

        <CollapsibleSection
          storageKey="governance"
          title={tr('治理', 'Governance')}
          defaultOpen
          items={[
            { key: 'approvals', to: '/approvals', icon: ShieldCheck, label: tr('审批', 'Approvals') },
            { key: 'audit', to: '/audit', icon: ScrollText, label: tr('审计日志', 'Audit logs') },
            { key: 'observability', to: '/observability', icon: Activity, label: tr('可观测', 'Observability') },
            { key: 'evals', icon: TestTube2, label: tr('评测', 'Evals'), planned: true },
            { key: 'users', to: '/users', icon: Users, label: tr('用户与权限', 'Users & roles') },
          ]}
        />
      </nav>

      {/* admin / settings entries 仅 admin 可见。user / viewer
          看不到这两个入口，避免误点之后再被 EmptyState 兜底 — 直接在
          导航层隔离更干净。后端兜底依然在（requireAdmin），UI 这层只
          是把入口藏起来。 */}
      {isAdmin && (
        <div className="mb-4 border-t border-zinc-800/60 p-2">
          <SidebarNavItem to="/settings/llm" icon={Settings} label={tr('设置', 'Settings')} level={2} />
        </div>
      )}

    </aside>
  );
}

// CollapsibleSection：分区标题可折叠，状态写入 localStorage，便于按使用习惯排布侧栏。
function CollapsibleSection({
  storageKey,
  title,
  defaultOpen = false,
  items,
}: {
  storageKey: string;
  title: string;
  defaultOpen?: boolean;
  items: SidebarSectionItem[];
}) {
  const { tr } = useI18n();
  const location = useLocation();
  const [manageOpen, setManageOpen] = useState(false);
  const [open, setOpen] = useState(() => {
    try {
      const raw = localStorage.getItem(`sidebar.section.${storageKey}`);
      if (raw === 'open') return true;
      if (raw === 'closed') return false;
    } catch {
      /* localStorage unavailable — fall through to default */
    }
    return defaultOpen;
  });
  const [hiddenKeys, setHiddenKeys] = useState<string[]>(() => {
    try {
      const raw = localStorage.getItem(`sidebar.section.${storageKey}.hidden`);
      const parsed: unknown = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed)
        ? parsed.filter((value): value is string => typeof value === 'string')
        : [];
    } catch {
      return [];
    }
  });
  const hasActiveItem = items.some((item) => {
    if (item.planned || !item.to) return false;
    if (item.exact) return location.pathname === item.to;
    const [path] = item.to.split('?');
    return location.pathname === path || location.pathname.startsWith(`${path}/`);
  });
  useEffect(() => {
    if (hasActiveItem) setOpen(true);
  }, [hasActiveItem]);

  const toggle = () => {
    setOpen((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(`sidebar.section.${storageKey}`, next ? 'open' : 'closed');
      } catch {
        /* ignore */
      }
      return next;
    });
  };
  const setItemVisible = (key: string, visible: boolean) => {
    setHiddenKeys((current) => {
      const next = visible
        ? current.filter((itemKey) => itemKey !== key)
        : current.includes(key)
          ? current
          : [...current, key];
      try {
        localStorage.setItem(`sidebar.section.${storageKey}.hidden`, JSON.stringify(next));
      } catch {
        /* localStorage unavailable */
      }
      return next;
    });
  };
  const visibleItems = items.filter((item) => !hiddenKeys.includes(item.key));

  return (
    <div>
      <div
        className="group/section relative mt-5 flex items-center px-2 pb-1.5"
      >
        <button
          type="button"
          onClick={toggle}
          aria-expanded={open}
          className="min-w-0 flex-1 text-left text-[13px] font-semibold text-zinc-300 transition-colors hover:text-zinc-100"
        >
          {title}
        </button>
        <div className="pointer-events-none flex items-center gap-0.5 opacity-0 transition-opacity group-hover/section:pointer-events-auto group-hover/section:opacity-100 group-focus-within/section:pointer-events-auto group-focus-within/section:opacity-100">
          <Popover open={manageOpen} onOpenChange={setManageOpen}>
          <Hint content={tr('管理菜单', 'Manage menu')}><PopoverTrigger
            type="button"
            aria-label={tr(`管理${title}菜单`, `Manage ${title} menu`)}
            aria-expanded={manageOpen}

            className={cn(
              'rounded p-1 text-zinc-600 hover:bg-zinc-800 hover:text-zinc-300',
              hiddenKeys.length > 0 && 'text-zinc-400',
            )}
          >
            <Settings2 size={12} />
          </PopoverTrigger></Hint>
            <PopoverContent side="right" className="w-52" aria-label={tr(`${title}菜单项`, `${title} menu items`)}>
                <div className="px-2 py-1 text-[11px] font-medium text-zinc-500">
                  {tr('显示的菜单', 'Visible items')}
                </div>
                {items.map((item) => (
                  <Label
                    key={item.key}
                    className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-[12px] text-zinc-300 hover:bg-zinc-800"
                  >
                    <Checkbox

                      checked={!hiddenKeys.includes(item.key)}
                      onCheckedChange={(checkedValue) => setItemVisible(item.key, checkedValue)}
                      className="h-3.5 w-3.5"
                    />
                    <item.icon size={13} className="shrink-0 text-zinc-500" />
                    <span className="truncate">{item.label}</span>
                    {item.planned ? (
                      <span className="ml-auto text-[10px] text-zinc-600">{tr('规划中', 'Soon')}</span>
                    ) : null}
                  </Label>
                ))}            </PopoverContent>
          </Popover>
          <Hint content={open ? tr('折叠', 'Collapse') : tr('展开', 'Expand')}><Button variant="subtle" size="sm"
            type="button"
            onClick={toggle}
            aria-label={open ? tr(`折叠${title}`, `Collapse ${title}`) : tr(`展开${title}`, `Expand ${title}`)}

            className="p-1"
          >
            <ChevronRight
              size={11}
              className={cn('transition-transform duration-150', open && 'rotate-90')}
            />
          </Button></Hint>
        </div>


      </div>
      {open ? (
        <div className="space-y-0.5">
          {visibleItems.map((item) => (
            <div key={item.key} className="group/item relative">
              <SidebarNavItem
                to={item.planned ? undefined : item.to}
                icon={item.icon}
                iconSize={item.iconSize}
                label={item.label}
                exact={item.exact}
                exactQuery={item.exactQuery}
                badge={item.badge}
                planned={item.planned}
                reserveTrailingAction
              />
              <Hint content={tr('从侧栏取消固定', 'Unpin from sidebar')}><Button variant="subtle" size="sm"
                type="button"
                onClick={() => setItemVisible(item.key, false)}
                aria-label={tr(`从侧栏取消固定${item.label}`, `Unpin ${item.label} from sidebar`)}

                className="pointer-events-none absolute right-1 top-1/2 -translate-y-1/2 p-1 opacity-0 transition-opacity group-hover/item:pointer-events-auto group-hover/item:opacity-100 group-focus-within/item:pointer-events-auto group-focus-within/item:opacity-100"
              >
                <PinOff size={12} />
              </Button></Hint>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function SidebarNavItem({
  to,
  icon: Icon,
  iconSize = 14,
  label,
  exact,
  exactQuery,
  disabled,
  planned,
  muted,
  level,
  badge,
  reserveTrailingAction,
}: {
  to?: string;
  icon: IconType;
  iconSize?: number;
  label: string;
  exact?: boolean;
  exactQuery?: boolean;
  disabled?: boolean;
  planned?: boolean;
  muted?: boolean;
  // level 1 = 一级（首页 / 仪表盘）不缩进；默认 2 = 二级，缩 ml-2 比之前 ml-3 更紧凑。
  level?: 1 | 2;
  // badge: optional unread count rendered as a red pill on the right.
  // Hidden when 0 or undefined; capped to "99+" so widths stay sane.
  badge?: number;
  reserveTrailingAction?: boolean;
}) {
  const { tr } = useI18n();
  const indentCls = level === 1 ? '' : 'ml-2';
  const baseCls = cn(
    indentCls,
    'flex items-center gap-2 rounded-md px-2 py-1.5 text-[13px] transition-colors',
    reserveTrailingAction && 'pr-8',
  );
  const location = useLocation();
  if (disabled || planned || !to) {
    const plannedLabel = tr('规划中', 'Soon');
    return (
      <div
        aria-disabled
        aria-label={planned ? `${label}（${plannedLabel}）` : label}
        title={planned ? plannedLabel : undefined}
        className={cn(
          baseCls,
          'cursor-not-allowed opacity-50',
          muted ? 'text-zinc-300' : 'text-zinc-500'
        )}
      >
        <Icon size={iconSize} className="shrink-0 text-zinc-500" />
        <span className="flex-1 truncate">{label}</span>
        {planned ? (
          <span className="text-[10px] text-zinc-600">{plannedLabel}</span>
        ) : null}
      </div>
    );
  }

  // We need pathname AND query-string to participate in active detection when
  // a sidebar item explicitly includes a query. Plain pathname items remain
  // active across page-local filters like /devices?roles=server.
  const [targetPath, targetQuery] = to.split('?');
  const hasQuery = Boolean(targetQuery);
  const targetParams = hasQuery ? new URLSearchParams(targetQuery) : null;
  let isActive: boolean;
  if (location.pathname !== targetPath) {
    isActive = false;
  } else if (hasQuery) {
    const currentParams = new URLSearchParams(location.search);
    // Every query key in `to` must match the current URL. Extra query keys on
    // the current page are ignored so a filtered tab can still highlight.
    isActive = paramsEqualOnDefinedKeys(targetParams!, currentParams);
  } else if (exactQuery) {
    isActive = location.search === '';
  } else if (exact) {
    isActive = location.pathname === targetPath;
  } else {
    isActive = true;
  }

  return (
    <NavLink
      to={to}
      end={exact}
      className={cn(
        baseCls,
        'text-zinc-300 hover:bg-zinc-800/60 hover:text-zinc-100',
        isActive && 'bg-zinc-800 text-zinc-100'
      )}
    >
      <Icon size={iconSize} className="shrink-0 text-zinc-400" />
      <span className="flex-1 truncate">{label}</span>
      {badge != null && badge > 0 && (
        <span
          className="ml-auto inline-flex min-w-[18px] items-center justify-center rounded-full bg-red-500/90 px-1.5 text-[10px] font-medium text-white"
          aria-label={tr(`${badge} 未确认`, `${badge} open`)}
        >
          {badge > 99 ? '99+' : badge}
        </span>
      )}
    </NavLink>
  );
}

// paramsEqualOnDefinedKeys reports whether every key in `target` has the
// same value in `current`. Extra keys on `current` are ignored. Used to
// decide whether a sidebar item with `?tab=reports` should highlight against
// a URL like `/pages?tab=reports&status=open`.
function paramsEqualOnDefinedKeys(target: URLSearchParams, current: URLSearchParams) {
  for (const [k, v] of target.entries()) {
    if (current.get(k) !== v) return false;
  }
  return true;
}
