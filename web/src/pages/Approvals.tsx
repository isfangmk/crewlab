import { useAssistantPage } from '@/lib/assistantPage';
import {
  Button,
  Checkbox,
  ChoiceChip,
  EmptyStateCard,
  FilterBar,
  FilterPill,
  Input,
  MetricCard,
  PageHeader,
  RowAction,
  RowActionIcon,
  SearchInput,
  StatusPill,
  TableCard,
  TablePager,
} from '@/components/ui';
import type { StatusTone } from '@/components/ui/StatusPill';
import { Select } from '@/components/ui/Select';
import { RejectApprovalDialog } from '@/components/RejectApprovalDialog';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  ArrowDown,
  ArrowUp,
  BadgeCheck,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  CircleSlash,
  Clock,
  Copy,
  PlayCircle,
  RefreshCw,
  ShieldCheck,
  TimerOff,
  X,
  type LucideIcon,
} from 'lucide-react';
import {
  listApprovals,
  getApproval,
  approveApproval,
  rejectApproval,
  batchDecideApprovals,
  type Approval,
  type ApprovalCounts,
  type ApprovalStatus,
} from '@/api/approvals';
import { ApiError } from '@/api/client';
import { useI18n } from '@/i18n/locale';
import { cn } from '@/lib/cn';
import { fullDateTime } from '@/lib/format';
import { parseLocalDateTime, rangeForPreset, type TimePreset } from '@/lib/timeRange';

// 审批列表：Agent 提出的危险工具调用，管理员在此批准或拒绝。
// 状态、关键字、提交时间与服务端分页都由后端处理；顶部指标卡复用同一次响应里的状态分布。

const PAGE_SIZE_OPTIONS = [20, 50, 100];
const DEFAULT_PAGE_SIZE = 20;

const STATUSES: ApprovalStatus[] = ['pending', 'approved', 'executed', 'rejected', 'failed', 'expired'];

const STATUS_META: Record<ApprovalStatus, { tone: StatusTone; icon: LucideIcon }> = {
  pending: { tone: 'warn', icon: Clock },
  approved: { tone: 'ok', icon: BadgeCheck },
  executed: { tone: 'ok', icon: PlayCircle },
  rejected: { tone: 'neutral', icon: CircleSlash },
  failed: { tone: 'danger', icon: CircleAlert },
  expired: { tone: 'neutral', icon: TimerOff },
};

export default function ApprovalsPage() {
  const { tr } = useI18n();
  const [params, setParams] = useSearchParams();
  const selectedId = params.get('approval');

  const [items, setItems] = useState<Approval[]>([]);
  const [total, setTotal] = useState(0);
  // null 表示深链单条视图：不发全量统计请求，也不渲染指标卡，避免展示误导性的 0。
  const [counts, setCounts] = useState<ApprovalCounts | null>(null);
  const [status, setStatus] = useState<ApprovalStatus | ''>('pending');
  const [query, setQuery] = useState('');
  const [timePreset, setTimePreset] = useState<TimePreset>('all');
  const [customFrom, setCustomFrom] = useState('');
  const [customTo, setCustomTo] = useState('');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  // 列表只按提交时间排序：desc = 最新在前（默认），asc = 最早在前。
  const [sortDir, setSortDir] = useState<'desc' | 'asc'>('desc');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // null=关闭；'batch'=批量拒绝；否则为单条审批。
  const [rejectTarget, setRejectTarget] = useState<'batch' | Approval | null>(null);

  // 自定义时间只填了一边时不发请求：先提示补齐，避免拿「全部时间」冒充自定义结果。
  const customIncomplete =
    timePreset === 'custom' && (customFrom === '' || customTo === '');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // 深链只看单条审批：不受状态/时间/分页影响，否则会看不到目标记录。
      if (selectedId) {
        setItems([await getApproval(selectedId)]);
        setTotal(1);
        setCounts(null);
        setErr(null);
        return;
      }

      let createdFrom: string | undefined;
      let createdTo: string | undefined;
      if (customIncomplete) {
        setItems([]);
        setTotal(0);
        setCounts(null);
        setErr(null);
        return;
      }
      if (timePreset === 'custom') {
        createdFrom = parseLocalDateTime(customFrom);
        createdTo = parseLocalDateTime(customTo);
        if (createdFrom > createdTo) {
          throw new Error(tr('开始时间不能晚于结束时间', 'Start time cannot be later than end time'));
        }
      } else {
        const range = rangeForPreset(timePreset);
        createdFrom = range.from;
        createdTo = range.to;
      }

      const res = await listApprovals({
        status: status === '' ? undefined : status,
        q: query.trim() === '' ? undefined : query.trim(),
        created_from: createdFrom,
        created_to: createdTo,
        sort_dir: sortDir,
        limit: pageSize,
        offset: page * pageSize,
      });
      if (!Array.isArray(res.items)) {
        throw new Error(tr('审批列表缺少 items', 'Approval list is missing items'));
      }
      if (typeof res.total !== 'number' || res.counts === undefined) {
        throw new Error(tr('审批列表缺少分页信息', 'Approval list is missing pagination info'));
      }
      setItems(res.items);
      setTotal(res.total);
      setCounts(res.counts);
      setErr(null);

      // 当前页被删空（例如批准掉整页）时回退到最后一个有效页，避免停在空页。
      const maxPage = Math.max(0, Math.ceil(res.total / pageSize) - 1);
      if (page > maxPage) {
        setPage(maxPage);
      }
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [selectedId, status, query, timePreset, customFrom, customTo, customIncomplete, page, pageSize, sortDir, tr]);

  useEffect(() => {
    // 与搜索共享同一 250ms 防抖：输入过程中不打满后端。
    const timer = window.setTimeout(() => {
      void load();
    }, 250);
    return () => window.clearTimeout(timer);
  }, [load]);

  // 数据页变化后清空勾选，避免跨页混淆批量对象。
  useEffect(() => {
    setSelected(new Set());
  }, [items]);

  const resetFiltersToFirstPage = () => {
    setPage(0);
  };

  const clearFilters = () => {
    setParams({});
    setQuery('');
    setStatus('');
    setTimePreset('all');
    setCustomFrom('');
    setCustomTo('');
    setPage(0);
  };

  const pendingIds = useMemo(
    () => items.filter((a) => a.status === 'pending').map((a) => a.id),
    [items],
  );
  const allPendingSelected = pendingIds.length > 0 && pendingIds.every((id) => selected.has(id));

  // 批准直接执行，无二次确认。
  const onApprove = async (a: Approval) => {
    setBusy(a.id);
    try {
      await approveApproval(a.id);
      await load();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setBusy('');
    }
  };

  const onBatchApprove = async () => {
    const ids = [...selected];
    if (ids.length === 0) {
      setErr(tr('请先勾选审批单', 'Select approvals first'));
      return;
    }
    setBusy('batch');
    try {
      await batchDecideApprovals(ids, true, '');
      await load();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setBusy('');
    }
  };

  const rejectWithReason = async (reason: string) => {
    if (rejectTarget === null) {
      throw new Error(tr('没有待拒绝的审批', 'No approval selected to reject'));
    }
    if (rejectTarget === 'batch') {
      const ids = [...selected];
      if (ids.length === 0) {
        throw new Error(tr('请先勾选审批单', 'Select approvals first'));
      }
      setBusy('batch');
      try {
        await batchDecideApprovals(ids, false, reason);
        await load();
      } catch (e) {
        const msg = e instanceof ApiError ? e.message : (e as Error).message;
        setErr(msg);
        throw e instanceof Error ? e : new Error(msg);
      } finally {
        setBusy('');
      }
      return;
    }
    setBusy(rejectTarget.id);
    try {
      await rejectApproval(rejectTarget.id, reason);
      await load();
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : (e as Error).message;
      setErr(msg);
      throw e instanceof Error ? e : new Error(msg);
    } finally {
      setBusy('');
    }
  };

  const toggleSelected = (id: string) => {
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  useAssistantPage('审批列表', 'Approvals', {
    status,
    query,
    time_preset: timePreset,
    page,
    page_size: pageSize,
    sort_dir: sortDir,
    count: items.length,
    total,
    selected_approval_id: selectedId,
    sample_approval_ids: items.slice(0, 4).map((a) => a.id).join(', '),
    loading,
  });

  const rejectSubject =
    rejectTarget === 'batch'
      ? tr(`已选 ${selected.size} 项`, `${selected.size} selected`)
      : rejectTarget?.title;

  const filtered = query.trim() !== '' || status !== '' || timePreset !== 'all';

  // 状态是固定枚举且带计数，单独占筛选卡第二行，避免把搜索行挤到换行。
  const footer = (
    <>
      <span className="pr-1 text-[11px] text-zinc-500">{tr('状态', 'Status')}</span>
      <ChoiceChip
        active={status === ''}
        label={tr('全部', 'All')}
        count={counts ? STATUSES.reduce((sum, s) => sum + counts[s], 0) : undefined}
        onClick={() => {
          resetFiltersToFirstPage();
          setParams({});
          setStatus('');
        }}
      />
      {STATUSES.map((s) => (
        <ChoiceChip
          key={s}
          active={status === s}
          label={statusLabel(s, tr)}
          count={counts ? counts[s] : undefined}
          onClick={() => {
            resetFiltersToFirstPage();
            setParams({});
            setStatus(s);
          }}
        />
      ))}
      {timePreset === 'custom' ? (
        <span className="ml-2 inline-flex items-center gap-1.5">
          <span className="text-[11px] text-zinc-500">{tr('提交时间', 'Created')}</span>
          <Input
            type="datetime-local"
            aria-label={tr('开始时间', 'Start')}
            value={customFrom}
            onChange={(e) => {
              resetFiltersToFirstPage();
              setCustomFrom(e.target.value);
            }}
            className="h-7 w-[11.5rem] text-[12px]"
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
            className="h-7 w-[11.5rem] text-[12px]"
          />
        </span>
      ) : null}
      {selected.size > 0 ? (
        <>
          <span className="ml-2 text-[11px] text-zinc-400">
            {tr(`已选 ${selected.size} 项`, `${selected.size} selected`)}
          </span>
          <RowAction
            icon={Check}
            tone="positive"
            disabled={busy === 'batch'}
            onClick={() => void onBatchApprove()}
          >
            {tr('批量批准', 'Approve')}
          </RowAction>
          <RowAction
            icon={X}
            tone="danger"
            disabled={busy === 'batch'}
            onClick={() => setRejectTarget('batch')}
          >
            {tr('批量拒绝', 'Reject')}
          </RowAction>
          <Button
            variant="ghost"
            size="sm"
            type="button"
            onClick={() => setSelected(new Set())}
            className="ml-auto inline-flex h-7 items-center gap-1 px-1.5 text-[11px] text-zinc-500"
          >
            {tr('清空选择', 'Clear selection')}
          </Button>
        </>
      ) : null}
    </>
  );

  return (
    <>
      <main className="anim-fade flex flex-1 flex-col overflow-hidden">
        <PageHeader
          title={tr('待确认', 'Approvals')}
          subtitle={tr(
            'Agent、工作流和 MCP 提交的危险操作，需人工批准后才执行',
            'Dangerous actions from agents, workflows and MCP need approval before execution',
          )}
          leading={
            selectedId ? (
              <Link
                to="/approvals"
                className="inline-flex items-center gap-1 text-[11px] text-zinc-500 transition-colors hover:text-zinc-300"
              >
                <ChevronLeft size={12} aria-hidden />
                {tr('返回列表', 'Back to list')}
              </Link>
            ) : undefined
          }
          actions={
            <RowAction
              icon={RefreshCw}
              iconClassName={loading ? 'animate-spin' : undefined}
              onClick={() => void load()}
              disabled={loading}
            >
              {tr('刷新', 'Refresh')}
            </RowAction>
          }
        />

        <FilterBar className="mx-6 mt-3" footer={footer}>
          <SearchInput
            value={query}
            onChange={(v) => {
              resetFiltersToFirstPage();
              setQuery(v);
            }}
            placeholder={tr('搜索标题 / 摘要 / 工具 / 会话 ID', 'Search title / summary / tool / session ID')}
            ariaLabel={tr('搜索审批', 'Search approvals')}
            className="min-w-[14rem] flex-1"
          />
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
          <div className="ml-auto flex items-center gap-2">
            {pendingIds.length > 0 ? (
              <Button
                variant="ghost"
                size="sm"
                type="button"
                onClick={() => {
                  setSelected((cur) => {
                    if (allPendingSelected) {
                      const next = new Set(cur);
                      for (const id of pendingIds) next.delete(id);
                      return next;
                    }
                    const next = new Set(cur);
                    for (const id of pendingIds) next.add(id);
                    return next;
                  });
                }}
                className="h-7 px-2 text-[11px] text-zinc-400"
              >
                {allPendingSelected
                  ? tr('取消全选', 'Deselect all')
                  : tr(`全选本页待确认 (${pendingIds.length})`, `Select pending on page (${pendingIds.length})`)}
              </Button>
            ) : null}
            <span className="text-[11px] tabular-nums text-zinc-500">
              {tr(`共 ${total} 条`, `${total} items`)}
            </span>
          </div>
        </FilterBar>

        {/* 概览：统计与列表共用搜索/时间条件，点卡片即按状态过滤 */}
        {counts ? (
          <div className="mx-6 mt-3 grid shrink-0 grid-cols-3 gap-2 lg:grid-cols-6">
            {STATUSES.map((s) => {
              const meta = STATUS_META[s];
              return (
                <MetricCard
                  key={s}
                  icon={meta.icon}
                  tone={meta.tone === 'neutral' ? 'default' : meta.tone}
                  label={statusLabel(s, tr)}
                  value={String(counts[s])}
                  hint={
                    s === 'pending'
                      ? tr('等待人工决策', 'Awaiting decision')
                      : tr('当前筛选范围', 'Current window')
                  }
                  active={status === s}
                  onClick={() => {
                    resetFiltersToFirstPage();
                    setParams({});
                    setStatus(s);
                  }}
                />
              );
            })}
          </div>
        ) : null}

        <div className="flex min-h-0 flex-1 flex-col px-6 pb-4 pt-4">
          {err && (
            <div className="mb-3 rounded-xl border border-red-500/40 bg-red-500/5 px-4 py-2.5 text-[12px] text-red-300">
              {err}
            </div>
          )}
          {loading ? (
            <div className="flex h-40 items-center justify-center text-sm text-zinc-500">
              {tr('加载中…', 'Loading…')}
            </div>
          ) : customIncomplete ? (
            <EmptyStateCard
              icon={Clock}
              title={tr('填写提交时间范围后加载', 'Fill in the created time range to load')}
              action={
                <Button
                  variant="ghost"
                  onClick={() => {
                    resetFiltersToFirstPage();
                    setTimePreset('all');
                    setCustomFrom('');
                    setCustomTo('');
                  }}
                >
                  {tr('改为全部时间', 'Use all time')}
                </Button>
              }
            />
          ) : items.length === 0 ? (
            <EmptyStateCard
              icon={ShieldCheck}
              title={
                filtered
                  ? tr('没有匹配的审批', 'No matching approvals')
                  : tr('没有待确认的操作', 'No actions awaiting approval')
              }
              action={
                filtered ? (
                  <Button variant="ghost" onClick={clearFilters}>
                    {tr('清除筛选', 'Clear filters')}
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <TableCard
              className="min-h-0 flex-1"
              footer={
                counts ? (
                  <TablePager
                    page={page}
                    pageSize={pageSize}
                    total={total}
                    shown={items.length}
                    loading={loading}
                    onPageChange={setPage}
                    pageSizeOptions={PAGE_SIZE_OPTIONS}
                    onPageSizeChange={(size) => {
                      setPage(0);
                      setPageSize(size);
                    }}
                  />
                ) : undefined
              }
            >
              <table className="og-action-table w-full text-sm">
                <thead className="sticky top-0 z-10 border-b border-zinc-800/60 bg-zinc-900/95 text-[11px] text-zinc-500 backdrop-blur">
                  <tr className="whitespace-nowrap">
                    <th scope="col" className="w-[4.25rem] px-2 py-2 text-left font-medium">
                      <span className="sr-only">{tr('展开', 'Expand')}</span>
                    </th>
                    <th scope="col" className="w-full px-3 py-2 text-left font-medium">
                      {tr('审批内容', 'Approval')}
                    </th>
                    <th scope="col" className="whitespace-nowrap px-3 py-2 text-left font-medium">
                      {tr('状态', 'Status')}
                    </th>
                    <th scope="col" className="whitespace-nowrap px-3 py-2 text-left font-medium">
                      {tr('来源', 'Source')}
                    </th>
                    <th
                      scope="col"
                      className="whitespace-nowrap px-3 py-2 text-left font-medium"
                      aria-sort={sortDir === 'asc' ? 'ascending' : 'descending'}
                    >
                      <button
                        type="button"
                        className="inline-flex items-center gap-1 hover:text-zinc-200"
                        onClick={() => {
                          setPage(0);
                          setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
                        }}
                      >
                        {tr('提交时间', 'Created')}
                        {sortDir === 'desc' ? (
                          <ArrowDown size={12} aria-hidden />
                        ) : (
                          <ArrowUp size={12} aria-hidden />
                        )}
                      </button>
                    </th>
                    <th scope="col" className="whitespace-nowrap px-3 py-2 text-right font-medium">
                      {tr('操作', 'Actions')}
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/40">
                  {items.map((a) => (
                    <ApprovalRow
                      key={a.id}
                      approval={a}
                      expanded={expanded[a.id] === true}
                      checked={selected.has(a.id)}
                      busy={busy === a.id || busy === 'batch'}
                      onToggleExpand={() => setExpanded((e) => ({ ...e, [a.id]: !e[a.id] }))}
                      onToggleChecked={() => toggleSelected(a.id)}
                      onApprove={() => void onApprove(a)}
                      onReject={() => setRejectTarget(a)}
                    />
                  ))}
                </tbody>
              </table>
            </TableCard>
          )}
        </div>
      </main>
      <RejectApprovalDialog
        open={rejectTarget !== null}
        subject={rejectSubject}
        onClose={() => setRejectTarget(null)}
        onConfirm={rejectWithReason}
      />
    </>
  );
}

function ApprovalRow({
  approval: a,
  expanded,
  checked,
  busy,
  onToggleExpand,
  onToggleChecked,
  onApprove,
  onReject,
}: {
  approval: Approval;
  expanded: boolean;
  checked: boolean;
  busy: boolean;
  onToggleExpand(): void;
  onToggleChecked(): void;
  onApprove(): void;
  onReject(): void;
}) {
  const { tr } = useI18n();
  const meta = STATUS_META[a.status];
  const Icon = meta.icon;
  const pending = a.status === 'pending';

  return (
    <>
      <tr
        className={cn(
          'transition-colors',
          pending ? 'bg-warn/[0.03] hover:bg-warn/[0.06]' : 'hover:bg-zinc-900/40',
          expanded && 'bg-zinc-900/40',
        )}
      >
        <td className="px-2 py-2">
          <div className="flex items-center gap-1">
            {pending ? (
              <Checkbox
                checked={checked}
                onCheckedChange={onToggleChecked}
                aria-label={tr('选择审批', 'Select approval')}
              />
            ) : (
              <span className="inline-block w-4" aria-hidden />
            )}
            <RowActionIcon
              icon={expanded ? ChevronDown : ChevronRight}
              label={expanded ? tr('收起操作内容', 'Collapse payload') : tr('展开操作内容', 'Expand payload')}
              onClick={onToggleExpand}
              aria-expanded={expanded}
              className="shrink-0"
            />
          </div>
        </td>
        <td className="w-full max-w-0 px-3 py-2">
          <div className="flex min-w-0 items-center gap-1.5">
            <Icon
              size={13}
              className={cn('shrink-0', pending ? 'text-warn' : 'text-zinc-500')}
              aria-hidden
            />
            <button
              type="button"
              onClick={onToggleExpand}
              className="min-w-0 truncate text-left text-[12.5px] font-medium text-zinc-100 transition-colors hover:text-indigo-300"
              title={a.title}
            >
              {a.title}
            </button>
            <span className="shrink-0 rounded border border-zinc-800 bg-zinc-950/40 px-1 py-px font-mono text-[10px] text-zinc-500">
              {a.kind}
            </span>
          </div>
          {a.summary && a.summary !== a.title && (
            <div className="mt-0.5 line-clamp-1 whitespace-pre-wrap pl-[19px] text-[11px] text-zinc-500" title={a.summary}>
              {a.summary}
            </div>
          )}
        </td>
        <td className="whitespace-nowrap px-3 py-2">
          <StatusPill tone={meta.tone} label={statusLabel(a.status, tr)} pulse={pending} />
        </td>
        <td className="whitespace-nowrap px-3 py-2 text-[11px]">
          {a.session_id ? (
            <Link
              to={`/chat/${a.session_id}`}
              className="font-mono text-zinc-400 transition-colors hover:text-indigo-300 hover:underline"
              title={`${a.source} · ${a.session_id}`}
            >
              {a.session_id.slice(0, 8)}
            </Link>
          ) : (
            <span className="text-zinc-500">{a.source}</span>
          )}
        </td>
        <td className="whitespace-nowrap px-3 py-2 text-[11px] leading-4 text-zinc-400">
          <div className="tabular-nums">{fullDateTime(a.created_at)}</div>
          {a.decided_at ? (
            <div className="text-[10px] tabular-nums text-zinc-600">
              {tr('决策', 'decided')} {fullDateTime(a.decided_at)}
            </div>
          ) : null}
        </td>
        <td className="whitespace-nowrap px-3 py-2 text-right">
          {pending ? (
            <div className="flex items-center justify-end gap-1.5">
              <RowAction icon={Check} tone="positive" onClick={onApprove} disabled={busy}>
                {tr('批准', 'Approve')}
              </RowAction>
              <RowAction icon={X} tone="danger" onClick={onReject} disabled={busy}>
                {tr('拒绝', 'Reject')}
              </RowAction>
            </div>
          ) : (
            <span className="text-[11px] text-zinc-600">—</span>
          )}
        </td>
      </tr>
      {expanded ? (
        <tr className="bg-zinc-950/30">
          <td colSpan={6} className="px-3 pb-3 pt-0">
            <div className="ml-[3.625rem] space-y-2.5">
              <PayloadBlock title={tr('操作内容', 'Action payload')} body={prettify(a.payload)} />
              {a.result ? <PayloadBlock title={tr('执行结果', 'Result')} body={prettify(a.result)} /> : null}
              {a.decision_reason || a.reason ? (
                <div className="rounded-lg border border-zinc-800/60 bg-zinc-950/40 px-2.5 py-2 text-[11px] leading-5 text-zinc-400">
                  <span className="mr-1 text-zinc-500">{tr('决策说明', 'Decision reason')}</span>
                  {a.decision_reason || a.reason}
                </div>
              ) : null}
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}

function PayloadBlock({ title, body }: { title: string; body: string }) {
  const { tr } = useI18n();
  return (
    <div className="overflow-hidden rounded-lg border border-zinc-800/60 bg-zinc-950/60">
      <div className="flex items-center justify-between gap-2 border-b border-zinc-800/60 px-2.5 py-1.5">
        <span className="text-[10px] uppercase tracking-wider text-zinc-500">{title}</span>
        <Button
          variant="subtle"
          size="sm"
          type="button"
          aria-label={tr('复制内容', 'Copy content')}
          onClick={() => void navigator.clipboard.writeText(body)}
          className="h-5 w-5 p-0 text-zinc-500"
        >
          <Copy size={10} />
        </Button>
      </div>
      <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-all px-2.5 py-2 text-[11px] leading-5 text-text-muted">
        {body}
      </pre>
    </div>
  );
}

function statusLabel(s: ApprovalStatus, tr: (zh: string, en: string) => string): string {
  switch (s) {
    case 'pending':
      return tr('待确认', 'Pending');
    case 'approved':
      return tr('已批准', 'Approved');
    case 'executed':
      return tr('已执行', 'Executed');
    case 'rejected':
      return tr('已拒绝', 'Rejected');
    case 'failed':
      return tr('失败', 'Failed');
    case 'expired':
      return tr('已过期', 'Expired');
    default:
      return s;
  }
}

function prettify(s: string): string {
  try {
    return JSON.stringify(JSON.parse(s), null, 2);
  } catch {
    return s;
  }
}
