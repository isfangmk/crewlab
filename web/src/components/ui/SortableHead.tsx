import type { MouseEventHandler, ReactNode } from 'react';
import { ArrowDown, ArrowUp, ChevronsUpDown } from 'lucide-react';

import { useI18n } from '@/i18n/locale';
import { cn } from '@/lib/cn';

export type SortOrder = 'asc' | 'desc';

type SortButtonProps = {
  label: ReactNode;
  active: boolean;
  order: SortOrder;
  onClick?: MouseEventHandler<HTMLButtonElement>;
  title?: string;
  className?: string;
};

/** 可排序表头按钮：激活列用紫色箭头反馈，未激活显示双向箭头。 */
export function SortButton({ label, active, order, onClick, title, className }: SortButtonProps) {
  const Icon = active ? (order === 'asc' ? ArrowUp : ArrowDown) : ChevronsUpDown;
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={cn(
        '-my-0.5 inline-flex items-center gap-1 rounded px-1 py-0.5 transition-colors hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-indigo-500/40',
        active && 'text-zinc-200',
        className,
      )}
    >
      {label}
      <Icon size={11} aria-hidden className={active ? 'text-indigo-300' : 'text-zinc-600'} />
    </button>
  );
}

type SortableHeadProps = {
  label: string;
  active: boolean;
  order: SortOrder;
  onClick(): void;
  align?: 'left' | 'right';
  className?: string;
};

/** 整格可排序表头；aria-sort 挂在 th 上，读屏能识别当前排序列与方向。 */
export function SortableHead({ label, active, order, onClick, align = 'left', className }: SortableHeadProps) {
  const { tr } = useI18n();
  return (
    <th
      scope="col"
      aria-sort={active ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}
      className={cn('px-3 py-2 font-medium', align === 'right' ? 'text-right' : 'text-left', className)}
    >
      <SortButton
        label={label}
        active={active}
        order={order}
        onClick={onClick}
        title={tr(`按${label}排序`, `Sort by ${label}`)}
      />
    </th>
  );
}
