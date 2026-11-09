import { forwardRef, type ButtonHTMLAttributes } from 'react';
import { cn } from '@/lib/cn';
import type { IconType } from '@/lib/icon';

export type RowActionTone = 'neutral' | 'positive' | 'danger';

const BASE =
  'inline-flex shrink-0 items-center justify-center whitespace-nowrap rounded-md border transition-colors disabled:cursor-not-allowed disabled:opacity-50';

const TONE_TEXT: Record<RowActionTone, string> = {
  neutral: 'border-border bg-card text-text hover:bg-text/[0.06]',
  positive: 'border-border bg-card text-ok hover:bg-ok/10 hover:text-ok',
  danger: 'border-border bg-card text-danger hover:border-danger/40 hover:bg-danger/10',
};

const ICON_TONE: Record<RowActionTone, string> = {
  neutral: 'text-text-muted hover:bg-text/[0.06] hover:text-text',
  positive: 'text-ok hover:bg-ok/10 hover:text-ok',
  danger: 'text-danger hover:bg-danger/10 hover:text-danger',
};

/**
 * 行内文字操作的统一类名。导出给 `Link` 等非 button 元素复用，
 * 保证「打开会话」这类跳转与真实按钮完全同高、同描边、同字号。
 */
export function rowActionClass(tone: RowActionTone = 'neutral'): string {
  return cn(BASE, 'h-7 gap-1 px-2 text-[11px] font-medium', TONE_TEXT[tone]);
}

/** 行内图标操作的统一类名（28×28，无边框，hover 才出现底色）。 */
export function rowActionIconClass(tone: RowActionTone = 'neutral'): string {
  return cn(
    'inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md transition-colors disabled:cursor-not-allowed disabled:opacity-50',
    ICON_TONE[tone],
  );
}

type RowActionProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'type'> & {
  icon: IconType;
  tone?: RowActionTone;
  /** 图标动效（如加载中的 spin）；尺寸与颜色仍由组件统一控制。 */
  iconClassName?: string;
  children: string;
};

// 列表行内文字操作：固定 28px 高、11px 字号、12px 图标，左右内边距 8px。
// 表格里所有「批准 / 运行 / 拒绝」这类动作都走这里，避免各页各写一套尺寸。
export const RowAction = forwardRef<HTMLButtonElement, RowActionProps>(function RowAction(
  { icon: Icon, tone = 'neutral', iconClassName, children, className, ...props },
  ref,
) {
  return (
    <button ref={ref} type="button" data-slot="button" className={cn(rowActionClass(tone), className)} {...props}>
      <Icon size={12} className={iconClassName} aria-hidden />
      {children}
    </button>
  );
});

type RowActionIconProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'type' | 'children'> & {
  icon: IconType;
  /** 必填：图标按钮没有可见文字，无障碍名称只能来自这里。 */
  label: string;
  tone?: RowActionTone;
};

// 列表行内图标操作：与 RowAction 同高同圆角，只是收成正方形。
export const RowActionIcon = forwardRef<HTMLButtonElement, RowActionIconProps>(function RowActionIcon(
  { icon: Icon, label, tone = 'neutral', className, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      data-slot="button"
      aria-label={label}
      title={label}
      className={cn(rowActionIconClass(tone), className)}
      {...props}
    >
      <Icon size={13} aria-hidden />
    </button>
  );
});
