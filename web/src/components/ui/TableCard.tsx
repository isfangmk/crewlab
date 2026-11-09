import type { ReactNode } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { cn } from '@/lib/cn';
import { Button } from './Button';
import { Select } from './Select';
import { useI18n } from '@/i18n/locale';

type TableCardProps = {
  children: ReactNode;
  /** 表格底部分页区；不传则不占位。 */
  footer?: ReactNode;
  className?: string;
};

// 列表卡：圆角卡片包住可滚动表格，底部分页与表体共用同一边框。
export function TableCard({ children, footer, className }: TableCardProps) {
  return (
    <div
      className={cn(
        'flex min-h-0 flex-col overflow-hidden rounded-xl border border-zinc-800/60 bg-zinc-900/30',
        className,
      )}
    >
      <div className="min-h-0 flex-1 overflow-auto">{children}</div>
      {footer}
    </div>
  );
}

type TablePagerProps = {
  page: number; // 从 0 开始
  pageSize: number;
  total: number;
  shown: number;
  loading?: boolean;
  onPageChange(page: number): void;
  /** 传入后展示「每页条数」下拉。 */
  pageSizeOptions?: number[];
  onPageSizeChange?(size: number): void;
  className?: string;
};

// 统一分页条：左侧每页条数与区间，右侧上/下页。
export function TablePager({
  page,
  pageSize,
  total,
  shown,
  loading,
  onPageChange,
  pageSizeOptions,
  onPageSizeChange,
  className,
}: TablePagerProps) {
  const { tr } = useI18n();
  const currentPage = Math.max(0, page);
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const first = total === 0 ? 0 : currentPage * pageSize + 1;
  const last = total === 0 ? 0 : currentPage * pageSize + shown;

  return (
    <div
      role="navigation"
      aria-label={tr('分页', 'Pagination')}
      className={cn(
        'flex shrink-0 flex-wrap items-center gap-2 border-t border-zinc-800/60 px-3 py-2',
        className,
      )}
    >
      {pageSizeOptions && onPageSizeChange ? (
        <Select
          aria-label={tr('每页条数', 'Page size')}
          value={String(pageSize)}
          onValueChange={(v) => onPageSizeChange(Number.parseInt(v, 10))}
          options={pageSizeOptions.map((n) => ({ value: String(n), label: tr(`${n} 条/页`, `${n} / page`) }))}
          className="h-7 w-[7.5rem] text-[11px]"
        />
      ) : null}
      <span className="text-[11px] tabular-nums text-zinc-500">
        {tr(`${first}-${last} / 共 ${total} 条`, `${first}-${last} of ${total}`)}
      </span>
      <div className="ml-auto flex items-center gap-1.5">
        <Button
          variant="outline"
          size="sm"
          type="button"
          aria-label={tr('上一页', 'Previous page')}
          disabled={loading || currentPage === 0}
          onClick={() => onPageChange(Math.max(0, currentPage - 1))}
          className="h-7 w-7 p-0"
        >
          <ChevronLeft size={13} aria-hidden />
        </Button>
        <span className="min-w-[3.5rem] text-center text-[11px] tabular-nums text-zinc-400">
          {currentPage + 1} / {pageCount}
        </span>
        <Button
          variant="outline"
          size="sm"
          type="button"
          aria-label={tr('下一页', 'Next page')}
          disabled={loading || currentPage + 1 >= pageCount}
          onClick={() => onPageChange(currentPage + 1)}
          className="h-7 w-7 p-0"
        >
          <ChevronRight size={13} aria-hidden />
        </Button>
      </div>
    </div>
  );
}
