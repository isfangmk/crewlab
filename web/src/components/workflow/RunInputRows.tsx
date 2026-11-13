// 节点输入：按变量逐行展示。
//
// 后端只把节点真正读到的变量写进 input_json（代码节点走 AST、其余节点扫
// {{变量}}），所以这里直接按键值行渲染，而不是甩一坨 JSON 给用户。
// 面板「上次运行」和运行详情时间线共用这一个组件，两处观感保持一致。
//
// 结构与输出对齐：字段名在内容框外面（上方一条标签行），值在框内。

import { RunValueBlock, useEmptyText } from '@/components/workflow/RunValueBlock';
import { useI18n } from '@/i18n/locale';

export function RunInputRows({ input }: { input: unknown }) {
  const { tr } = useI18n();
  const empty = useEmptyText();

  if (input === null || input === undefined) {
    return (
      <div className="rounded-lg border border-dashed border-border/70 px-2.5 py-2 text-[10.5px] text-text-faint">
        {empty.noInput}
      </div>
    );
  }
  // 单值输入（试跑历史里的纯文本预览）没有变量名可列，原样展示
  if (typeof input !== 'object' || Array.isArray(input)) {
    return (
      <div className="overflow-hidden rounded-lg border border-border/60 bg-bg/60">
        <RunValueBlock label={tr('input', 'input')} value={input} />
      </div>
    );
  }

  const entries = Object.entries(input as Record<string, unknown>);
  if (entries.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border/70 px-2.5 py-2 text-[10.5px] text-text-faint">
        {empty.noVars}
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border/60 bg-bg/60">
      {entries.map(([name, value], index) => (
        <div key={name} className={index > 0 ? 'border-t border-border/60' : undefined}>
          <RunValueBlock label={name} value={value} />
        </div>
      ))}
    </div>
  );
}
