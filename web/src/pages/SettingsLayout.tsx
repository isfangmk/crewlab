import { Hint } from '@/components/ui/Tooltip';
import { Suspense } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import {
  KeyRound,
  Loader2,
  Bot,
  Database,
  Gauge,
  Info,
  Lock,
  Shield,
} from 'lucide-react';
import { cn } from '@/lib/cn';
import type { IconType } from '@/lib/icon';
import { EmptyStateCard, PageHeader } from '@/components/ui';
import { useI18n } from '@/i18n/locale';
import { usePermissions } from '@/store/me';

type RailItem = {
  to: string;
  icon: IconType;
  labelZh: string;
  labelEn: string;
  hintZh: string;
  hintEn: string;
};

const RAIL_ITEMS: RailItem[] = [
  { to: 'llm', icon: KeyRound, labelZh: '模型', labelEn: 'LLM', hintZh: '对话模型唯一来源', hintEn: 'Sole source of chat models' },
  { to: 'secrets', icon: Lock, labelZh: '凭证', labelEn: 'Credentials', hintZh: '密钥托管', hintEn: 'Secret vault' },
  { to: 'agent', icon: Bot, labelZh: '助理', labelEn: 'Agent', hintZh: '运行参数', hintEn: 'Runtime options' },
  { to: 'knowledge', icon: Database, labelZh: '知识库', labelEn: 'Knowledge', hintZh: '切块与检索阈值', hintEn: 'Chunking and retrieval thresholds' },
  { to: 'preferences', icon: Gauge, labelZh: '偏好', labelEn: 'Preferences', hintZh: '界面偏好', hintEn: 'UI preferences' },
  { to: 'about', icon: Info, labelZh: '关于', labelEn: 'About', hintZh: '版本信息', hintEn: 'Version info' },
];

export default function SettingsLayout() {
  const { tr } = useI18n();
  const { isAdmin } = usePermissions();
  if (!isAdmin) {
    return (
      <main className="anim-fade flex flex-1 flex-col overflow-hidden">
        <PageHeader title={tr('设置', 'Settings')} subtitle={tr('产品级配置', 'Product settings')} />
        <div className="flex-1 overflow-y-auto px-6 py-6">
          <div className="mx-auto max-w-3xl">
            <EmptyStateCard
              icon={Shield}
              title={tr('需要管理员权限', 'Admin permission required')}
              hint={tr('设置页只对管理员开放。', 'Settings are admin-only.')}
            />
          </div>
        </div>
      </main>
    );
  }
  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader title={tr('设置', 'Settings')} subtitle={tr('产品级配置', 'Product settings')} />
      <div className="flex-1 overflow-hidden">
        <div className="grid h-full grid-cols-1 lg:grid-cols-[240px_1fr]">
          <nav
            aria-label={tr('设置分类', 'Settings categories')}
            className={cn(
              'border-zinc-800 bg-zinc-950/40',
              'lg:h-full lg:overflow-y-auto lg:border-r',
              'flex shrink-0 gap-1 overflow-x-auto border-b px-3 py-3 lg:flex-col lg:gap-0.5 lg:px-3 lg:py-4',
            )}
          >
            {RAIL_ITEMS.map((item) => (
              <RailLink key={item.to} item={item} />
            ))}
          </nav>
          <div className="overflow-y-auto px-6 py-6">
            <div className="mx-auto max-w-3xl">
              <Suspense fallback={<SettingsLoading />}>
                <Outlet />
              </Suspense>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}

function RailLink({ item }: { item: RailItem }) {
  const { tr } = useI18n();
  const Icon = item.icon;
  return (
    <Hint content={tr(item.hintZh, item.hintEn)}>
      <NavLink
        to={item.to}
        className={({ isActive }) =>
          cn(
            'flex items-center gap-2 rounded-md px-2.5 py-1.5 text-[12.5px] transition-colors',
            isActive
              ? 'bg-indigo-500/10 text-zinc-100 ring-1 ring-inset ring-indigo-500/30'
              : 'text-zinc-400 hover:bg-zinc-800/40 hover:text-zinc-200',
          )
        }
      >
        {({ isActive }) => (
          <>
            <Icon size={14} className={isActive ? 'text-indigo-300' : undefined} />
            <span>{tr(item.labelZh, item.labelEn)}</span>
          </>
        )}
      </NavLink>
    </Hint>
  );
}

function SettingsLoading() {
  return (
    <div className="flex items-center gap-2 text-sm text-zinc-500">
      <Loader2 className="animate-spin" size={16} />
      Loading…
    </div>
  );
}
