import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { MessageBubble } from '@/components/MessageBubble';
import {
  ErrorBanner, PageHeader } from '@/components/ui';
import type { ChatMessage, ChatSession } from '@/api/chat';
import { useI18n } from '@/i18n/locale';
import { errorText } from '@/lib/errors';

/** 只读分享页：无需登录。 */
export default function ShareViewPage() {
  const { token = '' } = useParams<{ token: string }>();
  const { tr } = useI18n();
  const [session, setSession] = useState<ChatSession | null>(null);
  const [items, setItems] = useState<ChatMessage[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch(`/api/v1/chat/shares/${encodeURIComponent(token)}`);
        if (!res.ok) {
          throw new Error(`status=${res.status}`);
        }
        const data = (await res.json()) as {
          session: ChatSession;
          items: ChatMessage[];
        };
        if (cancelled) return;
        setSession(data.session);
        setItems(data.items ?? []);
      } catch (e) {
        if (!cancelled) setErr(errorText(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-3xl flex-col">
      <PageHeader
        title={session?.title ?? tr('分享会话', 'Shared session')}
        subtitle={tr('只读分享链接', 'Read-only share link')}
      />
      <div className="px-6 py-6">
        {err && (
          <ErrorBanner>
            {err}
          </ErrorBanner>
        )}
        <div className="flex flex-col gap-5">
          {items.map((m) => (
            <MessageBubble key={m.id} message={m} />
          ))}
        </div>
      </div>
    </main>
  );
}
