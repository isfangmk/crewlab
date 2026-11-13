import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

// SectionCard — 设置页 / 详情页的分组卡片。
// 统一：标题 12.5px、说明 11px、内边距 16/12px、可选右上动作与底部操作条。
type Props = {
  title: ReactNode;
  description?: ReactNode;
  /** 右上角动作（通常是一个 RowAction 或 Switch）。 */
  actions?: ReactNode;
  /** 底部操作条；有内容时自动加顶部分隔线并右对齐。 */
  footer?: ReactNode;
  children?: ReactNode;
  className?: string;
  bodyClassName?: string;
  testId?: string;
};

export function SectionCard({
  title,
  description,
  actions,
  footer,
  children,
  className,
  bodyClassName,
  testId,
}: Props) {
  return (
    <section data-testid={testId} className={cn('flex flex-col overflow-hidden rounded-xl border border-zinc-800/60 bg-zinc-900/40', className)}>
      <header className="flex flex-wrap items-start justify-between gap-2 border-b border-zinc-800/60 px-4 py-3">
        <div className="min-w-0">
          <h2 className="text-[12.5px] font-medium leading-5 text-zinc-100">{title}</h2>
          {description ? (
            <p className="mt-0.5 text-[11px] leading-4 text-zinc-500">{description}</p>
          ) : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-1.5">{actions}</div> : null}
      </header>
      <div className={cn('min-h-0 flex-1 px-4 py-3', bodyClassName)}>{children}</div>
      {footer ? (
        <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-zinc-800/60 px-4 py-2.5">
          {footer}
        </footer>
      ) : null}
    </section>
  );
}
