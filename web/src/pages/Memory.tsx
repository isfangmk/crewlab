// 跨会话记忆管理：列表 / 新增 / 删除（语义回忆由对话内 recall_memory 工具完成）。
import { useCallback, useEffect, useState } from 'react';
import { Brain, Loader2, Plus, RefreshCw, Trash2 } from 'lucide-react';
import {
  Button,
  EmptyStateCard,
  FilterBar,
  Input,
  PageHeader,
  RowAction,
  RowActionIcon,
  SearchInput,
  TableCard,
  TableHead,
  TablePager,
  Textarea,
  tableLayout,
  COL,
} from '@/components/ui';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { Modal } from '@/components/Modal';
import { errorText } from '@/lib/errors';
import { fullDateTime } from '@/lib/format';
import { useI18n } from '@/i18n/locale';
import { usePermissions } from '@/store/me';
import { request } from '@/api/client';

interface MemoryItem {
  id: string;
  content: string;
  agent_id: string | null;
  enabled: boolean;
  source_session_id: string | null;
  created_at: string | null;
}

const PAGE_SIZE = 20;

function listMemory(query: string) {
  const qs = query.trim() ? `?q=${encodeURIComponent(query.trim())}` : '';
  return request<{ items: MemoryItem[]; total: number }>('GET', `/memory${qs}`);
}

function createMemory(content: string, agentId: string | null) {
  return request<MemoryItem>('POST', '/memory', { content, agent_id: agentId });
}

function deleteMemory(id: string) {
  return request<{ ok: boolean }>('DELETE', `/memory/${id}`);
}

export default function MemoryPage() {
  const { tr } = useI18n();
  const { canMutate } = usePermissions();
  const [items, setItems] = useState<MemoryItem[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(0);
  const [creating, setCreating] = useState(false);
  const [newContent, setNewContent] = useState('');
  const [saving, setSaving] = useState(false);
  const [formErr, setFormErr] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<MemoryItem | null>(null);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const r = await listMemory(query);
      setItems(r.items);
      setErr(null);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setLoading(false);
    }
  }, [query]);

  useEffect(() => {
    const t = window.setTimeout(() => void load(true), 250);
    return () => window.clearTimeout(t);
  }, [load]);

  const filtered = items ?? [];
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const paged = filtered.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);

  const save = async () => {
    setSaving(true);
    setFormErr(null);
    try {
      await createMemory(newContent, null);
      setNewContent('');
      setCreating(false);
      await load(true);
    } catch (e) {
      setFormErr(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  const openCreate = () => {
    setNewContent('');
    setFormErr(null);
    setCreating(true);
  };

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tr('记忆', 'Memory')}
        subtitle={tr(
          '跨会话长期记忆：对话中可由助理保存，并可按语义回忆',
          'Long-term memory across sessions, saved by assistants and recalled semantically',
        )}
        actions={
          <>
            <RowAction icon={RefreshCw} onClick={() => void load(true)}>
              {tr('刷新', 'Refresh')}
            </RowAction>
            {canMutate && (
              <Button variant="primary" size="sm" onClick={openCreate}>
                <Plus size={12} />
                {tr('新增记忆', 'New memory')}
              </Button>
            )}
          </>
        }
      />
      <FilterBar className="mx-6 mt-3">
        <SearchInput
          value={query}
          onChange={(v) => {
            setQuery(v);
            setPage(0);
          }}
          placeholder={tr('按内容筛选…', 'Filter by content…')}
          ariaLabel={tr('筛选记忆', 'Filter memory')}
          className="min-w-[16rem] flex-1"
        />
        <span className="ml-auto text-[11px] tabular-nums text-zinc-500">
          {tr(`共 ${filtered.length} 条`, `${filtered.length} total`)}
        </span>
      </FilterBar>

      <div className="flex min-h-0 flex-1 flex-col px-6 py-4">
        {err && (
          <div className="mb-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-[12px] text-red-300">
            {err}
          </div>
        )}
        {loading && items === null ? (
          <div className="flex flex-1 items-center justify-center text-sm text-zinc-500">
            <Loader2 size={14} className="mr-2 animate-spin" />
            {tr('加载中…', 'Loading…')}
          </div>
        ) : filtered.length === 0 ? (
          <EmptyStateCard
            icon={Brain}
            title={tr('还没有任何记忆', 'No memories yet')}
            hint={tr(
              '对话中对助理说「记住：…」，或在上面手工新增。',
              'Tell the assistant to remember something, or add one above.',
            )}
          />
        ) : (
          <TableCard
            className="min-h-0 flex-1"
            footer={
              <TablePager
                page={safePage}
                pageSize={PAGE_SIZE}
                total={filtered.length}
                shown={paged.length}
                onPageChange={setPage}
              />
            }
          >
            <table {...tableLayout(64)}>
              <colgroup>
                <col className={COL.main} />
                <col className={COL.tag} />
                <col className={COL.time} />
                <col className={COL.actionsNarrow} />
              </colgroup>
              <TableHead>
                <tr className="whitespace-nowrap">
                  <th scope="col" className="px-3 py-2 text-left font-medium">
                    {tr('内容', 'Content')}
                  </th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">
                    {tr('归属', 'Scope')}
                  </th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">
                    {tr('创建时间', 'Created')}
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    {tr('操作', 'Actions')}
                  </th>
                </tr>
              </TableHead>
              <tbody className="divide-y divide-zinc-800/40">
                {paged.map((m) => (
                  <tr key={m.id} className="group transition-colors hover:bg-zinc-900/40">
                    <td className="px-3 py-2">
                      <span className="line-clamp-2 text-[12.5px] leading-5 text-zinc-200">
                        {m.content}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-[11px] text-zinc-400">
                      {m.agent_id ?? tr('全局', 'Global')}
                    </td>
                    <td className="px-3 py-2 text-[11px] text-zinc-500">
                      {m.created_at ? fullDateTime(m.created_at) : '—'}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <div className="flex items-center justify-end gap-0.5 opacity-60 transition-opacity group-hover:opacity-100">
                        {canMutate && (
                          <RowActionIcon
                            icon={Trash2}
                            label={tr('删除', 'Delete')}
                            aria-label={tr(`删除记忆 ${m.id}`, `Delete memory ${m.id}`)}
                            tone="danger"
                            onClick={() => setDeleting(m)}
                          />
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableCard>
        )}
      </div>

      {creating && (
        <Modal
          open
          onClose={() => setCreating(false)}
          title={tr('新增记忆', 'New memory')}
          size="md"
          footer={
            <>
              <Button variant="ghost" onClick={() => setCreating(false)}>
                {tr('取消', 'Cancel')}
              </Button>
              <Button
                variant="primary"
                size="sm"
                disabled={saving || newContent.trim() === ''}
                onClick={() => void save()}
              >
                {saving && <Loader2 size={12} className="animate-spin" />}
                {tr('保存', 'Save')}
              </Button>
            </>
          }
        >
          <div className="space-y-2">
            <Textarea
              value={newContent}
              onChange={(e) => setNewContent(e.target.value)}
              maxLength={2000}
              placeholder={tr('一条记忆描述一个事实或偏好…', 'One fact or preference per memory…')}
              className="h-32 w-full resize-y"
              autoFocus
            />
            <div className="text-[11px] text-zinc-500">
              {tr(
                '保存后可按语义回忆；一条记忆只描述一个事实。',
                'Recalled semantically after saving; one fact per memory.',
              )}
            </div>
            {formErr && <p className="text-[11px] text-red-400">{formErr}</p>}
          </div>
        </Modal>
      )}
      {deleting && (
        <ConfirmDialog
          open
          title={tr('删除这条记忆', 'Delete this memory')}
          description={deleting.content.slice(0, 120)}
          confirmLabel={tr('删除', 'Delete')}
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            await deleteMemory(deleting.id);
            setDeleting(null);
            await load(true);
          }}
        />
      )}
    </main>
  );
}
