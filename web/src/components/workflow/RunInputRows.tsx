// 节点输入：按变量逐行展示。
//
// 后端只把节点真正读到的变量写进 input_json（代码节点走 AST、其余节点扫
// {{变量}}），所以这里直接按键值行渲染，而不是甩一坨 JSON 给用户。
// 面板「上次运行」和运行详情时间线共用这一个组件，两处观感保持一致。

import { cn } from '@/lib/cn';
import { useI18n } from '@/i18n/locale';

function valueText(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value === null || value === undefined) return 'null';
  return JSON.stringify(value, null, 2);
}

export function RunInputRows({ input }: { input: unknown }) {
  const { tr } = useI18n();

  if (input === null || input === undefined) {
    return (
      <div className="rounded-lg border border-dashed border-border/70 px-2.5 py-2 text-[10.5px] text-text-faint">
        {tr('（无输入）', '(no input)')}
      </div>
    );
  }
  // 单值输入（试跑历史里的纯文本预览）没有变量名可列，原样展示
  if (typeof input !== 'object' || Array.isArray(input)) {
    return (
      <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border/60 bg-bg/60 px-2.5 py-2 font-mono text-[10.5px] leading-4 text-text">
        {valueText(input)}
      </pre>
    );
  }

  const entries = Object.entries(input as Record<string, unknown>);
  if (entries.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border/70 px-2.5 py-2 text-[10.5px] leading-4 text-text-faint">
        {tr('这一步不读取任何变量', 'This step reads no variables')}
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border/60 bg-bg/60">
      {entries.map(([name, value], index) => (
        <div
          key={name}
          className={cn(
            'grid grid-cols-[minmax(0,92px)_1fr] items-start gap-2 px-2.5 py-1.5',
            index > 0 && 'border-t border-border/50',
          )}
        >
          <span className="truncate font-mono text-[10.5px] font-medium leading-4 text-text-muted" title={name}>
            {name}
          </span>
          <pre className="max-h-28 overflow-auto whitespace-pre-wrap break-words font-mono text-[10.5px] leading-4 text-text">
            {valueText(value)}
          </pre>
        </div>
      ))}
    </div>
  );
}
