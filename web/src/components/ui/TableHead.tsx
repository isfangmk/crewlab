import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

// 表格表头：粘在表格顶部，滚动时列名保持可见。
// 11 个列表页各写一份同样的 className，改一次要动十几个文件，这里统一一处。
export const TABLE_HEAD_CLASS =
  'sticky top-0 z-10 border-b border-zinc-800/60 bg-zinc-900/95 text-[11px] text-zinc-500 backdrop-blur';

type Props = {
  children: ReactNode;
  className?: string;
};

export function TableHead({ children, className }: Props) {
  return <thead className={cn(TABLE_HEAD_CLASS, className)}>{children}</thead>;
}

/** 表头单元格：默认左对齐，与表体首列对齐。 */
export function TableHeadCell({ children, className }: Props) {
  return <th className={cn('px-3 py-2 text-left font-medium', className)}>{children}</th>;
}
