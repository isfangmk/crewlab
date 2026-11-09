import { cn } from '@/lib/cn';

// CodeBlock — payload / result / 参数 JSON 的统一代码块。
// 表面：圆角 8px + zinc-800/60 描边 + zinc-950 底；字号 11px；默认限制高度并内部滚动。
type Props = {
  children: string;
  /** 覆盖默认的 max-h-72。 */
  maxHeightClassName?: string;
  /** true = 自动换行（长 JSON / 堆栈）；false = 横向滚动（表格类数据）。 */
  wrap?: boolean;
  className?: string;
};

export function CodeBlock({ children, maxHeightClassName = 'max-h-72', wrap = true, className }: Props) {
  return (
    <pre
      className={cn(
        'overflow-auto rounded-lg border border-zinc-800/60 bg-zinc-950 px-3 py-2 font-mono text-[11px] leading-5 text-zinc-300',
        maxHeightClassName,
        wrap ? 'whitespace-pre-wrap break-words' : 'whitespace-pre',
        className,
      )}
    >
      {children}
    </pre>
  );
}
