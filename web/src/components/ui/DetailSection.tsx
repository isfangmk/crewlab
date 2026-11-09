import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

// DetailSection — 详情页分组标题 + 内容，统一 11px 大写标签与 8px 间距。
type Props = {
  title: string;
  children: ReactNode;
  /** danger 用于「错误」这类需要立刻注意的分组。 */
  tone?: 'danger';
  actions?: ReactNode;
  className?: string;
};

export function DetailSection({ title, children, tone, actions, className }: Props) {
  return (
    <section className={cn('min-w-0', className)}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3
          className={cn(
            'text-[11px] font-medium uppercase tracking-wide',
            tone === 'danger' ? 'text-danger' : 'text-zinc-500',
          )}
        >
          {title}
        </h3>
        {actions ? <div className="flex shrink-0 items-center gap-1.5">{actions}</div> : null}
      </div>
      <div className="mt-2">{children}</div>
    </section>
  );
}
