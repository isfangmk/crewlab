// CommandPalette — ⌘P / Ctrl+P 快速跳转：模糊匹配侧栏路由 + 最近 5 条会话。
//
// 过滤、排序、键盘导航与 ARIA 全部由 cmdk 提供（不自研打分与方向键状态机）；
// 本文件只管数据（路由表 + 最近会话）与跳转行为。

import { Dialog, DialogContent, DialogTitle } from '@/components/ui/Dialog';
import { Command } from 'cmdk';
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, MessageSquare, Compass, MessageCircle } from 'lucide-react';
import { listSessions, searchMessages, type ChatSession, type MessageSearchHit } from '@/api/chat';
import { APP_ROUTES, type AppRoute } from '@/lib/routes';
import { useI18n } from '@/i18n/locale';

type Props = {
  open: boolean;
  onClose(): void;
};

// 选中行高亮与 og-menu-item 同一口径（text 色 7% 中性底）：
// 变体类（data-[selected]）不会被浅色模式的 zinc 重映射命中，不能写死深色。
const ITEM_CLASS =
  'flex w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-left text-[12px] text-text outline-none data-[selected=true]:bg-text/[0.07]';

export function CommandPalette({ open, onClose }: Props) {
  const { tr } = useI18n();
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [recent, setRecent] = useState<ChatSession[]>([]);
  const [msgHits, setMsgHits] = useState<MessageSearchHit[]>([]);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // 每次打开重置查询并拉最近会话。
  useEffect(() => {
    if (!open) return;
    setQuery('');
    let cancelled = false;
    void listSessions({ limit: 5, offset: 0 })
      .then((r) => {
        if (cancelled) return;
        if (!Array.isArray(r.items)) {
          throw new Error('会话列表缺少 items');
        }
        setRecent(r.items);
      })
      .catch(() => {
        if (!cancelled) setRecent([]);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  // 查询变化时搜索消息正文（防抖 250ms，关键词为空则清空）。
  useEffect(() => {
    const q = query.trim();
    if (q === '') {
      setMsgHits([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void searchMessages(q, 5)
        .then((r) => { if (!cancelled) setMsgHits(r.items); })
        .catch(() => { if (!cancelled) setMsgHits([]); });
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [query]);

  if (!open) return null;

  function go(path: string) {
    navigate(path);
    onClose();
  }

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogContent initialFocus={inputRef} className="max-w-xl overflow-hidden" aria-label={tr('命令面板', 'Command palette')}>
        <DialogTitle className="sr-only">{tr('命令面板', 'Command palette')}</DialogTitle>
        <Command loop label={tr('命令面板', 'Command palette')}>
          <div className="flex items-center gap-2 border-b border-zinc-800 px-4 py-3">
            <Search size={14} className="text-zinc-500" />
            <Command.Input
              ref={inputRef}
              value={query}
              onValueChange={setQuery}
              placeholder={tr('跳转到… 搜索路由、会话', 'Go to… search routes, sessions')}
              aria-label={tr('搜索', 'Search')}
              data-variant="inset"
              className="og-input flex-1"
            />
            <kbd className="hidden rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-500 sm:inline">
              Esc
            </kbd>
          </div>

          <Command.List className="max-h-[60vh] overflow-y-auto py-1">
            <Command.Empty className="px-4 py-8 text-center text-[12px] text-zinc-500">
              {tr('无结果', 'No results')}
            </Command.Empty>

            <Command.Group
              heading={
                <div className="flex items-center gap-1.5 px-3 pb-0.5 pt-1 text-[10px] uppercase tracking-wide text-zinc-500">
                  <Compass size={10} />
                  <span>{tr('导航', 'Navigation')}</span>
                </div>
              }
            >
              {APP_ROUTES.map((r: AppRoute) => (
                <Command.Item
                  key={r.path}
                  value={`${r.label} ${r.group} ${r.path} ${(r.keywords ?? []).join(' ')}`}
                  onSelect={() => go(r.path)}
                  className={ITEM_CLASS}
                >
                  <span className="truncate text-inherit">{r.label}</span>
                  <span className="ml-2 truncate text-[11px] text-zinc-500">{r.group}</span>
                  <span className="ml-auto truncate text-[10px] text-zinc-600">{r.path}</span>
                </Command.Item>
              ))}
            </Command.Group>

            <Command.Group
              heading={
                <div className="flex items-center gap-1.5 px-3 pb-0.5 pt-1 text-[10px] uppercase tracking-wide text-zinc-500">
                  <MessageSquare size={10} />
                  <span>{tr('最近会话', 'Recent sessions')}</span>
                </div>
              }
            >
              {recent.map((s, i) => (
                <Command.Item
                  key={s.id}
                  value={`${s.title || tr(`会话 ${i + 1}`, `Session ${i + 1}`)} ${s.id}`}
                  onSelect={() => go(`/chat/${s.id}`)}
                  className={ITEM_CLASS}
                >
                  <span className="truncate text-inherit">
                    {s.title || tr(`会话 ${i + 1}`, `Session ${i + 1}`)}
                  </span>
                  <span className="ml-auto truncate text-[10px] text-zinc-600">{s.id.slice(0, 8)}</span>
                </Command.Item>
              ))}
            </Command.Group>

            {msgHits.length > 0 && (
              <Command.Group
                heading={
                  <div className="flex items-center gap-1.5 px-3 pb-0.5 pt-1 text-[10px] uppercase tracking-wide text-zinc-500">
                    <MessageCircle size={10} />
                    <span>{tr('消息', 'Messages')}</span>
                  </div>
                }
              >
                {msgHits.map((hit) => (
                  <Command.Item
                    key={hit.message_id}
                    value={`${hit.snippet} ${hit.session_title} ${hit.message_id}`}
                    onSelect={() => {
                      navigate(`/chat/${hit.session_id}`, { state: { focusMessageId: hit.message_id } });
                      onClose();
                    }}
                    className={ITEM_CLASS}
                  >
                    <span className="truncate text-inherit">{hit.snippet}</span>
                    <span className="ml-auto truncate text-[10px] text-zinc-600">{hit.session_title}</span>
                  </Command.Item>
                ))}
              </Command.Group>
            )}
          </Command.List>
        </Command>

        <div className="flex items-center justify-between border-t border-zinc-800 px-4 py-2 text-[10px] text-zinc-600">
          <span>{tr('↑↓ 切换 · Enter 跳转 · Esc 关闭', '↑↓ navigate · Enter open · Esc close')}</span>
          <span>⌘P / Ctrl+P</span>
        </div>
      </DialogContent>
    </Dialog>
  );
}
