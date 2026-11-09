import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

// DetailStat — 详情页指标格：10px 标签 + 14px 等宽数值。
type StatProps = {
  label: string;
  value: ReactNode;
  /** 悬停完整值（数值被截断时使用）。 */
  title?: string;
  className?: string;
};

export function DetailStat({ label, value, title, className }: StatProps) {
  return (
    <div className={cn('min-w-0 bg-zinc-900/40 px-3 py-2', className)}>
      <div className="truncate text-[10px] tracking-wide text-zinc-500">{label}</div>
      <div
        className="mt-0.5 truncate text-[14px] font-medium tabular-nums text-zinc-100"
        title={title ?? (typeof value === 'string' ? value : undefined)}
      >
        {value}
      </div>
    </div>
  );
}

// DetailStatGrid — 指标格容器：1px 分隔线，2 列起，桌面可到 4 列。
export function DetailStatGrid({
  children,
  columns = 4,
  className,
}: {
  children: ReactNode;
  columns?: 2 | 3 | 4;
  className?: string;
}) {
  const cols = columns === 2 ? 'sm:grid-cols-2' : columns === 3 ? 'sm:grid-cols-3' : 'sm:grid-cols-4';
  return (
    <div
      className={cn(
        'grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-zinc-800/60 bg-zinc-800/40',
        cols,
        className,
      )}
    >
      {children}
    </div>
  );
}
