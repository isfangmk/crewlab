import { useEffect, useRef, useState } from 'react';
import { Eraser, FlaskConical, Send, Square, X } from 'lucide-react';

import {
  listModels,
  type ChatMessage,
  type ModelCatalog,
  type ModelSelection,
} from '@/api/chat';
import { playgroundStream, type UserAgentInput } from '@/api/agents';
import { ApiError } from '@/api/client';
import { MessageBubble } from '@/components/MessageBubble';
import {
  ModelDropdown,
  ThinkingToggle,
  WebSearchToggle,
} from '@/components/ChatModelControls';
import { Button } from '@/components/ui/Button';
import { Textarea } from '@/components/ui/Textarea';
import { EmptyStateCard } from '@/components/ui/EmptyState';
import { useI18n } from '@/i18n/locale';

/** 助理草稿试跑面板：不落会话、不落观测，只在内存里跑一轮。 */
export function AgentPlaygroundPanel({
  agent,
  onClose,
}: {
  agent: UserAgentInput;
  onClose: () => void;
}) {
  const { tr, locale } = useI18n();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [thinking, setThinking] = useState(false);
  const [webSearch, setWebSearch] = useState(false);
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [model, setModel] = useState<ModelSelection | null>(null);
  const active = useRef<AbortController | null>(null);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    listModels()
      .then((c) => {
        if (!alive) return;
        setCatalog(c);
        setModel(c.default);
      })
      .catch(() => {
        // 模型目录拉不到不阻塞试跑，后端会按草稿/全局默认解析模型
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  function stop() {
    active.current?.abort();
    active.current = null;
    setBusy(false);
  }

  function clear() {
    if (busy) return;
    setMessages([]);
    setError('');
    setDraft('');
  }

  async function send() {
    const question = draft.trim();
    if (!question || busy) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError('');
    setMessages((old) => [
      ...old,
      { id: `user-${old.length}`, role: 'user', content: question },
      { id: `reply-${old.length}`, role: 'assistant', pending: true },
    ]);
    setDraft('');
    const appendDelta = (delta: string) => {
      if (controller.signal.aborted) return;
      setMessages((old) =>
        old.map((m, i) =>
          i === old.length - 1 ? { ...m, content: (m.content ?? '') + delta } : m,
        ),
      );
    };
    try {
      await playgroundStream(
        {
          agent,
          content: question,
          provider: model?.provider,
          model: model?.model,
          thinkingEnabled: thinking,
          webSearchEnabled: webSearch,
          locale,
        },
        {
          onAssistantDelta: (e) => {
            if (e.delta) appendDelta(e.delta);
          },
          onThinkingDelta: () => {
            // 思考链不进正文，仅保留正文增量
          },
          onDone: (reply) => {
            const content = reply.assistant_message?.content;
            if (typeof content === 'string' && content !== '') {
              setMessages((old) =>
                old.map((m, i) => (i === old.length - 1 ? { ...m, content } : m)),
              );
            }
          },
          onError: (err) => {
            throw err;
          },
        },
        controller.signal,
      );
    } catch (e) {
      if (!controller.signal.aborted) {
        setError(e instanceof ApiError ? e.message : String(e));
      }
    } finally {
      if (active.current === controller) active.current = null;
      setMessages((old) => old.map((m) => ({ ...m, pending: false })));
      setBusy(false);
    }
  }

  return (
    <div
      className="flex h-full min-h-0 flex-col rounded-xl border border-zinc-800/60 bg-zinc-900/40"
      aria-label={tr('助理试跑', 'Agent playground')}
    >
      <div className="flex items-center gap-2 border-b border-zinc-800/60 px-3 py-2">
        <FlaskConical size={15} className="text-indigo-300" aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs font-medium text-zinc-200">
            {tr('试跑', 'Playground')}
          </div>
          <div className="truncate text-[11px] text-zinc-500">
            {tr('试跑不落会话、不影响正式对话', 'Preview only — not saved to any session')}
          </div>
        </div>
        <ThinkingToggle enabled={thinking} onToggle={setThinking} disabled={busy} />
        <WebSearchToggle enabled={webSearch} onToggle={setWebSearch} disabled={busy} />
        <Button
          variant="plain"
          size="sm"
          onClick={clear}
          disabled={busy || messages.length === 0}
          title={tr('清空', 'Clear')}
          aria-label={tr('清空试跑记录', 'Clear preview')}
          className="w-7 p-0 text-text-muted hover:bg-muted"
        >
          <Eraser size={15} />
        </Button>
        <Button
          variant="plain"
          size="sm"
          onClick={onClose}
          title={tr('关闭', 'Close')}
          aria-label={tr('关闭试跑面板', 'Close preview')}
          className="w-7 p-0 text-text-muted hover:bg-muted"
        >
          <X size={15} />
        </Button>
      </div>

      <div className="flex items-center gap-2 border-b border-zinc-800/60 px-3 py-1.5">
        <span className="text-[11px] text-zinc-500">{tr('模型', 'Model')}</span>
        <ModelDropdown
          disabled={busy}
          providers={catalog?.providers ?? []}
          selected={model}
          onPick={setModel}
        />
      </div>

      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {messages.length === 0 ? (
          <EmptyStateCard
            title={tr('还没有试跑记录', 'No preview yet')}
            hint={tr(
              '在下方输入一句话，看看这份草稿会怎么回答。',
              'Send a prompt below to see how this draft responds.',
            )}
          />
        ) : (
          <div className="flex flex-col gap-2">
            {messages.map((m) => (
              <MessageBubble key={m.id} message={m} actionsDisabled />
            ))}
          </div>
        )}
        {error !== '' && (
          <div
            role="alert"
            className="mt-2 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300"
          >
            {error}
          </div>
        )}
      </div>

      <div className="border-t border-zinc-800/60 p-2">
        <div className="flex items-end gap-2">
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            placeholder={tr('输入一句话试试这份草稿…', 'Send a prompt to try this draft…')}
            aria-label={tr('试跑输入', 'Preview input')}
            rows={2}
            className="min-h-[2.5rem] flex-1"
            disabled={busy}
          />
          {busy ? (
            <Button
              variant="outline"
              size="sm"
              onClick={stop}
              aria-label={tr('停止', 'Stop')}
              className="h-8 px-2"
            >
              <Square size={13} aria-hidden />
            </Button>
          ) : (
            <Button
              variant="primary"
              size="sm"
              onClick={() => void send()}
              disabled={draft.trim() === ''}
              aria-label={tr('发送', 'Send')}
              className="h-8 px-2"
            >
              <Send size={13} aria-hidden />
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

export default AgentPlaygroundPanel;
