// 节点库：按后端 node_groups 分组，支持点击添加与拖拽到画布。
//
// 版式对齐 Dify 的 block 面板：实色图标块 + 名称，分组标题是 10px 大写小字，
// 行高 30px、悬停才浮出「+」——画布本身是主角，面板不该抢视线。
// 只有一个开始节点：已存在时禁用「开始」，避免用户加了才被后端拒绝。

import { useMemo, useState, type DragEvent } from 'react';
import { Plus, Search } from 'lucide-react';
import type { CatalogGroup, CatalogKind, NodeKind } from '@/api/workflows';
import { cn } from '@/lib/cn';
import { iconOf, toneOf } from '@/lib/workflowMeta';
import { useI18n } from '@/i18n/locale';

type Props = {
  groups: CatalogGroup[];
  kinds: CatalogKind[];
  /** 已存在开始节点时禁用同类型节点。 */
  disabledKinds?: Set<NodeKind>;
  onAdd(kind: NodeKind): void;
  onDragStart(kind: NodeKind, event: DragEvent<HTMLButtonElement>): void;
};

export function WorkflowPalette({ groups, kinds, disabledKinds, onAdd, onDragStart }: Props) {
  const { tr, locale } = useI18n();
  const zh = locale === 'zh-CN';
  const [query, setQuery] = useState('');

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (q === '') return kinds;
    return kinds.filter((kind) => {
      const title = zh ? kind.title : kind.title_en;
      return title.toLowerCase().includes(q) || kind.key.includes(q);
    });
  }, [kinds, query, zh]);

  return (
    <aside className="flex w-[216px] shrink-0 flex-col border-r border-border/70 bg-card/30">
      <div className="shrink-0 px-3 pb-2 pt-3">
        <div className="mb-2 flex items-baseline justify-between">
          <span className="text-[11px] font-semibold text-text">{tr('节点库', 'Node library')}</span>
          <span className="text-[10px] tabular-nums text-text-faint">{kinds.length}</span>
        </div>
        <div className="flex h-8 items-center gap-1.5 rounded-lg border border-border/70 bg-bg/60 px-2 transition-colors focus-within:border-indigo-500/60">
          <Search size={12} className="shrink-0 text-text-faint" aria-hidden />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={tr('搜索节点', 'Search nodes')}
            aria-label={tr('搜索节点', 'Search nodes')}
            className="h-full w-full bg-transparent text-[11.5px] text-text placeholder:text-text-faint focus:outline-none"
          />
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {groups.map((group) => {
          const items = filtered.filter((kind) => kind.group === group.key);
          if (items.length === 0) return null;
          return (
            <div key={group.key} className="mb-3 last:mb-0">
              <div className="px-1.5 pb-1 text-[10px] font-medium uppercase tracking-wider text-text-faint">
                {zh ? group.title : group.title_en}
              </div>
              <div className="space-y-[3px]">
                {items.map((kind) => {
                  const Icon = iconOf(kind.key);
                  const tone = toneOf(kind.key);
                  const disabled = disabledKinds?.has(kind.key) ?? false;
                  return (
                    <button
                      key={kind.key}
                      type="button"
                      draggable={!disabled}
                      disabled={disabled}
                      onDragStart={(event) => onDragStart(kind.key, event)}
                      onClick={() => onAdd(kind.key)}
                      title={disabled ? tr('画布上已有开始节点', 'The canvas already has a start node') : tr(`添加${kind.title}`, `Add ${kind.title_en}`)}
                      className={cn(
                        'group flex w-full items-center gap-2 rounded-lg border border-transparent px-1.5 py-[5px] text-left transition-colors',
                        disabled ? 'cursor-not-allowed opacity-40' : 'hover:border-border/70 hover:bg-bg/70 active:bg-bg',
                      )}
                    >
                      <span className={cn('flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-md shadow-sm', tone.iconBox)}>
                        <Icon size={12} strokeWidth={2.2} className={tone.icon} aria-hidden />
                      </span>
                      <span className="min-w-0 flex-1 truncate text-[11.5px] text-text-muted group-hover:text-text">
                        {zh ? kind.title : kind.title_en}
                      </span>
                      <Plus
                        size={11}
                        className="shrink-0 text-text-faint opacity-0 transition-opacity group-hover:opacity-100"
                        aria-hidden
                      />
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
        {filtered.length === 0 && (
          <div className="px-2 py-6 text-center text-[11px] text-text-faint">{tr('没有匹配的节点', 'No matching nodes')}</div>
        )}
      </div>

      <div className="shrink-0 border-t border-border/70 px-3 py-2 text-[10px] leading-4 text-text-faint">
        {tr('拖到画布，或点击直接添加', 'Drag onto the canvas, or click to add')}
      </div>
    </aside>
  );
}
