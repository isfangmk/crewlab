import { create } from 'zustand';
import { listSessions, type ChatSession } from '@/api/chat';

// chatSessionsStore is the single source of truth for the home/chat
// session list. Components that mutate sessions (creating a new one in
// Home, sending the first message that bumps `updated_at`, etc.) call
// invalidateChatSessions() to trigger a refetch so the session list updates
// without a full page reload.
type ChatSessionsState = {
  sessions: ChatSession[];
  loading: boolean;
  error: boolean;
  query: string;
  includeArchived: boolean;
  setQuery(q: string): void;
  setIncludeArchived(v: boolean): void;
  refresh(): Promise<void>;
};

export const useChatSessions = create<ChatSessionsState>((set, get) => ({
  sessions: [],
  loading: false,
  error: false,
  query: '',
  includeArchived: false,
  setQuery(q: string) {
    set({ query: q });
  },
  setIncludeArchived(v: boolean) {
    set({ includeArchived: v });
  },
  async refresh() {
    set({ loading: true, error: false });
    try {
      const { query, includeArchived } = get();
      const r = await listSessions({
        q: query.trim() === '' ? undefined : query.trim(),
        include_archived: includeArchived,
        limit: 50,
        offset: 0,
      });
      if (!Array.isArray(r.items)) {
        throw new Error('会话列表缺少 items');
      }
      set({ sessions: r.items, loading: false, error: false });
    } catch {
      set({ loading: false, error: true });
    }
  },
}));

// invalidateChatSessions triggers a refetch from anywhere in the app.
export function invalidateChatSessions() {
  void useChatSessions.getState().refresh();
}
