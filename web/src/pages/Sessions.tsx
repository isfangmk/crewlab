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
  Paperclip,
  Pin,
  PinOff,
  RefreshCw,
  Share2,
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
  listSessionModels,
  listSessions,
  updateSessionFlags,
  type ChatSession,
  type SessionSortBy,
  type SessionSortDir,
} from '@/api/chat';
import { listAgents, localizedAgent, type AgentSummary } from '@/api/agents';
import { AgentBadge } from '@/components/AgentBadge';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import {
  Button,
  EmptyStateCard,
  ErrorBanner,
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
  TableHead,
  TablePager,
  Toast,
} from '@/components/ui';
import type { StatusTone } from '@/components/ui/StatusPill';
import { Checkbox } from '@/components/ui/Checkbox';
import { useRequestSeq } from '@/lib/requestSeq';
import { Select } from '@/components/ui/Select';
import { Hint } from '@/components/ui/Tooltip';
import { useI18n } from '@/i18n/locale';
import { cn } from '@/lib/cn';
import { formatNumber, fullDateTime } from '@/lib/format';
import { invalidateChatSessions } from '@/store/chatSessions';
import { errorText } from '@/lib/errors';

const PAGE_SIZE = 20;

// 超过这个条数的会话，列表里的摘要默认折叠成一行，需要手动展开。
// 阈值定在 40：低于它时摘要一行就是全部，折叠按钮只会增加噪声。
const LONG_SESSION_MESSAGES = 40;

type TimePreset = 'all' | 'today' | '7d' | '30d' | 'custom';

/** 分页列表只开放时间列排序；其他列保持服务端默认顺序。 */
const SORTABLE_IDS = new Set<SessionSortBy>(['updated_at', 'last_message_at']);

/** 最近一回合状态 → 胶囊文案；未知状态不猜，直接不显示。 */
const TURN_STATUS: Record<string, { tone: StatusTone; zh: string; en: string }> = {
  ok: { tone: 'ok', zh: '正常', en: 'OK' },
  running: { tone: 'info', zh: '进行中', en: 'Running' },
  error: { tone: 'danger', zh: '失败', en: 'Failed' },
  stopped: { tone: 'warn', zh: '已停止', en: 'Stopped' },
};

/** 列表里的 token 量级只用于扫读，精确值挂在 title 上。 */
function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

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

/** 会话 ID 点击即复制；不显示附加标识，复制前后文字样式保持不变。 */
function SessionIdCell({
  id,
  onCopyResult,
}: {
  id: string;
  onCopyResult: (ok: boolean) => void;
}) {
  const { tr } = useI18n();

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(id);
      onCopyResult(true);
    } catch {
      onCopyResult(false);
    }
  };

  return (
    <div className="min-w-0">
      <button
        type="button"
        onClick={() => void copy()}
        aria-label={tr(`复制会话 ID ${id}`, `Copy session ID ${id}`)}
        title={tr('点击复制会话 ID', 'Click to copy session ID')}
        className="w-full min-w-0 rounded text-left font-mono text-[10px] leading-4 text-zinc-500 transition-colors hover:text-zinc-300"
      >
        <span className="break-all">{id}</span>
      </button>
    </div>
  );
}

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
  const [modelFilter, setModelFilter] = useState('');
  const [models, setModels] = useState<string[]>([]);
  // 长会话摘要折叠状态按会话 id 记录，翻页/刷新后每条会话各自独立。
  const [expandedPreviews, setExpandedPreviews] = useState<Record<string, boolean>>({});
  const [timePreset, setTimePreset] = useState<TimePreset>('all');
  const [customFrom, setCustomFrom] = useState('');
  const [customTo, setCustomTo] = useState('');
  const [page, setPage] = useState(0);
  const [sorting, setSorting] = useState<SortingState>([]);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // 筛选条件会连续变化：路数守卫保证只有最新一次请求的结果落进状态
  const reqSeq = useRequestSeq();
  const [busyId, setBusyId] = useState('');
  const [batchBusy, setBatchBusy] = useState(false);
  // 删除确认走统一弹窗：单条删除与批量删除共用，不再弹原生 confirm
  const [deleteTarget, setDeleteTarget] = useState<ChatSession | null>(null);
  const [batchConfirmOpen, setBatchConfirmOpen] = useState(false);
  const [copyToast, setCopyToast] = useState<{ tone: 'ok' | 'err'; text: string } | null>(null);

  useEffect(() => {
    if (!copyToast) return;
    const timer = window.setTimeout(() => setCopyToast(null), 5000);
    return () => window.clearTimeout(timer);
  }, [copyToast]);

  const handleCopyResult = useCallback(
    (ok: boolean) => {
      setCopyToast({
        tone: ok ? 'ok' : 'err',
        text: ok
          ? tr('会话 ID 已复制', 'Session ID copied')
          : tr('复制失败，请检查浏览器剪贴板权限', 'Copy failed. Check clipboard permission.'),
      });
    },
    [tr],
  );

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
          setErr(errorText(e));
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 模型筛选项来自「会话里实际出现过的模型」，只取一次；
  // 列表刷新时再用本页数据并集补齐，避免新模型要刷新整页才出现。
  useEffect(() => {
    let cancelled = false;
    void listSessionModels()
      .then((res) => {
        if (cancelled) return;
        if (!Array.isArray(res.items)) {
          throw new Error('模型列表缺少 items');
        }
        setModels(res.items);
      })
      .catch((e) => {
        if (!cancelled) {
          setErr(errorText(e));
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const load = useCallback(async (silent = false) => {
    const current = reqSeq.next();
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
        model: modelFilter === '' ? undefined : modelFilter,
        updated_from: updatedFrom,
        updated_to: updatedTo,
        ...sort,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      });
      if (reqSeq.isStale(current)) return;
      if (!Array.isArray(res.items)) {
        throw new Error('会话列表缺少 items');
      }
      if (typeof res.total !== 'number') {
        throw new Error('会话列表缺少 total');
      }
      setItems(res.items);
      setTotal(res.total);
      // 本页出现的模型并进筛选项：新会话用了新模型时不必重新加载整页。
      setModels((prev) => {
        const next = new Set(prev);
        for (const s of res.items) {
          if (s.model) next.add(s.model);
        }
        return next.size === prev.length ? prev : [...next].sort();
      });
      setErr(null);
    } catch (e) {
      if (reqSeq.isStale(current)) return;
      setErr(errorText(e));
    } finally {
      if (!reqSeq.isStale(current)) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [query, includeArchived, agentId, modelFilter, timePreset, customFrom, customTo, page, sorting, reqSeq]);

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
      setErr(errorText(e));
    } finally {
      setBusyId('');
    }
  };

  const runDelete = async (session: ChatSession) => {
    await deleteSession(session.id);
    invalidateChatSessions();
    // 删掉当前页最后一条时回退到有效页，避免停在空白页
    if (items.length === 1 && page > 0) setPage(page - 1);
    else await load(true);
  };

  const openDelete = (session: ChatSession) => {
    setDeleteTarget(session);
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
      setErr(errorText(e));
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
      onDelete: (session) => openDelete(session),
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
          const messageCount = session.message_count ?? 0;
          const attachmentCount = session.attachment_count ?? 0;
          const preview = (session.last_message_preview ?? '').trim();
          const expanded = expandedPreviews[session.id] === true;
          // 长会话的摘要默认只占一行；短会话一行本来就够看，不给折叠按钮。
          const collapsible = messageCount >= LONG_SESSION_MESSAGES && preview !== '';
          return (
            <div className="min-w-0">
              <div className="flex min-w-0 items-center gap-1.5">
                <Link
                  to={`/chat/${session.id}`}
                  title={title}
                  className="flex min-w-0 flex-1 items-center gap-1.5 text-[13px] font-medium text-zinc-100 hover:underline"
                >
                  {session.pinned ? <Pin size={12} className="shrink-0 text-amber-400" aria-hidden /> : null}
                  <span className="truncate">{title}</span>
                </Link>
                {session.shared ? (
                  <span
                    className="shrink-0 text-accent"
                    title={tr('已开启只读分享', 'Read-only share is active')}
                    aria-label={tr('已分享', 'Shared')}
                  >
                    <Share2 size={12} aria-hidden />
                  </span>
                ) : null}
                {attachmentCount > 0 ? (
                  <span
                    className="inline-flex shrink-0 items-center gap-0.5 text-[10.5px] tabular-nums text-zinc-500"
                    title={tr(`${attachmentCount} 个附件`, `${attachmentCount} attachments`)}
                  >
                    <Paperclip size={11} aria-hidden />
                    {attachmentCount}
                  </span>
                ) : null}
              </div>
              {preview !== '' ? (
                <div className="mt-0.5 flex min-w-0 items-start gap-1 text-[10.5px] leading-4 text-zinc-500">
                  <span
                    className={cn(
                      'min-w-0 flex-1 text-zinc-500',
                      expanded ? 'whitespace-pre-line break-words' : 'line-clamp-1',
                    )}
                  >
                    {preview}
                  </span>
                  {collapsible ? (
                    <button
                      type="button"
                      aria-expanded={expanded}
                      aria-label={tr(
                        expanded ? `收起摘要：${title}` : `展开摘要：${title}`,
                        expanded ? `Collapse summary: ${title}` : `Expand summary: ${title}`,
                      )}
                      className="shrink-0 text-[10.5px] text-zinc-400 underline-offset-2 hover:text-zinc-200 hover:underline"
                      onClick={() => {
                        setExpandedPreviews((prev) => ({ ...prev, [session.id]: !expanded }));
                      }}
                    >
                      {tr(expanded ? '收起' : '展开', expanded ? 'Collapse' : 'Expand')}
                    </button>
                  ) : null}
                </div>
              ) : null}
            </div>
          );
        },
      }),
      columnHelper.accessor('id', {
        id: 'session_id',
        header: tr('会话 ID', 'Session ID'),
        enableSorting: false,
        cell: ({ getValue }) => (
          <SessionIdCell id={getValue()} onCopyResult={handleCopyResult} />
        ),
      }),
      // 轮次与调用量独立成列，不进入标题单元格
      columnHelper.display({
        id: 'counts',
        header: tr('轮次 / 调用', 'Turns / calls'),
        enableSorting: false,
        cell: ({ row }) => {
          const turnCount = row.original.turn_count ?? 0;
          const toolCallCount = row.original.tool_call_count ?? 0;
          const skillCallCount = row.original.skill_call_count ?? 0;
          const calls = [
            toolCallCount > 0 ? tr(`${toolCallCount} 次工具`, `${toolCallCount} tools`) : '',
            skillCallCount > 0 ? tr(`${skillCallCount} 次技能`, `${skillCallCount} skills`) : '',
          ]
            .filter((item) => item !== '')
            .join(' · ');
          if (turnCount === 0 && calls === '') {
            return <span className="text-[12px] text-zinc-600">—</span>;
          }
          return (
            <div className="space-y-0.5 text-[10.5px] leading-4 tabular-nums">
              {turnCount > 0 ? (
                <span className="block text-zinc-500">
                  {tr(`${turnCount} 轮`, `${turnCount} turns`)}
                </span>
              ) : null}
              {calls !== '' ? <span className="block text-zinc-600">{calls}</span> : null}
            </div>
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
        cell: ({ row }) => {
          const model = row.original.model;
          const tokens = row.original.total_tokens;
          return (
            <div className="min-w-0">
              <span className="block truncate font-mono text-[11px] text-zinc-500" title={model || undefined}>
                {model || '—'}
              </span>
              {tokens !== null && tokens !== undefined ? (
                <span
                  className="mt-0.5 block truncate text-[10.5px] tabular-nums text-zinc-600"
                  title={tr(`累计 ${formatNumber(tokens)} tokens`, `${formatNumber(tokens)} tokens total`)}
                >
                  {formatTokens(tokens)} tokens
                </span>
              ) : null}
            </div>
          );
        },
      }),
      columnHelper.display({
        id: 'runtime',
        header: tr('运行', 'Runtime'),
        enableSorting: false,
        cell: ({ row }) => {
          const session = row.original;
          const status = session.last_turn_status ? TURN_STATUS[session.last_turn_status] : undefined;
          const pending = session.pending_approvals ?? 0;
          if (status === undefined && pending === 0) {
            return <span className="text-[12px] text-zinc-600">—</span>;
          }
          const pill =
            status === undefined ? null : (
              <StatusPill
                tone={status.tone}
                label={tr(status.zh, status.en)}
                pulse={status.tone === 'info'}
              />
            );
          return (
            <div className="flex flex-wrap items-center gap-1">
              {/* 失败回合要能一键跳到该会话的 Trace，否则只能靠 session_id 手抄 */}
              {status?.tone === 'danger' ? (
                <Link
                  to={`/observability?session_id=${encodeURIComponent(session.id)}`}
                  title={tr('查看该会话的回合 Trace', 'View traces for this session')}
                  className="rounded-full transition-opacity hover:opacity-80"
                >
                  {pill}
                </Link>
              ) : (
                pill
              )}
              {pending > 0 ? (
                <StatusPill tone="warn" label={tr(`待审 ${pending}`, `${pending} pending`)} />
              ) : null}
            </div>
          );
        },
      }),
      columnHelper.accessor((row) => row.last_message_at ?? row.updated_at ?? row.created_at, {
        id: 'last_message_at',
        header: tr('最近活动', 'Last activity'),
        cell: ({ row }) => {
          const raw =
            row.original.last_message_at ?? row.original.updated_at ?? row.original.created_at;
          if (!raw) {
            return <span className="text-[12px] text-zinc-600">—</span>;
          }
          // 一律显示完整日期时间：相对时间（「3 小时前」）无法用于核对与排序
          const label = row.original.last_message_at
            ? tr('最后一条消息', 'Last message')
            : tr('会话更新时间', 'Session updated');
          return (
            <span
              className="whitespace-nowrap text-[12px] tabular-nums text-zinc-500"
              title={label}
            >
              {fullDateTime(raw)}
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
              : { tone: 'ok', zh: '普通', en: 'Normal' };
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
    [tr, rowActions, expandedPreviews, handleCopyResult],
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
    setBatchBusy(true);
    try {
      const res = await batchDeleteSessions(selectedIds);
      if (typeof res.deleted !== 'number') {
        throw new Error('批量删除响应缺少 deleted');
      }
      setRowSelection({});
      invalidateChatSessions();
      // 整页被删空时回退到有效页
      if (items.length === selectedIds.length && page > 0) setPage(page - 1);
      else await load(true);
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

  const modelSelectOptions = useMemo(
    () => [
      { value: '', label: tr('全部模型', 'All models') },
      ...models.map((m) => ({ value: m, label: m })),
    ],
    [models, tr],
  );

  useAssistantPage('会话管理', 'Sessions', {
    count: total,
    page,
    query,
    include_archived: includeArchived,
    agent_id: agentId,
    model: modelFilter,
    time_preset: timePreset,
    sort: sorting[0] ? `${sorting[0].id}:${sorting[0].desc ? 'desc' : 'asc'}` : 'default',
    selected: selectedIds.length,
    loading,
  });

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tr('会话', 'Sessions')}
        subtitle={tr(
          '搜索、按助理/模型筛选、置顶归档、批量删除与分页',
          'Search, filter by assistant/model, pin, archive, batch delete, and pagination',
        )}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {selectedIds.length > 0 ? (
              <Button
                variant="danger"
                size="sm"
                type="button"
                disabled={batchBusy}
                onClick={() => setBatchConfirmOpen(true)}
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
          label={tr('模型', 'Model')}
          active={modelFilter !== ''}
          onClear={() => {
            resetFiltersToFirstPage();
            setModelFilter('');
          }}
          width="w-[11.5rem]"
        >
          <Select
            aria-label={tr('模型', 'Model')}
            value={modelFilter}
            onValueChange={(v) => {
              resetFiltersToFirstPage();
              setModelFilter(v);
            }}
            options={modelSelectOptions}
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
          <ErrorBanner>
            {err}
          </ErrorBanner>
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
                query.trim() !== '' || agentId !== '' || modelFilter !== '' || timePreset !== 'all'
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
                <table className="og-action-table w-full min-w-[80rem] table-fixed text-sm">
                  <colgroup>
                    <col className="w-[2.5rem]" />
                    <col className="w-[16rem]" />
                    <col className="w-[11rem]" />
                    <col className="w-[8rem]" />
                    <col className="w-[7rem]" />
                    <col className="w-[8rem]" />
                    <col className="w-[7rem]" />
                    <col className="w-[7.5rem]" />
                    <col className="w-[5rem]" />
                    <col className="w-[8rem]" />
                  </colgroup>
                  <TableHead>
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
                  </TableHead>
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

      <ConfirmDialog
        open={deleteTarget !== null}
        title={tr('删除会话', 'Delete chat')}
        description={
          deleteTarget === null ? null : (
            <>
              <p>
                {tr('确定删除会话 ', 'Delete ')}
                <span className="font-mono text-zinc-100">{deleteTarget.title || deleteTarget.id}</span>
                {tr(' ？', '?')}
              </p>
              <p className="mt-2 text-zinc-500">
                {tr(
                  '消息、附件、分享链接与观测记录会一并清除，该操作不可撤销。',
                  'Messages, attachments, share links and traces are removed too. This cannot be undone.',
                )}
              </p>
            </>
          )
        }
        onConfirm={async () => {
          if (deleteTarget === null) return;
          await runDelete(deleteTarget);
          setDeleteTarget(null);
        }}
        onClose={() => setDeleteTarget(null)}
      />

      <ConfirmDialog
        open={batchConfirmOpen}
        title={tr('批量删除会话', 'Delete selected chats')}
        description={
          <>
            <p>
              {tr(
                `确定删除选中的 ${selectedIds.length} 个会话？`,
                `Delete ${selectedIds.length} selected sessions?`,
              )}
            </p>
            <p className="mt-2 text-zinc-500">
              {tr(
                '消息、附件、分享链接与观测记录会一并清除，该操作不可撤销。',
                'Messages, attachments, share links and traces are removed too. This cannot be undone.',
              )}
            </p>
          </>
        }
        onConfirm={async () => {
          await runBatchDelete();
          setBatchConfirmOpen(false);
        }}
        onClose={() => setBatchConfirmOpen(false)}
      />

      {copyToast ? <Toast tone={copyToast.tone} text={copyToast.text} /> : null}
    </main>
  );
}
