import { useAssistantPage } from '@/lib/assistantPage';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Archive,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Download,
  MessageSquare,
  Pin,
  PinOff,
  RefreshCw,
  SquarePen,
  Trash2,
} from 'lucide-react';
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
  type RowSelectionState,
  type SortingState,
} from '@tanstack/react-table';
import {
  batchDeleteSessions,
  deleteSession,
  exportSessionMarkdown,
  listSessions,
  updateSessionFlags,
  type ChatSession,
  type SessionSortBy,
  type SessionSortDir,
} from '@/api/chat';
import { listAgents, localizedAgent, type AgentSummary } from '@/api/agents';
import { ApiError } from '@/api/client';
import { AgentBadge } from '@/components/AgentBadge';
import {
  Button,
  EmptyStateCard,
  FilterBar,
  FilterPill,
  Input,
  Label,
  PageHeader,
  RowAction,
  RowActionIcon,
  SearchInput,
  StatusPill,
  TableCard,
  TablePager,
} from '@/components/ui';
import type { StatusTone } from '@/components/ui/StatusPill';
import { Checkbox } from '@/components/ui/Checkbox';
import { Select } from '@/components/ui/Select';
import { Hint } from '@/components/ui/Tooltip';
import { useI18n } from '@/i18n/locale';
import { cn } from '@/lib/cn';
import { fullDateTime } from '@/lib/format';
import { invalidateChatSessions } from '@/store/chatSessions';

const PAGE_SIZE = 20;

type TimePreset = 'all' | 'today' | '7d' | '30d' | 'custom';

/** 分页列表只开放「更新」这一时间列排序；其他列保持服务端默认顺序。 */
const SORTABLE_IDS = new Set<SessionSortBy>(['updated_at']);

/** 把本地日界/datetime-local 转成 ISO，供 updated_from/to 查询。 */
function toIso(date: Date): string {
  return date.toISOString();
}

function startOfLocalDay(d = new Date()): Date {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  return x;
}

function rangeForPreset(preset: TimePreset): { from?: string; to?: string } {
  if (preset === 'all') return {};
  const now = new Date();
  if (preset === 'today') {
    return { from: toIso(startOfLocalDay(now)), to: toIso(now) };
  }
  if (preset === '7d') {
    const from = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
    return { from: toIso(from), to: toIso(now) };
  }
  if (preset === '30d') {
    const from = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
    return { from: toIso(from), to: toIso(now) };
  }
  return {};
}

function parseLocalDateTime(value: string): string {
  if (value === '') {
    throw new Error('自定义时间不能为空');
  }
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) {
    throw new Error(`非法时间: ${value}`);
  }
  return d.toISOString();
}

function sortingToApi(sorting: SortingState): {
  sort_by?: SessionSortBy;
  sort_dir?: SessionSortDir;
} {
  const first = sorting[0];
  if (!first) return {};
  if (!SORTABLE_IDS.has(first.id as SessionSortBy)) {
    throw new Error(`不支持的排序列: ${first.id}`);
  }
  return {
    sort_by: first.id as SessionSortBy,
    sort_dir: first.desc ? 'desc' : 'asc',
  };
}

type RowActions = {
  busyId: string;
  onPin(session: ChatSession): void;
  onArchive(session: ChatSession): void;
  onExport(session: ChatSession): void;
  onDelete(session: ChatSession): void;
};

const columnHelper = createColumnHelper<ChatSession>();

/** 会话管理页：TanStack Table 负责勾选/表头排序；筛选与分页仍走服务端。 */
export default function SessionsPage() {
  const { tr } = useI18n();
  const navigate = useNavigate();
  const [items, setItems] = useState<ChatSession[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState('');
  const [includeArchived, setIncludeArchived] = useState(false);
  const [agentId, setAgentId] = useState('');
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [timePreset, setTimePreset] = useState<TimePreset>('all');
  const [customFrom, setCustomFrom] = useState('');
  const [customTo, setCustomTo] = useState('');
  const [page, setPage] = useState(0);
  const [sorting, setSorting] = useState<SortingState>([]);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busyId, setBusyId] = useState('');
  const [batchBusy, setBatchBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void listAgents()
      .then((res) => {
        if (cancelled) return;
        if (!Array.isArray(res.items)) {
          throw new Error('助理列表缺少 items');
        }
        setAgents(res.items.map(localizedAgent));
      })
      .catch((e) => {
        if (!cancelled) {
          setErr(e instanceof ApiError ? e.message : (e as Error).message);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const load = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true);
    else setLoading(true);
    try {
      let updatedFrom: string | undefined;
      let updatedTo: string | undefined;
      if (timePreset === 'custom') {
        if (customFrom === '' || customTo === '') {
          throw new Error('自定义时间范围必须同时填写起止时间');
        }
        updatedFrom = parseLocalDateTime(customFrom);
        updatedTo = parseLocalDateTime(customTo);
        if (updatedFrom > updatedTo) {
          throw new Error('开始时间不能晚于结束时间');
        }
      } else {
        const range = rangeForPreset(timePreset);
        updatedFrom = range.from;
        updatedTo = range.to;
      }
      const sort = sortingToApi(sorting);
      const res = await listSessions({
        q: query.trim() === '' ? undefined : query.trim(),
        include_archived: includeArchived,
        agent_id: agentId === '' ? undefined : agentId,
        updated_from: updatedFrom,
        updated_to: updatedTo,
        ...sort,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      });
      if (!Array.isArray(res.items)) {
        throw new Error('会话列表缺少 items');
      }
      if (typeof res.total !== 'number') {
        throw new Error('会话列表缺少 total');
      }
      setItems(res.items);
      setTotal(res.total);
      setErr(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [query, includeArchived, agentId, timePreset, customFrom, customTo, page, sorting]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void load();
    }, 250);
    return () => window.clearTimeout(timer);
  }, [load]);

  // 数据页变化后清空勾选，避免跨页误删
  useEffect(() => {
    setRowSelection({});
  }, [items]);

  const resetFiltersToFirstPage = () => {
    setPage(0);
  };

  const runFlag = async (session: ChatSession, flags: { pinned?: boolean; archived?: boolean }) => {
    setBusyId(session.id);
    try {
      await updateSessionFlags(session.id, flags);
      invalidateChatSessions();
      await load(true);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setBusyId('');
    }
  };

  const runDelete = async (session: ChatSession) => {
    if (!window.confirm(tr(`确定删除会话「${session.title || session.id}」？`, `Delete session "${session.title || session.id}"?`))) {
      return;
    }
    setBusyId(session.id);
    try {
      await deleteSession(session.id);
      invalidateChatSessions();
      await load(true);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setBusyId('');
    }
  };

  const runExport = async (session: ChatSession) => {
    setBusyId(session.id);
    try {
      const md = await exportSessionMarkdown(session.id);
      const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${session.title || session.id}.md`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setBusyId('');
    }
  };

  const rowActions: RowActions = useMemo(
    () => ({
      busyId,
      onPin: (session) => void runFlag(session, { pinned: !session.pinned }),
      onArchive: (session) => void runFlag(session, { archived: !session.archived }),
      onExport: (session) => void runExport(session),
      onDelete: (session) => void runDelete(session),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 动作闭包依赖最新 busy/load
    [busyId, items],
  );

  const columns = useMemo(
    () => [
      columnHelper.display({
        id: 'select',
        header: ({ table }) => (
          <Checkbox
            aria-label={tr('全选本页', 'Select page')}
            checked={table.getIsAllPageRowsSelected()}
            indeterminate={table.getIsSomePageRowsSelected() && !table.getIsAllPageRowsSelected()}
            onCheckedChange={(v) => table.toggleAllPageRowsSelected(v === true)}
          />
        ),
        cell: ({ row }) => (
          <Checkbox
            aria-label={tr('选择会话', 'Select session')}
            checked={row.getIsSelected()}
            disabled={!row.getCanSelect()}
            onCheckedChange={(v) => row.toggleSelected(v === true)}
          />
        ),
        size: 40,
        enableSorting: false,
      }),
      columnHelper.accessor('title', {
        id: 'title',
        header: tr('标题', 'Title'),
        enableSorting: false,
        cell: ({ row }) => {
          const session = row.original;
          const title = session.title || session.id;
          return (
            <Link
              to={`/chat/${session.id}`}
              title={title}
              className="flex min-w-0 items-center gap-1.5 text-[13px] font-medium text-zinc-100 hover:underline"
            >
              {session.pinned ? <Pin size={12} className="shrink-0 text-amber-400" aria-hidden /> : null}
              <span className="truncate">{title}</span>
            </Link>
          );
        },
      }),
      columnHelper.accessor('agent_id', {
        id: 'agent_id',
        header: tr('助理', 'Agent'),
        enableSorting: false,
        cell: ({ getValue }) => {
          const id = getValue();
          return id ? <AgentBadge agentId={id} size="xs" /> : <span className="text-[12px] text-zinc-600">—</span>;
        },
      }),
      columnHelper.accessor('model', {
        id: 'model',
        header: tr('模型', 'Model'),
        enableSorting: false,
        cell: ({ getValue }) => {
          const model = getValue();
          return (
            <span className="block truncate font-mono text-[11px] text-zinc-500" title={model || undefined}>
              {model || '—'}
            </span>
          );
        },
      }),
      columnHelper.accessor((row) => row.updated_at || row.created_at, {
        id: 'updated_at',
        header: tr('更新', 'Updated'),
        cell: ({ getValue }) => {
          const raw = getValue();
          return (
            <span className="whitespace-nowrap text-[12px] tabular-nums text-zinc-500">
              {raw ? fullDateTime(raw) : '—'}
            </span>
          );
        },
      }),
      columnHelper.display({
        id: 'status',
        header: tr('状态', 'Status'),
        enableSorting: false,
        cell: ({ row }) => {
          const session = row.original;
          const state: { tone: StatusTone; zh: string; en: string } = session.archived
            ? { tone: 'warn', zh: '已归档', en: 'Archived' }
            : session.pinned
              ? { tone: 'info', zh: '已置顶', en: 'Pinned' }
              : { tone: 'ok', zh: '进行中', en: 'Active' };
          return <StatusPill tone={state.tone} label={tr(state.zh, state.en)} />;
        },
      }),
      columnHelper.display({
        id: 'actions',
        header: () => <span className="block text-right">{tr('操作', 'Actions')}</span>,
        enableSorting: false,
        cell: ({ row }) => {
          const session = row.original;
          const busy = rowActions.busyId === session.id;
          return (
            <div className="inline-flex w-full items-center justify-end gap-0.5">
              <Hint content={session.pinned ? tr('取消置顶', 'Unpin') : tr('置顶', 'Pin')}>
                <RowActionIcon
                  icon={session.pinned ? PinOff : Pin}
                  label={session.pinned ? tr('取消置顶', 'Unpin') : tr('置顶', 'Pin')}
                  disabled={busy}
                  onClick={() => rowActions.onPin(session)}
                />
              </Hint>
              <Hint content={session.archived ? tr('取消归档', 'Unarchive') : tr('归档', 'Archive')}>
                <RowActionIcon
                  icon={Archive}
                  label={session.archived ? tr('取消归档', 'Unarchive') : tr('归档', 'Archive')}
                  disabled={busy}
                  onClick={() => rowActions.onArchive(session)}
                />
              </Hint>
              <Hint content={tr('导出 Markdown', 'Export Markdown')}>
                <RowActionIcon
                  icon={Download}
                  label={tr('导出 Markdown', 'Export Markdown')}
                  disabled={busy}
                  onClick={() => rowActions.onExport(session)}
                />
              </Hint>
              <Hint content={tr('删除', 'Delete')}>
                <RowActionIcon
                  icon={Trash2}
                  label={tr('删除', 'Delete')}
                  tone="danger"
                  disabled={busy}
                  onClick={() => rowActions.onDelete(session)}
                />
              </Hint>
            </div>
          );
        },
      }),
    ],
    [tr, rowActions],
  );

  const table = useReactTable({
    data: items,
    columns,
    state: { sorting, rowSelection },
    onSortingChange: (updater) => {
      setPage(0);
      setSorting(updater);
    },
    onRowSelectionChange: setRowSelection,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => row.id,
    manualSorting: true,
    enableRowSelection: true,
  });

  const selectedIds = table.getSelectedRowModel().rows.map((r) => r.original.id);

  const runBatchDelete = async () => {
    if (selectedIds.length === 0) {
      throw new Error('未选择会话');
    }
    if (
      !window.confirm(
        tr(`确定删除选中的 ${selectedIds.length} 个会话？`, `Delete ${selectedIds.length} selected sessions?`),
      )
    ) {
      return;
    }
    setBatchBusy(true);
    try {
      const res = await batchDeleteSessions(selectedIds);
      if (typeof res.deleted !== 'number') {
        throw new Error('批量删除响应缺少 deleted');
      }
      setRowSelection({});
      invalidateChatSessions();
      await load(true);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setBatchBusy(false);
    }
  };

  const agentOptions = useMemo(
    () => [
      { value: '', label: tr('全部助理', 'All assistants') },
      ...agents.map((a) => ({ value: a.name, label: a.name })),
    ],
    [agents, tr],
  );

  useAssistantPage('会话管理', 'Sessions', {
    count: total,
    page,
    query,
    include_archived: includeArchived,
    agent_id: agentId,
    time_preset: timePreset,
    sort: sorting[0] ? `${sorting[0].id}:${sorting[0].desc ? 'desc' : 'asc'}` : 'default',
    selected: selectedIds.length,
    loading,
  });

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tr('会话', 'Sessions')}
        subtitle={tr('搜索、排序、批量删除与分页管理', 'Search, sort, batch delete, and pagination')}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {selectedIds.length > 0 ? (
              <Button
                variant="danger"
                size="sm"
                type="button"
                disabled={batchBusy}
                onClick={() => void runBatchDelete()}
                className="inline-flex items-center gap-1.5"
              >
                <Trash2 size={13} />
                {tr(`删除所选 (${selectedIds.length})`, `Delete selected (${selectedIds.length})`)}
              </Button>
            ) : null}
            <Button
              variant="primary"
              size="sm"
              type="button"
              onClick={() => navigate('/')}
              className="inline-flex items-center gap-1.5"
            >
              <SquarePen size={13} />
              {tr('新建会话', 'New chat')}
            </Button>
            <RowAction
              icon={RefreshCw}
              iconClassName={refreshing ? 'animate-spin' : undefined}
              disabled={loading || refreshing}
              onClick={() => void load(true)}
            >
              {tr('刷新', 'Refresh')}
            </RowAction>
          </div>
        }
      />

      <FilterBar className="mx-6 mt-3">
        <SearchInput
          value={query}
          onChange={(v) => {
            resetFiltersToFirstPage();
            setQuery(v);
          }}
          placeholder={tr('搜索标题或模型…', 'Search title or model…')}
          ariaLabel={tr('搜索', 'Search')}
          className="min-w-[16rem] flex-1"
        />
        <FilterPill
          label={tr('助理', 'Assistant')}
          active={agentId !== ''}
          onClear={() => {
            resetFiltersToFirstPage();
            setAgentId('');
          }}
          width="w-[10.5rem]"
        >
          <Select
            aria-label={tr('助理', 'Assistant')}
            value={agentId}
            onValueChange={(v) => {
              resetFiltersToFirstPage();
              setAgentId(v);
            }}
            options={agentOptions}
            variant="ghost"
            className="h-7 min-w-0 border-0 bg-transparent px-0 text-[12px] shadow-none"
          />
        </FilterPill>
        <FilterPill
          label={tr('时间', 'Time')}
          active={timePreset !== 'all'}
          onClear={() => {
            resetFiltersToFirstPage();
            setTimePreset('all');
            setCustomFrom('');
            setCustomTo('');
          }}
          width="w-[9rem]"
        >
          <Select
            aria-label={tr('时间范围', 'Time range')}
            value={timePreset}
            onValueChange={(v) => {
              resetFiltersToFirstPage();
              setTimePreset(v as TimePreset);
            }}
            options={[
              { value: 'all', label: tr('全部时间', 'All time') },
              { value: 'today', label: tr('今天', 'Today') },
              { value: '7d', label: tr('近 7 天', 'Last 7 days') },
              { value: '30d', label: tr('近 30 天', 'Last 30 days') },
              { value: 'custom', label: tr('自定义', 'Custom') },
            ]}
            variant="ghost"
            className="h-7 min-w-0 border-0 bg-transparent px-0 text-[12px] shadow-none"
          />
        </FilterPill>
        {timePreset === 'custom' ? (
          <>
            <Input
              type="datetime-local"
              aria-label={tr('开始时间', 'Start')}
              value={customFrom}
              onChange={(e) => {
                resetFiltersToFirstPage();
                setCustomFrom(e.target.value);
              }}
              className="h-8 w-[12.5rem] text-[12px]"
            />
            <span aria-hidden className="text-[11px] text-zinc-600">→</span>
            <Input
              type="datetime-local"
              aria-label={tr('结束时间', 'End')}
              value={customTo}
              onChange={(e) => {
                resetFiltersToFirstPage();
                setCustomTo(e.target.value);
              }}
              className="h-8 w-[12.5rem] text-[12px]"
            />
          </>
        ) : null}
        <Label className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-zinc-800/60 bg-zinc-900/50 px-2 text-[11px] text-zinc-400">
          <Checkbox
            checked={includeArchived}
            onCheckedChange={(v) => {
              resetFiltersToFirstPage();
              setIncludeArchived(v === true);
            }}
          />
          {tr('含归档', 'Include archived')}
        </Label>
        <span className="ml-auto text-[11px] tabular-nums text-zinc-500">
          {tr(`共 ${total} 个`, `${total} total`)}
        </span>
      </FilterBar>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden px-6 py-4">
        {err && (
          <div className="mb-3 rounded-lg border border-red-500/40 bg-red-500/5 px-4 py-3 text-sm text-red-300">
            {err}
          </div>
        )}
        <div className="flex min-h-0 flex-1 flex-col">
          {loading ? (
            <div className="flex h-40 items-center justify-center text-sm text-zinc-500">
              {tr('加载中…', 'Loading…')}
            </div>
          ) : items.length === 0 ? (
            <EmptyStateCard
              icon={MessageSquare}
              title={
                query.trim() !== '' || agentId !== '' || timePreset !== 'all'
                  ? tr('没有匹配的会话', 'No matching chats')
                  : tr('还没有会话', 'No sessions yet')
              }
              action={
                <Button variant="primary" onClick={() => navigate('/')}>
                  <SquarePen size={12} /> {tr('去首页新建', 'New chat on Home')}
                </Button>
              }
            />
          ) : (
            <TableCard
              footer={
                <TablePager
                  page={page}
                  pageSize={PAGE_SIZE}
                  total={total}
                  shown={items.length}
                  loading={loading || refreshing || batchBusy}
                  onPageChange={setPage}
                />
              }
            >
                <table className="og-action-table w-full min-w-[60rem] table-fixed text-sm">
                  <colgroup>
                    <col className="w-[2.5rem]" />
                    <col className="w-[17rem]" />
                    <col className="w-[8rem]" />
                    <col className="w-[9rem]" />
                    <col className="w-[9.5rem]" />
                    <col className="w-[5.5rem]" />
                    <col className="w-[8.5rem]" />
                  </colgroup>
                  <thead className="sticky top-0 z-10 border-b border-zinc-800/60 bg-zinc-900/95 text-[11px] text-zinc-500 backdrop-blur">
                    {table.getHeaderGroups().map((hg) => (
                      <tr key={hg.id} className="whitespace-nowrap">
                        {hg.headers.map((header) => {
                          const canSort = header.column.getCanSort();
                          const sorted = header.column.getIsSorted();
                          // sticky：纵向滚动时表头钉在滚动容器顶部
                          return (
                            <th key={header.id} className="px-3 py-2 text-left font-medium">
                              {header.isPlaceholder ? null : canSort ? (
                                <button
                                  type="button"
                                  className="inline-flex items-center gap-1 hover:text-zinc-200"
                                  onClick={header.column.getToggleSortingHandler()}
                                >
                                  {flexRender(header.column.columnDef.header, header.getContext())}
                                  {sorted === 'asc' ? (
                                    <ArrowUp size={12} aria-hidden />
                                  ) : sorted === 'desc' ? (
                                    <ArrowDown size={12} aria-hidden />
                                  ) : (
                                    <ArrowUpDown size={12} className="opacity-50" aria-hidden />
                                  )}
                                </button>
                              ) : (
                                flexRender(header.column.columnDef.header, header.getContext())
                              )}
                            </th>
                          );
                        })}
                      </tr>
                    ))}
                  </thead>
                  <tbody className="divide-y divide-zinc-800/40">
                    {table.getRowModel().rows.map((row) => (
                      <tr
                        key={row.id}
                        className={cn('transition-colors', row.original.archived && 'opacity-60')}
                      >
                        {row.getVisibleCells().map((cell) => (
                          <td key={cell.id} className="min-w-0 px-3 py-2">
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
            </TableCard>
          )}
        </div>
      </div>
    </main>
  );
}
