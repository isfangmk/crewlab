// MessageQueue — 输入区上方的排队消息条（插话/消息队列）。
//
// 回答中允许把后续消息排进会话队列，当前回合结束后由后端依次消费。
// 本组件只读地展示队列：内容摘要 + 位置序号 + 删除按钮，空队列整条隐藏。
// 视觉遵循 UI 设计规范：状态用 StatusPill（排队中=neutral，"N 条排队"=info），
// 行内删除用 RowActionIcon，字号走无衬线体系，不写硬编码色值。

import { ListOrdered, X as XIcon } from 'lucide-react';
import { RowActionIcon, StatusPill } from '@/components/ui';
import { useI18n } from '@/i18n/locale';
import type { QueueItem } from '@/api/chat';

type Props = {
  items: QueueItem[];
  /** 删除一条；父组件负责调 DELETE API 并刷新。 */
  onRemove(itemId: string): void;
  /** 正在删除的条目 id，用于单次禁用避免重复点击。 */
  removingId?: string | null;
};

// 单行摘要：折叠空白，避免长正文把队列条撑高。
function summary(text: string, max = 40): string {
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length > max ? `${flat.slice(0, max)}…` : flat;
}

export function MessageQueue({ items, onRemove, removingId }: Props) {
  const { tr } = useI18n();
  if (items.length === 0) return null;

  return (
    <div className="flex flex-col gap-1.5" data-slot="message-queue">
      <div className="flex items-center gap-2">
        <StatusPill tone="info" label={tr(`排队中 · ${items.length} 条`, `Queued · ${items.length}`)} />
        <span className="text-[10px] text-zinc-600">
          {tr('当前回答结束后依次发送', 'Sent in order after the current reply')}
        </span>
      </div>
      <div className="flex items-center gap-1.5 overflow-x-auto pb-0.5">
        {items.map((item, index) => (
          <div
            key={item.item_id}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-zinc-800 bg-zinc-900/60 py-1 pl-2 pr-1"
          >
            <ListOrdered size={12} className="shrink-0 text-zinc-500" aria-hidden />
            <StatusPill tone="neutral" label={String(index + 1)} />
            <span className="max-w-[16rem] truncate text-[11px] text-zinc-300" title={item.content}>
              {summary(item.content)}
            </span>
            <RowActionIcon
              icon={XIcon}
              tone="neutral"
              label={tr(`移除排队消息 ${index + 1}`, `Remove queued message ${index + 1}`)}
              disabled={removingId === item.item_id}
              onClick={() => onRemove(item.item_id)}
              className="h-6 w-6"
            />
          </div>
        ))}
      </div>
    </div>
  );
}
