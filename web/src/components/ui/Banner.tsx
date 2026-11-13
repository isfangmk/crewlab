import { AlertTriangle } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

// 错误条：页面级错误提示的统一外观。
// 各页曾手写 border-red-500/40 bg-red-500/5，这组类在浅色主题下没有对应覆盖，
// 错误底色几乎看不见；这里改用语义色，深浅主题都成立。
type Props = {
  children: ReactNode;
  className?: string;
};

export function ErrorBanner({ children, className }: Props) {
  return (
    <div
      role="alert"
      className={cn(
        'flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[11.5px] leading-5 text-danger',
        className,
      )}
    >
      <AlertTriangle size={12} className="mt-0.5 shrink-0" aria-hidden />
      <span className="min-w-0 flex-1 break-words">{children}</span>
    </div>
  );
}

/** 成功条：与错误条同构，颜色走 ok 语义色。 */
export function SuccessBanner({ children, className }: Props) {
  return (
    <div
      role="status"
      className={cn(
        'flex items-start gap-2 rounded-lg border border-ok/30 bg-ok/10 px-3 py-2 text-[11.5px] leading-5 text-ok',
        className,
      )}
    >
      <span className="min-w-0 flex-1 break-words">{children}</span>
    </div>
  );
}
