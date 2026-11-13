import { useCallback, useEffect, useMemo, useState } from 'react';
import { Loader2, Play, RefreshCw, Terminal, Timer } from 'lucide-react';
import {
  getSandboxExecution,
  getSandboxSummary,
  listSandboxExecutions,
  runSandboxCode,
  type SandboxExecution,
  type SandboxSource,
  type SandboxStatus,
  type SandboxSummary,
} from '@/api/sandbox';
import { ApiError } from '@/api/client';
import { Modal } from '@/components/Modal';
import {
  Button,
  Chip,
  CodeBlock,
  COL,
  DetailSection,
  DetailStat,
  DetailStatGrid,
  EmptyStateCard,
  ErrorBanner,
  FilterBar,
  FilterChip,
  FilterPill,
  Input,
  Label,
  MetricCard,
  PageHeader,
  RowAction,
  SearchInput,
  Select,
  StatusPill,
  TableCard,
  TableHead,
  TablePager,
  Textarea,
  Toast,
  tableLayout,
  type ToastTone,
} from '@/components/ui';
import { useI18n } from '@/i18n/locale';
import { useAssistantPage } from '@/lib/assistantPage';
import { cn } from '@/lib/cn';
import { errorText } from '@/lib/errors';
import { fullDateTime } from '@/lib/format';
import { useRequestSeq } from '@/lib/requestSeq';

/** 来源是「分类」而不是「状态」，用 Chip；状态用 StatusPill。 */
const SOURCE_META: Record<SandboxSource, { zh: string; en: string; tone: 'info' | 'accent' | 'default' }> = {
  skill: { zh: '技能脚本', en: 'Skill script', tone: 'info' },
  workflow: { zh: '工作流节点', en: 'Workflow node', tone: 'accent' },
  manual: { zh: '手动试跑', en: 'Manual run', tone: 'default' },
};

const STATUS_META: Record<SandboxStatus, { zh: string; en: string; tone: 'ok' | 'danger' | 'warn' }> = {
  succeeded: { zh: '成功', en: 'Succeeded', tone: 'ok' },
  failed: { zh: '失败', en: 'Failed', tone: 'danger' },
  timeout: { zh: '超时', en: 'Timeout', tone: 'warn' },
};

const SOURCE_FILTERS: (SandboxSource | '')[] = ['', 'skill', 'workflow', 'manual'];
const STATUS_FILTERS: (SandboxStatus | '')[] = ['', 'succeeded', 'failed', 'timeout'];

/** 耗时按量级换单位；执行多在秒级，毫秒级只在极快脚本上出现。单位用中性写法，两种语言都成立。 */
function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

/** 一行代码预览：单元格里只放第一行，完整内容进详情。 */
function codePreview(code: string): string {
  const firstLine = code.split('\n').find((line) => line.trim() !== '') ?? '';
  const trimmed = firstLine.trim();
  return trimmed === '' ? '—' : trimmed;
}

export default function SandboxPage() {
  const { tr } = useI18n();
  const [items, setItems] = useState<SandboxExecution[]>([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<SandboxSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [source, setSource] = useState<SandboxSource | ''>('');
  const [status, setStatus] = useState<SandboxStatus | ''>('');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(20);
  const [viewing, setViewing] = useState<SandboxExecution | null>(null);
  const [runOpen, setRunOpen] = useState(false);
  const [toast, setToast] = useState<{ tone: ToastTone; text: string } | null>(null);
  const seq = useRequestSeq();

  useAssistantPage('沙箱', 'Sandbox', {
    来源: source === '' ? '' : SOURCE_META[source].zh,
    状态: status === '' ? '' : STATUS_META[status].zh,
    关键字: query,
  });

  const flash = useCallback((tone: ToastTone, text: string) => {
    setToast({ tone, text });
    window.setTimeout(() => setToast(null), 4000);
  }, []);

  const load = useCallback(
    async (silent = false) => {
      const current = seq.next();
      if (silent) setRefreshing(true);
      else setLoading(true);
      try {
        const [list, sum] = await Promise.all([
          listSandboxExecutions({
            query,
            source,
            status,
            limit: pageSize,
            offset: page * pageSize,
          }),
          getSandboxSummary(),
        ]);
        if (seq.isStale(current)) return;
        setItems(list.items);
        setTotal(list.total);
        setSummary(sum);
        setErr(null);
      } catch (e) {
        if (seq.isStale(current)) return;
        setErr(errorText(e));
      } finally {
        if (!seq.isStale(current)) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [query, source, status, page, pageSize, seq],
  );

  // 筛选与翻页都重建 load：统一走 250ms 防抖，避免连点筛选打出一串请求。
  useEffect(() => {
    const timer = window.setTimeout(() => {
      void load();
    }, 250);
    return () => window.clearTimeout(timer);
  }, [load]);

  const clearAll = useCallback(() => {
    setQuery('');
    setSource('');
    setStatus('');
    setPage(0);
  }, []);

  const activeChips = useMemo(() => {
    const chips: { key: string; label: string; clear(): void }[] = [];
    if (query.trim() !== '') {
      chips.push({
        key: 'query',
        label: tr(`关键字：${query.trim()}`, `Keyword: ${query.trim()}`),
        clear: () => {
          setQuery('');
          setPage(0);
        },
      });
    }
    if (source !== '') {
      chips.push({
        key: 'source',
        label: tr(`来源：${SOURCE_META[source].zh}`, `Source: ${SOURCE_META[source].en}`),
        clear: () => {
          setSource('');
          setPage(0);
        },
      });
    }
    if (status !== '') {
      chips.push({
        key: 'status',
        label: tr(`状态：${STATUS_META[status].zh}`, `Status: ${STATUS_META[status].en}`),
        clear: () => {
          setStatus('');
          setPage(0);
        },
      });
    }
    return chips;
  }, [query, source, status, tr]);

  const counts = useMemo(() => {
    const byStatus = summary?.by_status ?? {};
    return {
      total: summary?.total ?? 0,
      succeeded: byStatus.succeeded ?? 0,
      failed: byStatus.failed ?? 0,
      timeout: byStatus.timeout ?? 0,
      avg: summary?.avg_duration_ms ?? 0,
    };
  }, [summary]);

  const hasFilter = activeChips.length > 0;

  return (
    <main className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title={tr('沙箱', 'Sandbox')}
        subtitle={tr(
          '不可信代码的统一执行入口：技能脚本、工作流代码节点与手动试跑都在容器里跑，宿主只拿到输出',
          'One execution path for untrusted code: skill scripts, workflow code nodes and manual runs all run in a container',
        )}
        actions={
          <>
            <RowAction
              icon={RefreshCw}
              iconClassName={cn(refreshing && 'animate-spin')}
              disabled={loading || refreshing}
              onClick={() => void load(true)}
            >
              {tr('刷新', 'Refresh')}
            </RowAction>
            <Button variant="primary" size="sm" onClick={() => setRunOpen(true)}>
              <Play size={12} aria-hidden />
              {tr('运行代码', 'Run code')}
            </Button>
          </>
        }
      />

      <FilterBar
        className="mx-6 mt-3"
        footer={
          hasFilter ? (
            <>
              {activeChips.map((chip) => (
                <FilterChip key={chip.key} label={chip.label} onClear={chip.clear} />
              ))}
              <Button variant="ghost" size="sm" onClick={clearAll}>
                {tr('清除全部', 'Clear all')}
              </Button>
            </>
          ) : null
        }
      >
        <SearchInput
          value={query}
          onChange={(next) => {
            setQuery(next);
            setPage(0);
          }}
          placeholder={tr('搜索代码 / 输出 / 来源标识', 'Search code / output / source ref')}
          ariaLabel={tr('搜索执行记录', 'Search executions')}
          className="min-w-[16rem] flex-1"
        />
        <FilterPill
          label={tr('来源', 'Source')}
          active={source !== ''}
          onClear={() => {
            setSource('');
            setPage(0);
          }}
          width="w-[10.5rem]"
        >
          <Select
            variant="ghost"
            aria-label={tr('来源', 'Source')}
            value={source}
            onValueChange={(next) => {
              setSource(next as SandboxSource | '');
              setPage(0);
            }}
            options={SOURCE_FILTERS.map((value) => ({
              value,
              label: value === '' ? tr('全部', 'All') : tr(SOURCE_META[value].zh, SOURCE_META[value].en),
            }))}
          />
        </FilterPill>
        <FilterPill
          label={tr('状态', 'Status')}
          active={status !== ''}
          onClear={() => {
            setStatus('');
            setPage(0);
          }}
          width="w-[9.5rem]"
        >
          <Select
            variant="ghost"
            aria-label={tr('状态', 'Status')}
            value={status}
            onValueChange={(next) => {
              setStatus(next as SandboxStatus | '');
              setPage(0);
            }}
            options={STATUS_FILTERS.map((value) => ({
              value,
              label: value === '' ? tr('全部', 'All') : tr(STATUS_META[value].zh, STATUS_META[value].en),
            }))}
          />
        </FilterPill>
        <div className="ml-auto flex items-center gap-2">
          <span className="text-[11px] tabular-nums text-zinc-500">
            {tr(`共 ${total} 条`, `${total} total`)}
          </span>
        </div>
      </FilterBar>

      {/* 列数与指标数一致：5 个指标配 5 列，行尾不会留空格子 */}
      <div className="mx-6 mt-3 grid shrink-0 grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        <MetricCard
          icon={Terminal}
          label={tr('执行总数', 'Executions')}
          value={String(counts.total)}
          hint={tr('技能 / 工作流 / 手动', 'Skill / workflow / manual')}
          active={status === ''}
          onClick={() => {
            setStatus('');
            setPage(0);
          }}
        />
        <MetricCard
          icon={Play}
          tone="ok"
          label={tr('成功', 'Succeeded')}
          value={String(counts.succeeded)}
          hint={
            counts.total === 0
              ? undefined
              : tr(`占比 ${Math.round((counts.succeeded / counts.total) * 100)}%`, `${Math.round((counts.succeeded / counts.total) * 100)}%`)
          }
          active={status === 'succeeded'}
          onClick={() => {
            setStatus('succeeded');
            setPage(0);
          }}
        />
        <MetricCard
          icon={Terminal}
          tone={counts.failed > 0 ? 'danger' : 'default'}
          label={tr('失败', 'Failed')}
          value={String(counts.failed)}
          hint={
            counts.total === 0
              ? undefined
              : tr(`占比 ${Math.round((counts.failed / counts.total) * 100)}%`, `${Math.round((counts.failed / counts.total) * 100)}%`)
          }
          bar={counts.total === 0 ? undefined : (counts.failed / counts.total) * 100}
          active={status === 'failed'}
          onClick={() => {
            setStatus('failed');
            setPage(0);
          }}
        />
        <MetricCard
          icon={Timer}
          tone={counts.timeout > 0 ? 'warn' : 'default'}
          label={tr('超时', 'Timed out')}
          value={String(counts.timeout)}
          hint={tr('超过执行上限', 'Exceeded limit')}
          active={status === 'timeout'}
          onClick={() => {
            setStatus('timeout');
            setPage(0);
          }}
        />
        <MetricCard
          icon={Timer}
          label={tr('平均耗时', 'Avg duration')}
          value={formatDuration(counts.avg)}
          hint={tr('全部执行记录', 'All executions')}
        />
      </div>

      <div className="flex min-h-0 flex-1 flex-col px-6 py-4">
        {err && (
          <ErrorBanner>
            {tr('加载失败：', 'Load failed: ')}
            {err}
          </ErrorBanner>
        )}

        {loading ? (
          <div className="flex h-40 items-center justify-center text-sm text-zinc-500">
            {tr('加载中…', 'Loading…')}
          </div>
        ) : items.length === 0 ? (
          <EmptyStateCard
            icon={Terminal}
            title={hasFilter ? tr('没有匹配的执行记录', 'No executions match') : tr('还没有执行记录', 'No executions yet')}
            hint={
              hasFilter
                ? tr('换个关键字，或清除筛选条件。', 'Try another keyword or clear filters.')
                : tr('技能脚本、工作流代码节点与手动试跑都会留在这里。', 'Skill scripts, workflow nodes and manual runs all land here.')
            }
            action={
              hasFilter ? (
                <Button variant="ghost" size="sm" onClick={clearAll}>
                  {tr('清除筛选', 'Clear filters')}
                </Button>
              ) : (
                <Button variant="primary" size="sm" onClick={() => setRunOpen(true)}>
                  <Play size={12} aria-hidden />
                  {tr('跑一段试试', 'Run something')}
                </Button>
              )
            }
          />
        ) : (
          <TableCard
            className="min-h-0 flex-1"
            footer={
              <TablePager
                page={page}
                pageSize={pageSize}
                total={total}
                shown={items.length}
                loading={loading || refreshing}
                onPageChange={setPage}
                pageSizeOptions={[20, 50, 100]}
                onPageSizeChange={(size) => {
                  setPage(0);
                  setPageSize(size);
                }}
              />
            }
          >
            <table className={tableLayout(52)}>
              <colgroup>
                <col className={COL.main} />
                <col className={COL.tag} />
                <col className={COL.tag} />
                <col className={COL.tag} />
                <col className={COL.tag} />
                <col className={COL.time} />
              </colgroup>
              <TableHead>
                <tr className="whitespace-nowrap">
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('代码 / 来源标识', 'Code / source ref')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('来源', 'Source')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('状态', 'Status')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('退出码', 'Exit')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('耗时', 'Duration')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('开始时间', 'Started')}</th>
                </tr>
              </TableHead>
              <tbody className="divide-y divide-zinc-800/40">
                {items.map((row) => (
                  <SandboxRow key={row.id} row={row} onView={() => setViewing(row)} />
                ))}
              </tbody>
            </table>
          </TableCard>
        )}
      </div>

      {viewing && (
        <ExecutionModal
          execution={viewing}
          onClose={() => setViewing(null)}
          onCopied={() => flash('ok', tr('已复制执行 ID', 'Execution ID copied'))}
        />
      )}

      {runOpen && (
        <RunCodeModal
          onClose={() => setRunOpen(false)}
          onDone={(result) => {
            setRunOpen(false);
            if (result.status === 'succeeded') {
              flash('ok', tr('沙盒执行成功', 'Sandbox run succeeded'));
            } else {
              flash('err', tr(`沙盒执行${STATUS_META[result.status].zh}`, `Sandbox run ${STATUS_META[result.status].en.toLowerCase()}`));
            }
            void load(true);
            void (async () => {
              try {
                setViewing(await getSandboxExecution(result.execution_id));
              } catch {
                // 详情拉取失败不影响「这次跑完了」这个结论，列表刷新已经足够
              }
            })();
          }}
        />
      )}

      {toast !== null && <Toast tone={toast.tone} text={toast.text} />}
    </main>
  );
}

function SandboxRow({
  row,
  onView,
}: {
  row: SandboxExecution;
  onView(): void;
}) {
  const { tr } = useI18n();
  const source = SOURCE_META[row.source];
  const status = STATUS_META[row.status];
  // source_ref 是触发方身份；手动试跑的 "manual" 与来源胶囊重复，不再重复展示
  const ref = row.source_ref !== '' && row.source_ref !== 'manual' ? row.source_ref : '';
  const meta = [ref, row.workflow_run_id, row.session_id].filter((part) => part !== '');

  return (
    <tr className="transition-colors hover:bg-zinc-900/40">
      <td className="min-w-0 px-3 py-2">
        <button
          type="button"
          onClick={onView}
          className={cn(
            'block w-full truncate text-left font-mono text-[12.5px] font-medium transition-colors hover:text-indigo-300',
            row.status === 'failed' ? 'text-danger' : 'text-zinc-100',
          )}
          title={codePreview(row.code)}
        >
          {codePreview(row.code)}
        </button>
        <div className="mt-0.5 truncate text-[10px] text-zinc-500" title={row.source_ref}>
          {meta.length === 0 ? '—' : meta.join(' · ')}
        </div>
      </td>
      <td className="whitespace-nowrap px-3 py-2">
        <Chip tone={source.tone} dense>
          {tr(source.zh, source.en)}
        </Chip>
      </td>
      <td className="whitespace-nowrap px-3 py-2">
        <StatusPill tone={status.tone} label={tr(status.zh, status.en)} />
      </td>
      <td className="whitespace-nowrap px-3 py-2 font-mono text-[11px] tabular-nums text-zinc-400">
        {row.exit_code === null ? <span className="text-zinc-600">{tr('无', 'n/a')}</span> : row.exit_code}
      </td>
      <td className="whitespace-nowrap px-3 py-2 font-mono text-[11px] tabular-nums text-zinc-400">
        {formatDuration(row.duration_ms)}
      </td>
      <td className="whitespace-nowrap px-3 py-2 text-[11px] tabular-nums text-zinc-500">
        {row.started_at === null ? '—' : fullDateTime(row.started_at)}
      </td>
    </tr>
  );
}

function ExecutionModal({
  execution,
  onClose,
  onCopied,
}: {
  execution: SandboxExecution;
  onClose(): void;
  onCopied(): void;
}) {
  const { tr } = useI18n();
  const status = STATUS_META[execution.status];
  const source = SOURCE_META[execution.source];
  // 标题只放对象名：有来源标识就用它，手动试跑没有名字，用来源本身当身份
  const title =
    execution.source_ref !== '' && execution.source_ref !== 'manual'
      ? execution.source_ref
      : tr('手动试跑', 'Manual run');

  return (
    <Modal
      open
      onClose={onClose}
      title={title}
      size="xl"
      resizable
      footer={
        <Button variant="outline" size="sm" onClick={onClose}>
          {tr('关闭', 'Close')}
        </Button>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-2">
          <StatusPill tone={status.tone} label={tr(status.zh, status.en)} />
          <span className="text-[11px] tabular-nums text-zinc-400">{formatDuration(execution.duration_ms)}</span>
          <span className="text-[11px] text-zinc-600">·</span>
          {/* 执行 ID 是这条记录的身份，复制入口放在它旁边，列表里不再单列一列操作 */}
          <button
            type="button"
            onClick={() => void navigator.clipboard.writeText(execution.id).then(onCopied)}
            aria-label={tr('复制执行 ID', 'Copy execution ID')}
            title={tr('复制执行 ID', 'Copy execution ID')}
            className="font-mono text-[11px] text-zinc-500 transition-colors hover:text-indigo-300"
          >
            {execution.id}
          </button>
        </div>

        <DetailStatGrid>
          <DetailStat label={tr('来源', 'Source')} value={tr(source.zh, source.en)} />
          <DetailStat
            label={tr('退出码', 'Exit code')}
            value={execution.exit_code === null ? '—' : String(execution.exit_code)}
          />
          <DetailStat label={tr('耗时', 'Duration')} value={formatDuration(execution.duration_ms)} />
          <DetailStat
            label={tr('开始时间', 'Started')}
            value={execution.started_at === null ? '—' : fullDateTime(execution.started_at)}
          />
        </DetailStatGrid>

        <DetailSection title={tr('代码', 'Code')}>
          <CodeBlock maxHeightClassName="max-h-60">{execution.code}</CodeBlock>
        </DetailSection>

        <DetailSection title={tr('标准输出', 'stdout')}>
          {execution.stdout === '' ? (
            <p className="text-[11.5px] text-zinc-600">{tr('这次执行没有产生标准输出', 'No stdout for this run')}</p>
          ) : (
            <CodeBlock maxHeightClassName="max-h-60">{execution.stdout}</CodeBlock>
          )}
        </DetailSection>

        {execution.stderr !== '' && (
          <DetailSection title={tr('标准错误', 'stderr')}>
            <CodeBlock maxHeightClassName="max-h-60">{execution.stderr}</CodeBlock>
          </DetailSection>
        )}

        {execution.error !== null && execution.error !== '' && (
          <DetailSection title={tr('错误', 'Error')} tone="danger">
            <CodeBlock maxHeightClassName="max-h-40">{execution.error}</CodeBlock>
          </DetailSection>
        )}

        <DetailSection title={tr('运行上下文', 'Context')}>
          <DetailStatGrid columns={2}>
            <DetailStat label={tr('镜像', 'Image')} value={execution.image} />
            <DetailStat
              label={tr('网络', 'Network')}
              value={execution.network_enabled ? tr('允许联网', 'Network enabled') : tr('已断网', 'Network disabled')}
            />
            <DetailStat label={tr('会话', 'Session')} value={execution.session_id === '' ? '—' : execution.session_id} />
            <DetailStat
              label={tr('工作流运行', 'Workflow run')}
              value={execution.workflow_run_id === '' ? '—' : execution.workflow_run_id}
            />
          </DetailStatGrid>
        </DetailSection>
      </div>
    </Modal>
  );
}

function RunCodeModal({
  onClose,
  onDone,
}: {
  onClose(): void;
  onDone(result: { execution_id: string; status: SandboxStatus }): void;
}) {
  const { tr } = useI18n();
  const [code, setCode] = useState("print('hello from sandbox')");
  const [libraries, setLibraries] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      const packages = libraries
        .split(/[\s,]+/)
        .map((name) => name.trim())
        .filter((name) => name !== '');
      const result = await runSandboxCode(code, packages);
      onDone({ execution_id: result.execution_id, status: result.status });
    } catch (e) {
      // 环境不可用（503）与入参非法（400）都要留在弹窗里：关掉弹窗用户就看不到原因了
      setErr(e instanceof ApiError ? e.message : errorText(e));
    } finally {
      setBusy(false);
    }
  }, [code, libraries, onDone]);

  return (
    <Modal
      open
      onClose={onClose}
      title={tr('运行代码', 'Run code')}
      size="xl"
      resizable
      footer={
        <>
          <Button variant="outline" size="sm" onClick={onClose} disabled={busy}>
            {tr('取消', 'Cancel')}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => void submit()}
            disabled={busy || code.trim() === ''}
          >
            {busy ? <Loader2 size={12} className="animate-spin" aria-hidden /> : <Play size={12} aria-hidden />}
            {busy ? tr('运行中…', 'Running…') : tr('运行', 'Run')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {err !== null && <ErrorBanner>{err}</ErrorBanner>}

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="sandbox-code">{tr('Python 代码', 'Python code')}</Label>
          <Textarea
            id="sandbox-code"
            className="min-h-[14rem] font-mono text-[11.5px]"
            value={code}
            spellCheck={false}
            onChange={(event) => setCode(event.target.value)}
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="sandbox-libraries">{tr('依赖（可选，逗号分隔）', 'Dependencies (optional, comma separated)')}</Label>
          <Input
            id="sandbox-libraries"
            value={libraries}
            placeholder="requests==2.31.0, numpy"
            onChange={(event) => setLibraries(event.target.value)}
          />
          <p className="text-[11px] leading-4 text-zinc-500">
            {tr(
              '沙盒默认断网，pip 装不了依赖；需要预装依赖请换镜像，或先打开 SANDBOX_NETWORK_ENABLED。',
              'The sandbox is offline by default, so pip cannot fetch packages; use a prebuilt image or enable SANDBOX_NETWORK_ENABLED.',
            )}
          </p>
        </div>

        <p className="text-[11px] leading-4 text-zinc-600">
          {tr(
            '代码在一次性容器里执行：断网、丢弃全部 capability、限制内存与进程数；宿主机只拿到输出与退出码。',
            'Code runs in a disposable container: no network, all capabilities dropped, memory and process limits; the host only gets output and the exit code.',
          )}
        </p>
      </div>
    </Modal>
  );
}
