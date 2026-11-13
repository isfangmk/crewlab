import { useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { useI18n } from '@/i18n/locale';
import { useTimeout } from '@/lib/useTimeout';

// MetaRow — 详情页的等宽键值行，可选一键复制。
// 用于 trace / 审批之类的编号、路径、时间字段。
type Props = {
  label: string;
  value: string;
  copyable?: boolean;
};

export function MetaRow({ label, value, copyable }: Props) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] uppercase tracking-wide text-zinc-600">{label}</dt>
      <dd className="flex min-w-0 items-center gap-1.5">
        <span className="min-w-0 flex-1 break-all font-mono text-[11px] text-zinc-300" title={value}>
          {value}
        </span>
        {copyable ? <CopyButton value={value} label={label} /> : null}
      </dd>
    </div>
  );
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const { tr } = useI18n();
  const [copied, setCopied] = useState(false);
  const copyTimer = useTimeout();
  const copy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    copyTimer(() => setCopied(false), 1500);
  };
  return (
    <button
      type="button"
      aria-label={tr(`复制 ${label}`, `Copy ${label}`)}
      onClick={() => void copy()}
      className="shrink-0 rounded p-1 text-zinc-600 transition-colors hover:bg-zinc-800/60 hover:text-zinc-300"
    >
      {copied ? <Check size={11} aria-hidden /> : <Copy size={11} aria-hidden />}
    </button>
  );
}
