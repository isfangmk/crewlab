import type { KeyboardEventHandler } from 'react';
import { Search, X } from 'lucide-react';
import { cn } from '@/lib/cn';
import { Input } from './Input';
import { useI18n } from '@/i18n/locale';

type Props = {
  value: string;
  onChange(value: string): void;
  placeholder: string;
  ariaLabel: string;
  /** 清空按钮的回调；默认只清空 value。 */
  onClear?(): void;
  onKeyDown?: KeyboardEventHandler<HTMLInputElement>;
  /** 外层宽度类，默认自适应占满一行。 */
  className?: string;
  inputClassName?: string;
};

// 列表页统一搜索框：左侧放大镜，有内容时右侧出现清空按钮。
export function SearchInput({
  value,
  onChange,
  placeholder,
  ariaLabel,
  onClear,
  onKeyDown,
  className,
  inputClassName,
}: Props) {
  const { tr } = useI18n();
  const clear = onClear ?? (() => onChange(''));
  return (
    <div className={cn('relative min-w-[15rem] flex-1', className)}>
      <Search
        size={13}
        aria-hidden
        className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500"
      />
      <Input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        aria-label={ariaLabel}
        className={cn(
          'h-8 pl-8 pr-8 text-[12px] [&::-webkit-search-cancel-button]:appearance-none',
          inputClassName,
        )}
      />
      {value === '' ? null : (
        <button
          type="button"
          aria-label={tr('清空搜索', 'Clear search')}
          onClick={() => clear()}
          className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-zinc-500 transition-colors hover:bg-zinc-800/60 hover:text-zinc-200"
        >
          <X size={12} aria-hidden />
        </button>
      )}
    </div>
  );
}
