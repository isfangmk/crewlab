import { useAssistantPage } from '@/lib/assistantPage';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  Activity,
  ArrowUpRight,
  CircleAlert,
  Coins,
  LoaderCircle,
  PlugZap,
  RefreshCw,
  RotateCcw,
  Sparkles,
  SquareTerminal,
  Timer,
} from 'lucide-react';
import {
  getObservationOverview,
  getObservationTurn,
  listObservationTurns,
  type ObservationFilters,
  type ObservationOverview,
  type ObservationToolCall,
  type ObservationTurn,
  type ObservationTurnDetail,
} from '@/api/observability';
import { ApiError } from '@/api/client';
import {
  Button,
  DetailSection,
  DetailStat,
  DetailStatGrid,
  EmptyStateCard,
  FilterBar,
  FilterChip,
  FilterPill,
  Input,
  MetaRow,
  MetricCard,
  PageHeader,
  RowAction,
  rowActionClass,
  SearchInput,
  Select,
  StatusPill,
  TableCard,
  TablePager,
} from '@/components/ui';
import type { StatusTone } from '@/components/ui/StatusPill';
import { Modal } from '@/components/Modal';
import { useI18n } from '@/i18n/locale';
import { formatNumber, fullDateTime } from '@/lib/format';
import { cn } from '@/lib/cn';

const PAGE_SIZES = [20, 50, 100] as const;
type RangePreset = 'all' | 'today' | '7d' | '30d' | 'custom';

/** datetime-local 是无时区的本地时间，转成 ISO 再交给后端。 */
function parseLocalDateTime(value: string): string {
  if (value === '') {
    throw new Error('时间不能为空');
  }
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) {
    throw new Error(`非法时间: ${value}`);
  }
  return d.toISOString();
}

function rangeFromPreset(preset: RangePreset): { from?: string; to?: string } {
  const now = new Date();
  if (preset === 'all') return {};
  if (preset === 'today') {
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    return { from: start.toISOString() };
  }
  if (preset === '7d') return { from: new Date(now.getTime() - 7 * 86_400_000).toISOString() };
  if (preset === '30d') return { from: new Date(now.getTime() - 30 * 86_400_000).toISOString() };
  throw new Error('自定义时间范围必须使用起止时间输入框');
}

function formatDurationMs(ms: number | null): string {
  if (ms == null) return '—';
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function formatTokens(n: number | null): string {
  return n == null ? '—' : formatNumber(n);
}

function toEpochMs(value: string, field: string): number {
  const t = new Date(value).getTime();
  if (Number.isNaN(t)) {
    throw new Error(`非法${field}: ${value}`);
  }
  return t;
}

type StatusStyle = { tone: StatusTone; zh: string; en: string };

const STATUS_STYLES: Record<string, StatusStyle> = {
  ok: { tone: 'ok', zh: '成功', en: 'OK' },
  error: { tone: 'danger', zh: '失败', en: 'Error' },
  stopped: { tone: 'warn', zh: '已停止', en: 'Stopped' },
  running: { tone: 'info', zh: '进行中', en: 'Running' },
};

function statusStyle(status: string): StatusStyle {
  const style = STATUS_STYLES[status];
  if (style === undefined) {
    throw new Error(`未知状态: ${status}`);
  }
  return style;
}

type CallKindStyle = {
  zh: string;
  en: string;
  Icon: typeof SquareTerminal;
  bar: string;
  chip: string;
};

const KIND_STYLES: Record<string, CallKindStyle> = {
  tool: { zh: '工具', en: 'Tool', Icon: SquareTerminal, bar: 'bg-ok', chip: 'border-ok/25 bg-ok/10 text-ok' },
  skill: { zh: '技能', en: 'Skill', Icon: Sparkles, bar: 'bg-accent', chip: 'border-accent/25 bg-accent/10 text-accent' },
  mcp: { zh: 'MCP', en: 'MCP', Icon: PlugZap, bar: 'bg-info', chip: 'border-info/25 bg-info/10 text-info' },
};

function kindStyle(kind: string): CallKindStyle {
  const style = KIND_STYLES[kind];
  if (style === undefined) {
    throw new Error(`未知调用类型: ${kind}`);
  }
  return style;
}

/** 可观测页：回合 Trace 列表（延迟 / token / 错误），多条件筛选 + 服务端分页。 */
export default function ObservabilityPage() {
  const { tr } = useI18n();
  const [items, setItems] = useState<ObservationTurn[]>([]);
  const [total, setTotal] = useState(0);
  const [overview, setOverview] = useState<ObservationOverview | null>(null);

  const [q, setQ] = useState('');
  const [status, setStatus] = useState('');
  const [agentId, setAgentId] = useState('');
  const [model, setModel] = useState('');
  // 会话筛选的唯一来源是 URL：会话列表点「失败」胶囊跳到这里时直接带 session_id，
  // 用 state 复制一份会出现「清掉筛选但 URL 还留着」的不一致。
  const [searchParams, setSearchParams] = useSearchParams();
  const sessionId = searchParams.get('session_id') ?? '';
  const [range, setRange] = useState<RangePreset>('all');
  const [customFrom, setCustomFrom] = useState('');
  const [customTo, setCustomTo] = useState('');
  const [minDuration, setMinDuration] = useState('');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState<number>(50);

  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<ObservationTurnDetail | null>(null);

  // 任一筛选变化都回到第一页，避免停在越界页看到空表。
  const resetToFirstPage = () => setPage(0);

  const clearSessionFilter = useCallback(() => {
    const next = new URLSearchParams(searchParams);
    next.delete('session_id');
    setSearchParams(next, { replace: true });
    setPage(0);
  }, [searchParams, setSearchParams]);

  const buildFilters = useCallback((): ObservationFilters => {
    const out: ObservationFilters = {};
    const keyword = q.trim();
    if (keyword !== '') out.q = keyword;
    if (sessionId !== '') out.session_id = sessionId;
    if (status !== '') out.status = status;
    if (agentId !== '') out.agent_id = agentId;
    if (model !== '') out.model = model;
    if (range === 'custom') {
      if (customFrom === '' || customTo === '') {
        throw new Error(tr('自定义时间范围必须同时填写起止时间', 'Custom range needs both start and end'));
      }
      out.started_from = parseLocalDateTime(customFrom);
      out.started_to = parseLocalDateTime(customTo);
      if (out.started_from > out.started_to) {
        throw new Error(tr('开始时间不能晚于结束时间', 'Start time cannot be later than end time'));
      }
    } else {
      const { from, to } = rangeFromPreset(range);
      if (from) out.started_from = from;
      if (to) out.started_to = to;
    }
    if (minDuration !== '') {
      out.min_duration_ms = Number.parseInt(minDuration, 10);
    }
    return out;
  }, [q, sessionId, status, agentId, model, range, customFrom, customTo, minDuration, tr]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const filters = buildFilters();
      const [res, ov] = await Promise.all([
        listObservationTurns({ ...filters, limit: pageSize, offset: page * pageSize }),
        getObservationOverview(filters),
      ]);
      if (!Array.isArray(res.items)) {
        throw new Error('观测列表缺少 items');
      }
      if (typeof res.total !== 'number') {
        throw new Error('观测列表缺少 total');
      }
      setItems(res.items);
      setTotal(res.total);
      setOverview(ov);
      setErr(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [buildFilters, page, pageSize]);

  useEffect(() => {
    const t = window.setTimeout(() => {
      void load();
    }, 250);
    return () => window.clearTimeout(t);
  }, [load]);

  const openDetail = async (traceId: string) => {
    try {
      const row = await getObservationTurn(traceId);
      setSelected(row);
      setErr(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
    }
  };

  const resetFilters = () => {
    setQ('');
    setStatus('');
    setAgentId('');
    setModel('');
    setRange('all');
    setCustomFrom('');
    setCustomTo('');
    setMinDuration('');
    setPage(0);
  };

  const agentOptions = useMemo(
    () => [
      { value: '', label: tr('全部助理', 'All agents') },
      ...(overview?.agents ?? []).map((a) => ({ value: a, label: a })),
    ],
    [overview, tr],
  );

  const modelOptions = useMemo(
    () => [
      { value: '', label: tr('全部模型', 'All models') },
      ...(overview?.models ?? []).map((m) => ({ value: m, label: m })),
    ],
    [overview, tr],
  );

  useAssistantPage('可观测', 'Observability', {
    total,
    page,
    page_size: pageSize,
    session_id: sessionId || 'all',
    status: status || 'all',
    agent_id: agentId || 'all',
    model: model || 'all',
    range,
    min_duration_ms: minDuration || 'all',
    loading,
  });

  const stats = overview?.stats;
  // 表格里的延迟条按当前页最慢一档归一，避免绝对值差异太大导致视觉失灵
  const maxLatency = useMemo(
    () => items.reduce((acc, row) => Math.max(acc, row.duration_ms ?? 0), 0),
    [items],
  );

  const errorRate =
    stats !== null && stats !== undefined && stats.total > 0
      ? Math.round((stats.error_count / stats.total) * 100)
      : 0;
  const avgTokens =
    stats !== null && stats !== undefined && stats.total > 0
      ? Math.round(stats.total_tokens / stats.total)
      : null;

  // 已生效的筛选条件：以可读文案列出，点击即撤销单个条件。
  const activeChips = useMemo(() => {
    const chips: { key: string; label: string; clear(): void }[] = [];
    if (sessionId !== '') {
      chips.push({
        key: 'session',
        // 会话 id 是 36 位 UUID，只显示前 8 位，完整值在 title 里
        label: tr(`会话：${sessionId.slice(0, 8)}`, `Session: ${sessionId.slice(0, 8)}`),
        clear: clearSessionFilter,
      });
    }
    if (status !== '') {
      const style = statusStyle(status);
      chips.push({
        key: 'status',
        label: tr(`状态：${style.zh}`, `Status: ${style.en}`),
        clear: () => {
          resetToFirstPage();
          setStatus('');
        },
      });
    }
    if (agentId !== '') {
      chips.push({
        key: 'agent',
        label: tr(`助理：${agentId}`, `Agent: ${agentId}`),
        clear: () => {
          resetToFirstPage();
          setAgentId('');
        },
      });
    }
    if (model !== '') {
      chips.push({
        key: 'model',
        label: tr(`模型：${model}`, `Model: ${model}`),
        clear: () => {
          resetToFirstPage();
          setModel('');
        },
      });
    }
    if (range !== 'all') {
      const presets: Record<Exclude<RangePreset, 'all'>, { zh: string; en: string }> = {
        today: { zh: '今天', en: 'Today' },
        '7d': { zh: '近 7 天', en: 'Last 7 days' },
        '30d': { zh: '近 30 天', en: 'Last 30 days' },
        custom: { zh: '自定义', en: 'Custom' },
      };
      const preset = presets[range];
      chips.push({
        key: 'range',
        label: tr(`时间：${preset.zh}`, `Time: ${preset.en}`),
        clear: () => {
          resetToFirstPage();
          setRange('all');
          setCustomFrom('');
          setCustomTo('');
        },
      });
    }
    if (minDuration !== '') {
      const minLabel = formatDurationMs(Number.parseInt(minDuration, 10));
      chips.push({
        key: 'duration',
        label: tr(`延迟：≥ ${minLabel}`, `Latency: ≥ ${minLabel}`),
        clear: () => {
          resetToFirstPage();
          setMinDuration('');
        },
      });
    }
    return chips;
  }, [sessionId, status, agentId, model, range, minDuration, tr, clearSessionFilter]);

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tr('可观测', 'Observability')}
        subtitle={tr(
          '对话回合 Trace：延迟、token、错误；支持多条件筛选与分页',
          'Chat-turn traces: latency, tokens, errors; multi-filter and paging',
        )}
        actions={
          <RowAction
            icon={RefreshCw}
            iconClassName={loading ? 'animate-spin' : undefined}
            disabled={loading}
            onClick={() => void load()}
          >
            {tr('刷新', 'Refresh')}
          </RowAction>
        }
      />

      <FilterBar
        className="mx-6 mt-3"
        footer={
          range === 'custom' || activeChips.length > 0 ? (
            <>
              {range === 'custom' ? (
                <>
                  <Input
                    type="datetime-local"
                    aria-label={tr('开始时间', 'Start')}
                    value={customFrom}
                    onChange={(e) => {
                      resetToFirstPage();
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
                      resetToFirstPage();
                      setCustomTo(e.target.value);
                    }}
                    className="h-8 w-[12.5rem] text-[12px]"
                  />
                </>
              ) : null}
              {activeChips.map((chip) => (
                <FilterChip key={chip.key} label={chip.label} onClear={chip.clear} />
              ))}
              {activeChips.length > 0 ? (
                <Button
                  variant="ghost"
                  size="sm"
                  type="button"
                  onClick={resetFilters}
                  className="ml-auto inline-flex h-7 items-center gap-1 px-1.5 text-[11px] text-zinc-500"
                >
                  <RotateCcw size={11} />
                  {tr('清除全部', 'Clear all')}
                </Button>
              ) : null}
            </>
          ) : null
        }
      >
        <SearchInput
          value={q}
          onChange={(v) => {
            resetToFirstPage();
            setQ(v);
          }}
          placeholder={tr('搜索 trace / 会话 / 请求 ID', 'Search trace / session / request ID')}
          ariaLabel={tr('搜索', 'Search')}
          inputClassName="font-mono"
        />
          <FilterPill
            label={tr('状态', 'Status')}
            active={status !== ''}
            onClear={() => {
              resetToFirstPage();
              setStatus('');
            }}
            width="w-[7.5rem]"
          >
            <Select
              aria-label={tr('状态', 'Status')}
              value={status}
              onValueChange={(v) => {
                resetToFirstPage();
                setStatus(v);
              }}
              options={[
                { value: '', label: tr('全部状态', 'All') },
                { value: 'running', label: tr('进行中', 'Running') },
                { value: 'ok', label: tr('成功', 'OK') },
                { value: 'error', label: tr('失败', 'Error') },
                { value: 'stopped', label: tr('已停止', 'Stopped') },
              ]}
              variant="ghost"
              className="h-7 min-w-0 border-0 bg-transparent px-0 text-[12px] shadow-none"
            />
          </FilterPill>
          <FilterPill
            label={tr('助理', 'Agent')}
            active={agentId !== ''}
            onClear={() => {
              resetToFirstPage();
              setAgentId('');
            }}
            width="w-[9rem]"
          >
            <Select
              aria-label={tr('助理', 'Agent')}
              value={agentId}
              onValueChange={(v) => {
                resetToFirstPage();
                setAgentId(v);
              }}
              options={agentOptions}
              variant="ghost"
              className="h-7 min-w-0 border-0 bg-transparent px-0 text-[12px] shadow-none"
            />
          </FilterPill>
          <FilterPill
            label={tr('模型', 'Model')}
            active={model !== ''}
            onClear={() => {
              resetToFirstPage();
              setModel('');
            }}
            width="w-[11rem]"
          >
            <Select
              aria-label={tr('模型', 'Model')}
              value={model}
              onValueChange={(v) => {
                resetToFirstPage();
                setModel(v);
              }}
              options={modelOptions}
              variant="ghost"
              className="h-7 min-w-0 border-0 bg-transparent px-0 text-[12px] shadow-none"
            />
          </FilterPill>
          <FilterPill
            label={tr('时间', 'Time')}
            active={range !== 'all'}
            onClear={() => {
              resetToFirstPage();
              setRange('all');
              setCustomFrom('');
              setCustomTo('');
            }}
            width="w-[8.5rem]"
          >
            <Select
              aria-label={tr('时间范围', 'Time range')}
              value={range}
              onValueChange={(v) => {
                resetToFirstPage();
                setRange(v as RangePreset);
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
          <FilterPill
            label={tr('延迟', 'Latency')}
            active={minDuration !== ''}
            onClear={() => {
              resetToFirstPage();
              setMinDuration('');
            }}
            width="w-[6.5rem]"
          >
            <Select
              aria-label={tr('最低延迟', 'Min latency')}
              value={minDuration}
              onValueChange={(v) => {
                resetToFirstPage();
                setMinDuration(v);
              }}
              options={[
                { value: '', label: tr('不限', 'Any') },
                { value: '1000', label: '≥ 1 s' },
                { value: '3000', label: '≥ 3 s' },
                { value: '10000', label: '≥ 10 s' },
                { value: '60000', label: '≥ 60 s' },
              ]}
              variant="ghost"
              className="h-7 min-w-0 border-0 bg-transparent px-0 text-[12px] shadow-none"
            />
          </FilterPill>
      </FilterBar>

      <div className="mx-6 mt-3 grid shrink-0 grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
        <MetricCard
          icon={Activity}
          label={tr('回合数', 'Turns')}
          value={stats ? formatNumber(stats.total) : '—'}
          hint={
            stats
              ? tr(
                  `${stats.error_count} 失败 · ${stats.running_count} 进行中`,
                  `${stats.error_count} errors · ${stats.running_count} running`,
                )
              : undefined
          }
        />
        <MetricCard
          icon={CircleAlert}
          tone={stats && stats.error_count > 0 ? 'danger' : 'default'}
          label={tr('失败', 'Errors')}
          value={stats ? formatNumber(stats.error_count) : '—'}
          hint={stats ? tr(`失败率 ${errorRate}%`, `${errorRate}% of turns`) : undefined}
          bar={stats ? errorRate : undefined}
        />
        <MetricCard
          icon={Timer}
          label="P95"
          value={stats ? formatDurationMs(stats.p95_duration_ms) : '—'}
          hint={stats ? tr(`平均 ${formatDurationMs(stats.avg_duration_ms)}`, `avg ${formatDurationMs(stats.avg_duration_ms)}`) : undefined}
        />
        <MetricCard
          icon={Coins}
          label="tokens"
          value={stats ? formatNumber(stats.total_tokens) : '—'}
          hint={avgTokens == null ? undefined : tr(`均值 ${formatNumber(avgTokens)}`, `${formatNumber(avgTokens)} avg`)}
        />
        <MetricCard
          icon={SquareTerminal}
          tone="ok"
          label={tr('工具调用', 'Tool calls')}
          value={stats ? formatNumber(stats.tool_call_total) : '—'}
          hint={tr('按回合累加', 'Summed per turn')}
        />
        <MetricCard
          icon={Sparkles}
          tone="accent"
          label={tr('技能调用', 'Skill calls')}
          value={stats ? formatNumber(stats.skill_call_total) : '—'}
          hint={tr('按回合累加', 'Summed per turn')}
        />
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden px-6 py-3">
        {err ? (
          <div className="mb-3 rounded-lg border border-red-500/40 bg-red-500/5 px-4 py-3 text-sm text-red-300">
            {err}
          </div>
        ) : null}
        {loading && items.length === 0 ? (
          <div className="flex h-40 items-center justify-center gap-2 text-[12px] text-zinc-500">
            <LoaderCircle size={13} className="animate-spin" aria-hidden />
            {tr('加载中…', 'Loading…')}
          </div>
        ) : items.length === 0 ? (
          <EmptyStateCard
            icon={Activity}
            title={tr('没有匹配的观测记录', 'No matching observations')}
            hint={tr(
              '松一点筛选条件，或先发一轮对话产生 Trace',
              'Loosen the filters, or send a chat turn to produce a trace',
            )}
          />
        ) : (
          <TableCard
            footer={
              <TablePager
                page={page}
                pageSize={pageSize}
                total={total}
                shown={items.length}
                loading={loading}
                onPageChange={setPage}
                pageSizeOptions={[...PAGE_SIZES]}
                onPageSizeChange={(size) => {
                  setPage(0);
                  setPageSize(size);
                }}
              />
            }
          >
              <table className="og-action-table w-full table-fixed text-sm">
                <colgroup>
                  <col className="w-[7.5rem]" />
                  <col className="w-[5.5rem]" />
                  <col />
                  <col className="w-[7.5rem]" />
                  <col className="w-[6rem]" />
                  <col className="w-[10.5rem]" />
                </colgroup>
                <thead>
                  <tr className="whitespace-nowrap border-b border-zinc-800/60 text-[11px] text-zinc-500">
                    <th scope="col" className="sticky top-0 z-10 px-3 py-2 text-left font-medium">{tr('时间', 'Time')}</th>
                    <th scope="col" className="sticky top-0 z-10 px-3 py-2 text-left font-medium">{tr('状态', 'Status')}</th>
                    <th scope="col" className="sticky top-0 z-10 px-3 py-2 text-left font-medium">{tr('输入 / 错误', 'Input / error')}</th>
                    <th scope="col" className="sticky top-0 z-10 px-3 py-2 text-left font-medium">{tr('工具 / 技能', 'Tools / skills')}</th>
                    <th scope="col" className="sticky top-0 z-10 px-3 py-2 text-right font-medium">{tr('总计 tokens', 'Total tokens')}</th>
                    <th scope="col" className="sticky top-0 z-10 px-3 py-2 text-right font-medium">{tr('耗时', 'Latency')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/40">
                  {items.map((row) => {
                    const isError = row.status === 'error';
                    const rowStatus = statusStyle(row.status);
                    const ratio = row.duration_ms == null || maxLatency === 0 ? 0 : row.duration_ms / maxLatency;
                    const isActive = selected?.trace_id === row.trace_id;
                    const summary = isError ? (row.error_message ?? row.input_summary) : row.input_summary;
                    const tokenHint = tr(
                      `输入 ${formatTokens(row.prompt_tokens)} · 输出 ${formatTokens(row.completion_tokens)}`,
                      `in ${formatTokens(row.prompt_tokens)} · out ${formatTokens(row.completion_tokens)}`,
                    );
                    return (
                      <tr
                        key={row.trace_id}
                        className="group cursor-pointer transition-colors"
                        style={isActive ? { backgroundColor: 'rgb(99 102 241 / 0.08)' } : undefined}
                        onClick={() => void openDetail(row.trace_id)}
                      >
                        <td
                          className="px-3 py-2 align-middle"
                          title={row.started_at ? fullDateTime(row.started_at) : undefined}
                        >
                          <div className="truncate text-[12px] tabular-nums text-zinc-400">
                            {row.started_at ? fullDateTime(row.started_at) : '—'}
                          </div>
                        </td>
                        <td className="px-3 py-2 align-middle">
                          <StatusPill
                            tone={rowStatus.tone}
                            label={tr(rowStatus.zh, rowStatus.en)}
                            pulse={row.status === 'running'}
                          />
                        </td>
                        <td className="min-w-0 px-3 py-2 align-middle">
                          <div
                            className={cn(
                              'truncate text-[12.5px] leading-5',
                              isError ? 'text-red-300' : 'text-zinc-200',
                            )}
                            title={summary || undefined}
                          >
                            {summary || '—'}
                          </div>
                          <div className="mt-0.5 flex min-w-0 items-center gap-1 text-[10px] text-zinc-600">
                            <Link
                              to={`/chat/${row.session_id}`}
                              className="shrink-0 font-mono transition-colors hover:text-indigo-300 hover:underline"
                              onClick={(e) => e.stopPropagation()}
                              title={row.session_id}
                            >
                              {row.session_id.slice(0, 8)}
                            </Link>
                            <span aria-hidden>·</span>
                            <span className="shrink-0">{row.agent_id}</span>
                            <span aria-hidden>·</span>
                            <span className="min-w-0 truncate font-mono" title={`${row.provider}/${row.model}`}>
                              {row.model}
                            </span>
                          </div>
                        </td>
                        <td className="px-3 py-2 align-middle">
                          <CallCounters tools={row.tool_call_count} skills={row.skill_call_count} />
                        </td>
                        <td
                          className="px-3 py-2 text-right align-middle text-[12px] tabular-nums text-zinc-300"
                          title={tokenHint}
                        >
                          {formatTokens(row.total_tokens)}
                        </td>
                        <td className="px-3 py-2 align-middle">
                          <div className="flex items-center justify-end gap-2">
                            <span className="h-1 w-12 shrink-0 overflow-hidden rounded-full bg-zinc-800/80">
                              <span
                                className={cn('block h-full rounded-full', isError ? 'bg-danger/70' : 'bg-zinc-400/70')}
                                style={{ width: `${Math.max(ratio * 100, ratio > 0 ? 6 : 0)}%` }}
                              />
                            </span>
                            <span
                              className={cn(
                                'w-14 shrink-0 text-right text-[12px] tabular-nums',
                                isError ? 'text-red-400' : 'text-zinc-200',
                              )}
                            >
                              {formatDurationMs(row.duration_ms)}
                            </span>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
          </TableCard>
        )}
      </div>

      <Modal
        open={selected !== null}
        onClose={() => setSelected(null)}
        title={tr('Trace 详情', 'Trace detail')}
        size="xl"
        resizable
      >
        {selected === null ? null : <TraceDetail turn={selected} />}
      </Modal>
    </main>
  );
}

// 筛选胶囊、指标卡、状态胶囊已抽到 components/ui，页面只保留业务样式。
function CallCounters({ tools, skills }: { tools: number; skills: number }) {
  const { tr } = useI18n();
  if (tools === 0 && skills === 0) {
    return <span className="block text-[12px] text-zinc-600">—</span>;
  }
  return (
    <span className="flex items-center gap-1">
      {tools > 0 ? (
        <span
          className="inline-flex items-center gap-0.5 rounded border border-ok/25 bg-ok/10 px-1 text-[10px] tabular-nums text-ok"
          title={tr(`${tools} 次工具调用`, `${tools} tool calls`)}
        >
          <SquareTerminal size={9} aria-hidden />
          {tools}
        </span>
      ) : null}
      {skills > 0 ? (
        <span
          className="inline-flex items-center gap-0.5 rounded border border-accent/25 bg-accent/10 px-1 text-[10px] tabular-nums text-accent"
          title={tr(`${skills} 次技能调用`, `${skills} skill calls`)}
        >
          <Sparkles size={9} aria-hidden />
          {skills}
        </span>
      ) : null}
    </span>
  );
}

/** 单次工具 / 技能调用在回合时间轴上的位置（百分比）。 */
type WaterfallRow = {
  call: ObservationToolCall;
  offsetMs: number;
  leftPct: number;
  widthPct: number;
};

function buildWaterfall(turn: ObservationTurnDetail): { rows: WaterfallRow[]; spanMs: number } {
  const calls = turn.tool_calls;
  if (turn.started_at === null) {
    throw new Error(`回合缺少开始时间: ${turn.trace_id}`);
  }
  const startMs = toEpochMs(turn.started_at, '回合开始时间');
  const entries = calls.map((call) => {
    if (call.started_at === null) {
      throw new Error(`调用缺少开始时间: ${call.tool_call_id}`);
    }
    const offsetMs = toEpochMs(call.started_at, '调用开始时间') - startMs;
    if (offsetMs < 0) {
      throw new Error(`调用开始早于回合开始: ${call.tool_call_id}`);
    }
    return { call, offsetMs, durationMs: call.duration_ms ?? 0 };
  });
  // 进行中的回合还没有 duration_ms，用最后一次调用的结束位置兜底
  const measured = entries.reduce((acc, e) => Math.max(acc, e.offsetMs + e.durationMs), 0);
  const spanMs = turn.duration_ms ?? measured;
  if (spanMs <= 0) {
    throw new Error(`回合时间跨度非正: ${turn.trace_id}`);
  }
  return {
    spanMs,
    rows: entries.map(({ call, offsetMs, durationMs }) => {
      const leftPct = Math.min((offsetMs / spanMs) * 100, 100);
      // 极短的调用也要留出可点击宽度，但不能被挤出右边界
      const widthPct = Math.min(Math.max((durationMs / spanMs) * 100, 1.2), 100 - leftPct);
      return { call, offsetMs, leftPct, widthPct };
    }),
  };
}

function TraceDetail({ turn }: { turn: ObservationTurnDetail }) {
  const { tr } = useI18n();
  const style = statusStyle(turn.status);
  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-zinc-800/60 pb-4">
        <div className="min-w-0 space-y-1.5">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <StatusPill
              tone={style.tone}
              label={tr(style.zh, style.en)}
              pulse={turn.status === 'running'}
              className="h-5 px-2 text-[11px] font-medium"
            />
            <span className="text-[22px] font-semibold leading-none tabular-nums text-zinc-100">
              {formatDurationMs(turn.duration_ms)}
            </span>
            <span className="text-[11px] text-zinc-500">{tr('总耗时', 'total')}</span>
          </div>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-zinc-500">
            <span className="text-zinc-300">{turn.agent_id}</span>
            <span aria-hidden>·</span>
            <span className="truncate font-mono">{turn.model}</span>
            <span aria-hidden>·</span>
            <span>{turn.provider}</span>
            <span aria-hidden>·</span>
            <span className="tabular-nums">{turn.started_at ? fullDateTime(turn.started_at) : '—'}</span>
          </div>
        </div>
        <Link
          to={`/chat/${turn.session_id}`}
          className={rowActionClass()}
        >
          {tr('打开会话', 'Open session')}
          <ArrowUpRight size={12} aria-hidden />
        </Link>
      </header>

      <DetailStatGrid>
        <DetailStat label={tr('输入 tokens', 'Input tokens')} value={formatTokens(turn.prompt_tokens)} />
        <DetailStat label={tr('输出 tokens', 'Output tokens')} value={formatTokens(turn.completion_tokens)} />
        <DetailStat label={tr('总计 tokens', 'Total tokens')} value={formatTokens(turn.total_tokens)} />
        <DetailStat
          label={tr('工具 / 技能', 'Tools / skills')}
          value={`${turn.tool_call_count} / ${turn.skill_call_count}`}
        />
      </DetailStatGrid>

      <TraceTimeline turn={turn} />

      <DetailSection title={tr('上下文', 'Context')}>
        <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
          <MetaRow label="trace_id" value={turn.trace_id} copyable />
          <MetaRow label="session_id" value={turn.session_id} copyable />
          <MetaRow label="request_id" value={turn.request_id || '—'} copyable={turn.request_id !== ''} />
          <MetaRow
            label={tr('结束时间', 'Ended at')}
            value={turn.ended_at ? fullDateTime(turn.ended_at) : '—'}
          />
        </dl>
      </DetailSection>

      <DetailSection title={tr('输入摘要', 'Input summary')}>
        <p className="whitespace-pre-wrap break-words rounded-lg border border-zinc-800/60 bg-zinc-900/30 px-3 py-2 text-[12px] leading-5 text-zinc-300">
          {turn.input_summary || '—'}
        </p>
      </DetailSection>

      {turn.error_message ? (
        <DetailSection title={tr('错误', 'Error')} tone="danger">
          <p className="whitespace-pre-wrap break-words rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 font-mono text-[11px] leading-5 text-danger">
            {turn.error_message}
          </p>
        </DetailSection>
      ) : null}
    </div>
  );
}

/** 回合时间轴：每次工具 / 技能调用按真实开始时间定位，直观看出串行与等待。 */
function TraceTimeline({ turn }: { turn: ObservationTurnDetail }) {
  const { tr } = useI18n();
  const calls = turn.tool_calls;

  if (calls.length === 0) {
    return (
      <DetailSection title={tr('时间线', 'Timeline')}>
        <p className="rounded-lg border border-dashed border-zinc-800/60 px-3 py-3 text-[12px] text-zinc-500">
          {tr('本回合没有调用工具或技能', 'No tool or skill calls in this turn')}
        </p>
      </DetailSection>
    );
  }

  const { rows, spanMs } = buildWaterfall(turn);

  return (
    <DetailSection
      title={tr(`时间线 · ${calls.length} 次调用`, `Timeline · ${calls.length} calls`)}
      actions={
        <span className="shrink-0 text-[10px] tabular-nums text-zinc-600">
          0 → {formatDurationMs(spanMs)}
        </span>
      }
    >
      <div className="space-y-1.5">
        {rows.map(({ call, offsetMs, leftPct, widthPct }) => {
          const kind = kindStyle(call.kind);
          const Icon = kind.Icon;
          const isError = call.status === 'error';
          return (
            <div key={`${call.tool_call_id}-${call.name}`} className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
              <div className="flex w-full min-w-0 items-center gap-1.5 sm:w-[16rem] sm:shrink-0">
                <span className={cn('inline-flex shrink-0 items-center rounded border px-1 py-0.5 text-[10px]', kind.chip)}>
                  <Icon size={9} aria-hidden className="mr-0.5" />
                  {tr(kind.zh, kind.en)}
                </span>
                <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-zinc-300" title={call.name}>
                  {call.name}
                </span>
                {call.detail !== '' && !call.name.includes(call.detail) ? (
                  <span className="max-w-[45%] shrink-0 truncate text-[10px] text-zinc-600" title={call.detail}>
                    · {call.detail}
                  </span>
                ) : null}
              </div>
              <div className="flex min-w-0 flex-1 items-center gap-2">
                <div
                  className="relative h-4 min-w-0 flex-1 overflow-hidden rounded bg-zinc-800/50"
                  title={`${formatDurationMs(call.duration_ms)} @ +${formatDurationMs(offsetMs)}`}
                >
                  <span
                    className={cn('absolute inset-y-0 rounded', isError ? 'bg-danger/70' : kind.bar)}
                    style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
                  />
                </div>
                <span
                  className={cn(
                    'w-16 shrink-0 text-right text-[11px] tabular-nums',
                    isError ? 'text-danger' : 'text-zinc-400',
                  )}
                >
                  {formatDurationMs(call.duration_ms)}
                </span>
              </div>
              {isError && call.error ? (
                <span className="-mt-0.5 w-full min-w-0 flex-1 truncate text-[10px] text-danger" title={call.error}>
                  {call.error}
                </span>
              ) : null}
            </div>
          );
        })}
      </div>
      <p className="mt-2 text-[10px] text-zinc-600">
        {tr(
          '色块为该次调用的实际耗时，空白处是模型生成与等待时间',
          'Bars are the actual call durations; the gaps are model generation and wait time',
        )}
      </p>
    </DetailSection>
  );
}

