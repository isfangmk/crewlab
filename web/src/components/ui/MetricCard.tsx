import { Activity, type LucideIcon } from 'lucide-react';
import { cn } from '@/lib/cn';
import type { IconType } from '@/lib/icon';

export type MetricTone = 'default' | 'danger' | 'ok' | 'warn' | 'accent' | 'info';

const METRIC_TONES: Record<MetricTone, { box: string; icon: string; bar: string }> = {
  default: { box: 'bg-zinc-800/60', icon: 'text-zinc-400', bar: 'bg-zinc-400/70' },
  danger: { box: 'bg-red-500/10', icon: 'text-red-400', bar: 'bg-red-500/70' },
  ok: { box: 'bg-ok/10', icon: 'text-ok', bar: 'bg-ok/70' },
  warn: { box: 'bg-warn/10', icon: 'text-warn', bar: 'bg-warn/70' },
  accent: { box: 'bg-accent/10', icon: 'text-accent', bar: 'bg-accent/70' },
  info: { box: 'bg-info/10', icon: 'text-info', bar: 'bg-info/70' },
};

export const DEFAULT_METRIC_ICON: LucideIcon = Activity;

type Props = {
  icon: IconType;
  label: string;
  value: string;
  /** 主数值下方的一行注脚，例如失败率、均值。 */
  hint?: string;
  tone?: MetricTone;
  /** 0-100，用于失败率等占比可视化。 */
  bar?: number;
  /** 传入后可点击，用于把指标卡当筛选入口。 */
  onClick?(): void;
  /** 当前筛选命中该卡片时高亮。 */
  active?: boolean;
  className?: string;
};

// 概览指标卡：图标 + 标签 + 主数值 + 注脚（可选占比条）。
// 列表页顶部统计统一用这个组件，避免各页各写一套字号与间距。
export function MetricCard({
  icon: Icon,
  label,
  value,
  hint,
  tone = 'default',
  bar,
  onClick,
  active,
  className,
}: Props) {
  const style = METRIC_TONES[tone];
  const body = (
    <>
      <div className="flex min-w-0 items-center gap-1.5">
        <span className={cn('flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded', style.box)}>
          <Icon size={11} className={style.icon} aria-hidden />
        </span>
        <span className="truncate text-[11px] text-zinc-500">{label}</span>
      </div>
      <div className="mt-1 truncate text-[19px] font-semibold leading-tight tabular-nums text-zinc-100" title={value}>
        {value}
      </div>
      <div className="mt-0.5 h-3 truncate text-[10px] leading-3 text-zinc-600">{hint ?? ''}</div>
      {bar === undefined ? null : (
        <div className="mt-1.5 h-0.5 w-full overflow-hidden rounded-full bg-zinc-800/80">
          <span
            className={cn('block h-full rounded-full transition-all', style.bar)}
            style={{ width: `${Math.min(Math.max(bar, 0), 100)}%` }}
          />
        </div>
      )}
    </>
  );

  if (onClick === undefined) {
    return (
      <div className={cn('min-w-0 rounded-xl border border-zinc-800/60 bg-zinc-900/40 px-3 py-2.5', className)}>
        {body}
      </div>
    );
  }

  return (
    <button
      type="button"
      aria-pressed={active === true}
      onClick={onClick}
      className={cn(
        'min-w-0 rounded-xl border px-3 py-2.5 text-left transition-colors',
        active
          ? 'border-indigo-500/40 bg-indigo-500/10'
          : 'border-zinc-800/60 bg-zinc-900/40 hover:border-zinc-700 hover:bg-zinc-900/60',
        className,
      )}
    >
      {body}
    </button>
  );
}
