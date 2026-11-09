import type { ReactNode } from 'react';
import { X } from 'lucide-react';
import { cn } from '@/lib/cn';
import { useI18n } from '@/i18n/locale';

type FilterBarProps = {
  children: ReactNode;
  /** 第二行：已生效条件胶囊、自定义时间等；有内容时自动加分隔线。 */
  footer?: ReactNode;
  className?: string;
};

// 统一筛选卡：第一行搜索 + 筛选控件，第二行已生效条件。
// 各列表页共用同一卡片表面，避免每页各写一套 border/padding。
export function FilterBar({ children, footer, className }: FilterBarProps) {
  return (
    <section
      className={cn(
        'shrink-0 overflow-hidden rounded-xl border border-zinc-800/60 bg-zinc-900/40',
        className,
      )}
    >
      <div className="flex flex-wrap items-center gap-2 px-3 py-2.5">{children}</div>
      {footer ? (
        <div className="flex flex-wrap items-center gap-1.5 border-t border-zinc-800/60 px-3 py-2">
          {footer}
        </div>
      ) : null}
    </section>
  );
}

type FilterPillProps = {
  label: string;
  /** 生效时高亮并显示一键清除按钮。 */
  active: boolean;
  onClear?(): void;
  /** 控件的固定宽度类，例如 w-[9rem]。 */
  width?: string;
  children: ReactNode;
};

// 筛选胶囊：左侧是字段名，右侧内嵌 Select / Input 等控件。
export function FilterPill({ label, active, onClear, width, children }: FilterPillProps) {
  const { tr } = useI18n();
  return (
    <div
      className={cn(
        'inline-flex h-8 items-center gap-1 rounded-lg border pl-2 pr-1 transition-colors',
        width,
        active
          ? 'border-indigo-500/40 bg-indigo-500/10'
          : 'border-zinc-800/60 bg-zinc-900/50 hover:border-zinc-700',
      )}
    >
      <span className={cn('shrink-0 text-[11px]', active ? 'text-indigo-300' : 'text-zinc-500')}>
        {label}
      </span>
      <div className="flex min-w-0 flex-1 items-center">{children}</div>
      {active && onClear ? (
        <button
          type="button"
          aria-label={tr(`清除${label}筛选`, `Clear ${label} filter`)}
          onClick={onClear}
          className="shrink-0 rounded p-0.5 text-zinc-400 transition-colors hover:bg-zinc-800/60 hover:text-zinc-100"
        >
          <X size={10} aria-hidden />
        </button>
      ) : null}
    </div>
  );
}

// 单选条件胶囊：用于风险、类别、状态等固定枚举筛选。
export function ChoiceChip({
  active,
  label,
  count,
  onClick,
}: {
  active: boolean;
  label: string;
  /** 可选的计数，展示在文案右侧。 */
  count?: number;
  onClick(): void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        'inline-flex h-7 items-center gap-1 rounded-lg border px-2 text-[11px] leading-none transition-colors',
        active
          ? 'border-zinc-600 bg-zinc-800 text-zinc-100'
          : 'border-zinc-800/60 bg-zinc-900/50 text-zinc-400 hover:border-zinc-700 hover:bg-zinc-800/60 hover:text-zinc-200',
      )}
    >
      {label}
      {count === undefined ? null : (
        <span className={cn('tabular-nums', active ? 'text-zinc-300' : 'text-zinc-600')}>{count}</span>
      )}
    </button>
  );
}

// 已生效条件胶囊：点击即单独撤销该条件。
export function FilterChip({ label, onClear }: { label: string; onClear(): void }) {
  const { tr } = useI18n();
  return (
    <button
      type="button"
      aria-label={tr(`清除条件：${label}`, `Clear filter: ${label}`)}
      onClick={onClear}
      className="inline-flex items-center gap-1 rounded-full border border-zinc-700 bg-zinc-800/40 px-2 py-1 text-[11px] leading-none text-zinc-300 transition-colors hover:border-zinc-600 hover:bg-zinc-800 hover:text-zinc-100"
    >
      {label}
      <X size={10} aria-hidden className="text-zinc-500" />
    </button>
  );
}
