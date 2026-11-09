import { cn } from '@/lib/cn';

export type StatusTone = 'ok' | 'warn' | 'danger' | 'info' | 'neutral';

const TONES: Record<StatusTone, { dot: string; pill: string }> = {
  ok: { dot: 'bg-ok', pill: 'border-ok/30 bg-ok/10 text-ok' },
  warn: { dot: 'bg-warn', pill: 'border-warn/30 bg-warn/10 text-warn' },
  danger: { dot: 'bg-danger', pill: 'border-danger/30 bg-danger/10 text-danger' },
  info: { dot: 'bg-info', pill: 'border-info/30 bg-info/10 text-info' },
  neutral: { dot: 'bg-zinc-500', pill: 'border-zinc-700 bg-zinc-800/40 text-zinc-400' },
};

type Props = {
  tone: StatusTone;
  label: string;
  /** 进行中状态用脉冲点强调。 */
  pulse?: boolean;
  className?: string;
};

// 状态胶囊：圆点 + 文案。列表行、详情页头部共用，保证同一状态同一配色。
export function StatusPill({ tone, label, pulse, className }: Props) {
  const style = TONES[tone];
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-1.5 py-[1px] text-[11px] leading-4',
        style.pill,
        className,
      )}
    >
      <span className={cn('h-1 w-1 shrink-0 rounded-full', style.dot, pulse && 'animate-pulse-dot')} aria-hidden />
      <span className="truncate">{label}</span>
    </span>
  );
}
