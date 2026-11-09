import { useEffect, useRef, useState } from 'react';
import { Brain, ChevronDown } from 'lucide-react';
import type { ChatMessage } from '@/api/chat';
import { cn } from '@/lib/cn';
import { useI18n } from '@/i18n/locale';

const AUTO_CLOSE_MS = 1000;

/**
 * 思考面板：对齐 Vercel AI Elements Reasoning 交互与层次。
 * 无卡片壳；流式时 shimmer 文案并展开跟滚，结束后显示时长并延迟收起。
 */
export function ThinkingPanel({ message }: { message: ChatMessage }) {
  const { tr } = useI18n();
  const body = message.content ?? '';
  const pending = message.pending === true;
  const [open, setOpen] = useState(pending);
  const [durationSec, setDurationSec] = useState<number | undefined>(undefined);
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const startAtRef = useRef<number | null>(null);
  const hasStreamedRef = useRef(pending);
  const autoClosedRef = useRef(false);

  useEffect(() => {
    if (pending) {
      hasStreamedRef.current = true;
      autoClosedRef.current = false;
      setOpen(true);
      if (startAtRef.current === null) startAtRef.current = Date.now();
      return;
    }
    if (startAtRef.current !== null) {
      setDurationSec(Math.max(1, Math.ceil((Date.now() - startAtRef.current) / 1000)));
      startAtRef.current = null;
    }
  }, [pending]);

  useEffect(() => {
    if (!hasStreamedRef.current || pending || !open || autoClosedRef.current) return;
    if (body === '') return;
    const timer = window.setTimeout(() => {
      setOpen(false);
      autoClosedRef.current = true;
    }, AUTO_CLOSE_MS);
    return () => window.clearTimeout(timer);
  }, [pending, open, body]);

  useEffect(() => {
    if (!pending || !open) return;
    const el = scrollerRef.current;
    if (el === null) return;
    el.scrollTop = el.scrollHeight;
  }, [body, pending, open]);

  if (body === '' && !pending) return null;

  const label = pending || durationSec === 0
    ? tr('思考中…', 'Thinking…')
    : durationSec === undefined
      ? tr('已思考片刻', 'Thought for a few seconds')
      : tr(`思考了 ${durationSec} 秒`, `Thought for ${durationSec} seconds`);

  return (
    <div className="w-full">
      <button
        type="button"
        aria-expanded={open}
        aria-label={label}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          'group flex w-full items-center gap-2 py-0.5 text-left text-sm',
          'text-zinc-500 transition-colors hover:text-zinc-200',
        )}
      >
        <Brain size={16} strokeWidth={1.75} className="shrink-0" />
        {pending ? (
          <span className="thinking-shimmer">{label}</span>
        ) : (
          <span>{label}</span>
        )}
        <ChevronDown
          size={16}
          strokeWidth={1.75}
          className={cn(
            'shrink-0 transition-transform duration-200',
            open ? 'rotate-180' : 'rotate-0',
          )}
        />
      </button>

      {open ? (
        <div
          ref={scrollerRef}
          className={cn(
            'mt-3 max-h-56 overflow-y-auto',
            'text-sm leading-relaxed text-zinc-500',
            'whitespace-pre-wrap break-words',
            'anim-fade',
          )}
        >
          {body === '' && pending ? tr('组织思路…', 'Gathering thoughts…') : body}
        </div>
      ) : null}
    </div>
  );
}
