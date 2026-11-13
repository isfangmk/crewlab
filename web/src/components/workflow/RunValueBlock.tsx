// 值展示：字段名在内容框外面，值在框内。
//
// 统一口径：字段名是元信息（一条标签行），值才是内容（下面的框）。
// 输入字段与节点输出都走这套结构，形状一致，扫起来不用重新适应。
//
// 值区按「值本身」决定高度：单行值不换行、不撑高整行；多行值才折行，
// 因为它们本来就占多行，横向截断反而看不出内容。

import { useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { cn } from '@/lib/cn';
import { useI18n } from '@/i18n/locale';

type Tone = 'info' | 'ok' | 'danger' | 'warn';

const DOT: Record<Tone, string> = {
  info: 'bg-info/70',
  ok: 'bg-ok/70',
  danger: 'bg-danger/70',
  warn: 'bg-warn/70',
};

const LABEL_TEXT: Record<Tone, string> = {
  info: 'text-info',
  ok: 'text-ok',
  danger: 'text-danger',
  warn: 'text-warn',
};

/** 值转文本：字符串原样，其余按 JSON 缩进。 */
export function valueText(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value === null || value === undefined) return 'null';
  return JSON.stringify(value, null, 2);
}

/** JSON 文本转可读文本：能解析就缩进展示，解析不了当纯文本。 */
export function jsonText(raw: string): string {
  if (raw === '') return '';
  try {
    const parsed: unknown = JSON.parse(raw);
    return typeof parsed === 'string' ? parsed : JSON.stringify(parsed, null, 2);
  } catch {
    return raw;
  }
}

/** 复制按钮：与字段名同行，复制的是这个字段的值。 */
function CopyButton({ text, name }: { text: string; name: string }) {
  const { tr } = useI18n();
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // 剪贴板不可用时保持原样，用户可以直接选中文本
    }
  };

  return (
    <button
      type="button"
      onClick={() => void copy()}
      className="ml-auto shrink-0 rounded px-1 text-text-faint transition-colors hover:bg-zinc-500/10 hover:text-text-muted"
      aria-label={tr(`复制 ${name}`, `Copy ${name}`)}
      title={copied ? tr('已复制', 'Copied') : tr('复制', 'Copy')}
    >
      {copied ? <Check size={10} aria-hidden /> : <Copy size={10} aria-hidden />}
    </button>
  );
}

/**
 * 值的字体：含中文走正文，纯 ASCII 结构走等宽。
 *
 * 等宽字体没有中文字形，中文落到后备字体后字号与字重都对不上；
 * 而字段值经常是中文回答或含中文的 JSON，强行等宽只会让正文变形。
 * 字段名是标识符，固定等宽；值按内容里有没有中文选字体。
 */
const CJK = /[\u3000-\u9fff\uff00-\uffef]/;

function valueFont(text: string): string {
  return CJK.test(text)
    ? 'text-[12px] leading-5 text-text'
    : 'font-mono text-[10.5px] leading-4 text-text';
}

/** 单个字段：标签行在外，内容框在内。 */
export function RunValueBlock({
  label,
  value,
  tone = 'info',
}: {
  label: string;
  value: unknown;
  tone?: Tone;
}) {
  const text = valueText(value);
  const single = !text.includes('\n');
  return (
    <div className="min-w-0">
      {/* 字段名：内容框外面的一条标签行，复制按钮与它同行 */}
      <div className="flex items-center gap-1.5 border-b border-border/50 bg-zinc-500/[0.06] px-2.5 py-1">
        <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', DOT[tone])} aria-hidden />
        <span
          className={cn('min-w-0 truncate font-mono text-[9.5px] leading-4', LABEL_TEXT[tone])}
          title={label}
        >
          {label}
        </span>
        <CopyButton text={text} name={label} />
      </div>
      <div
        className={cn(
          'px-2.5 py-1.5',
          valueFont(text),
          single ? 'truncate' : 'max-h-96 overflow-auto whitespace-pre-wrap break-words',
        )}
        title={single && text.length > 40 ? text : undefined}
      >
        {text}
      </div>
    </div>
  );
}

/** 空状态文案：各调用点共用一套措辞。 */
export function useEmptyText() {
  const { tr } = useI18n();
  return {
    noInput: tr('（无输入）', '(no input)'),
    noOutput: tr('（无输出）', '(no output)'),
    noVars: tr('这一步不读取任何变量', 'This step reads no variables'),
  };
}
