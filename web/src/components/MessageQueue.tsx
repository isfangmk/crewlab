// MessageQueue — 输入区上方的排队消息条（插话/消息队列）。
//
// 回答中允许把后续消息排进会话队列，当前回合结束后由后端依次消费。
// 每条队列项提供三个常规操作：插话（打断当前、优先执行自己）、修改（原地改正文，
// 位置不变）、删除。视觉遵循 UI 设计规范：状态用 StatusPill（排队中=neutral，
// "N 条排队"=info），行内操作用 RowActionIcon，字号走无衬线体系，不写硬编码色值。

import { useState } from 'react';
import { ListOrdered, X as XIcon, Zap, Pencil, Check, RotateCcw } from 'lucide-react';
import { Button, RowActionIcon, StatusPill } from '@/components/ui';
import { useI18n } from '@/i18n/locale';
import type { QueueItem } from '@/api/chat';

type Props = {
  items: QueueItem[];
  /** 删除一条；父组件负责调 DELETE API 并刷新。 */
  onRemove(itemId: string): void;
  /** 插话：打断当前回合并让该条优先执行。 */
  onInterject(itemId: string): void;
  /** 原地修改正文；位置不变，轮到它时按新正文执行。 */
  onUpdate(itemId: string, content: string): void;
  /** 正在操作的条目 id，用于单次禁用避免重复点击。 */
  busyId?: string | null;
  /** 当前是否有回答在进行；无回答时插话无意义，禁用插话按钮。 */
  canInterject?: boolean;
};

// 单行摘要：折叠空白，避免长正文把队列条撑高。
function summary(text: string, max = 40): string {
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length > max ? `${flat.slice(0, max)}…` : flat;
}

export function MessageQueue({ items, onRemove, onInterject, onUpdate, busyId, canInterject = true }: Props) {
  const { tr } = useI18n();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  if (items.length === 0) return null;

  const startEdit = (item: QueueItem) => {
    setEditingId(item.item_id);
    setDraft(item.content);
  };
  const cancelEdit = () => {
    setEditingId(null);
    setDraft('');
  };
  const saveEdit = (itemId: string) => {
    const next = draft.trim();
    if (!next) return;
    onUpdate(itemId, next);
    cancelEdit();
  };

  return (
    <div className="flex flex-col gap-1.5" data-slot="message-queue">
      <div className="flex items-center gap-2">
        <StatusPill tone="info" label={tr(`排队中 · ${items.length} 条`, `Queued · ${items.length}`)} />
        <span className="text-[10px] text-zinc-600">
          {tr('当前回答结束后依次发送', 'Sent in order after the current reply')}
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {items.map((item, index) => {
          const isEditing = editingId === item.item_id;
          const rowBusy = busyId === item.item_id;
          return (
            <div
              key={item.item_id}
              className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-zinc-800 bg-zinc-900/60 py-1 pl-2 pr-1"
              data-slot="queue-item"
              data-interrupt={item.interrupt ? 'true' : undefined}
            >
              <ListOrdered size={12} className="shrink-0 text-zinc-500" aria-hidden />
              <StatusPill
                tone={item.interrupt ? 'ok' : 'neutral'}
                label={item.interrupt ? tr(`插话 · ${index + 1}`, `Interject · ${index + 1}`) : String(index + 1)}
              />
              {isEditing ? (
                <>
                  <input
                    autoFocus
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (isImeComposingQueue(e)) return;
                      if (e.key === 'Enter') { e.preventDefault(); saveEdit(item.item_id); }
                      if (e.key === 'Escape') { e.preventDefault(); cancelEdit(); }
                    }}
                    aria-label={tr(`修改排队消息 ${index + 1}`, `Edit queued message ${index + 1}`)}
                    className="w-52 rounded-md border border-zinc-700 bg-zinc-950 px-2 py-0.5 text-[11px] text-zinc-100 outline-none focus:border-indigo-500"
                  />
                  <Button
                    variant="subtle"
                    size="sm"
                    type="button"
                    disabled={rowBusy || !draft.trim()}
                    aria-label={tr('保存修改', 'Save edit')}
                    className="h-6 w-6 p-0"
                    onClick={() => saveEdit(item.item_id)}
                  >
                    <Check size={12} />
                  </Button>
                  <RowActionIcon
                    icon={RotateCcw}
                    tone="neutral"
                    label={tr('取消修改', 'Cancel edit')}
                    disabled={rowBusy}
                    onClick={cancelEdit}
                    className="h-6 w-6"
                  />
                </>
              ) : (
                <>
                  <span className="max-w-[16rem] truncate text-[11px] text-zinc-300" title={item.content}>
                    {summary(item.content)}
                  </span>
                  <RowActionIcon
                    icon={Zap}
                    tone={item.interrupt ? 'positive' : 'neutral'}
                    label={tr(`插话并优先执行 ${index + 1}`, `Interject and run first ${index + 1}`)}
                    disabled={rowBusy || !canInterject}
                    onClick={() => onInterject(item.item_id)}
                    className="h-6 w-6"
                  />
                  <RowActionIcon
                    icon={Pencil}
                    tone="neutral"
                    label={tr(`修改排队消息 ${index + 1}`, `Edit queued message ${index + 1}`)}
                    disabled={rowBusy}
                    onClick={() => startEdit(item)}
                    className="h-6 w-6"
                  />
                  <RowActionIcon
                    icon={XIcon}
                    tone="danger"
                    label={tr(`移除排队消息 ${index + 1}`, `Remove queued message ${index + 1}`)}
                    disabled={rowBusy}
                    onClick={() => onRemove(item.item_id)}
                    className="h-6 w-6"
                  />
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// 输入法合成期间不提交，避免中文回车被吞。
function isImeComposingQueue(e: React.KeyboardEvent<HTMLInputElement>): boolean {
  return e.nativeEvent.isComposing || e.keyCode === 229;
}
