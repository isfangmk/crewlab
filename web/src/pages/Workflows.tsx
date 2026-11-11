// 工作流列表：编排（画布清单）与运行记录两种视图。
//
// 画布清单走服务端分页：搜索、状态筛选与排序都交给后端，页码/每页条数由 TablePager 控制。
// 运行记录同样走服务端分页，可按工作流过滤，从左侧导航或行内「历史」进入。

import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  ArrowDown,
  ArrowUp,
  ArrowUpRight,
  History,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Trash2,
  Workflow as WorkflowIcon,
} from 'lucide-react';
import { ApiError } from '@/api/client';
import {
  blankDefinition,
  createWorkflow,
  deleteWorkflow,
  getCatalog,
  getWorkflow,
  listRuns,
  listWorkflows,
  runWorkflow,
  type RunStatus,
  type WorkflowCatalog,
  type WorkflowDefinition,
  type WorkflowLastRun,
  type WorkflowRun,
  type WorkflowSummary,
} from '@/api/workflows';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { Modal } from '@/components/Modal';
import { DraftTag, RunStatusPill, formatDuration } from '@/components/workflow/RunStatusPill';
import { RunWorkflowDialog } from '@/components/workflow/RunWorkflowDialog';
import {
  Button,
  ChoiceChip,
  EmptyStateCard,
  FilterBar,
  Input,
  PageHeader,
  RowAction,
  RowActionIcon,
  SearchInput,
  Select,
  TableCard,
  TablePager,
  Textarea,
  Toast,
  type ToastTone,
} from '@/components/ui';
import { cn } from '@/lib/cn';
import { fullDateTime } from '@/lib/format';
import { usePermissions } from '@/store/me';
import { useI18n } from '@/i18n/locale';
import { emptyNode, layeredLayout } from '@/lib/workflowMeta';

type Tab = 'canvas' | 'runs';
type TemplateKey = 'blank' | 'knowledge' | 'approval';

// 运行状态筛选：与后端 RunStatus 一一对应，空值表示全部。
const RUN_STATUS_FILTERS: {
  value: '' | RunStatus;
  label: (tr: (zh: string, en: string) => string) => string;
}[] = [
  { value: '', label: (tr) => tr('全部', 'All') },
  { value: 'running', label: (tr) => tr('运行中', 'Running') },
  { value: 'waiting', label: (tr) => tr('等待人工', 'Waiting') },
  { value: 'succeeded', label: (tr) => tr('成功', 'Succeeded') },
  { value: 'failed', label: (tr) => tr('失败', 'Failed') },
];

export default function WorkflowsPage() {
  const { tr, locale } = useI18n();
  const zh = locale === 'zh-CN';
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const { canMutate } = usePermissions();

  const tab: Tab = params.get('tab') === 'runs' ? 'runs' : 'canvas';
  const workflowFilter = params.get('workflow') ?? '';

  const [items, setItems] = useState<WorkflowSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<{ tone: ToastTone; text: string } | null>(null);

  const [query, setQuery] = useState('');
  // 输入即请求会把每敲一个字都变成一次查询：300ms 停手后再查。
  const [activeQuery, setActiveQuery] = useState('');
  const [status, setStatus] = useState<'' | 'draft' | 'published'>('');
  const [sortDir, setSortDir] = useState<'desc' | 'asc'>('desc');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(20);

  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<WorkflowSummary | null>(null);
  const [running, setRunning] = useState<{ id: number; name: string; definition: WorkflowDefinition } | null>(null);
  const [loadingRunFor, setLoadingRunFor] = useState<number | null>(null);

  const flash = useCallback((tone: ToastTone, text: string) => {
    setToast({ tone, text });
    window.setTimeout(() => setToast(null), 4000);
  }, []);

  const fetchWorkflows = useCallback(
    async (silent = false) => {
      if (silent) setRefreshing(true);
      else setLoading(true);
      try {
        // 筛选与分页都在服务端做：total 是筛选后的总数，不是整库条数。
        const result = await listWorkflows({
          q: activeQuery.trim(),
          status,
          sort: sortDir,
          limit: pageSize,
          offset: page * pageSize,
        });
        setItems(result.items);
        setTotal(result.total);
        setError(null);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : (err as Error).message);
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [activeQuery, page, pageSize, sortDir, status],
  );

  useEffect(() => {
    void fetchWorkflows();
  }, [fetchWorkflows]);

  // 搜索框停手 300ms 才发请求，换关键字回到第一页；清空关键字立即生效。
  useEffect(() => {
    if (query === '') {
      setActiveQuery('');
      setPage(0);
      return;
    }
    const timer = window.setTimeout(() => {
      setActiveQuery(query);
      setPage(0);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  const hasFilter = activeQuery.trim() !== '' || status !== '';

  const openRun = useCallback(
    async (id: number) => {
      setLoadingRunFor(id);
      try {
        const detail = await getWorkflow(id);
        setRunning({ id: detail.id, name: detail.name, definition: detail.definition });
      } catch (err) {
        flash('err', err instanceof ApiError ? err.message : (err as Error).message);
      } finally {
        setLoadingRunFor(null);
      }
    },
    [flash],
  );

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tab === 'runs' ? tr('运行记录', 'Run history') : tr('工作流', 'Workflows')}
        subtitle={
          tab === 'runs'
            ? tr('每次运行的输入、输出与节点耗时都在这里', 'Inputs, outputs and per-node timings of every run')
            : tr(
                '用 ADK 图把 LLM、知识检索、MCP 工具与人工确认串起来；连线表达控制流，数据用 {{变量}} 传递',
                'Chain LLMs, retrieval, MCP tools and human gates on an ADK graph; edges carry control, {{vars}} carry data',
              )
        }
        actions={
          <>
            {tab === 'canvas' && (
              <RowAction
                icon={RefreshCw}
                iconClassName={cn(refreshing && 'animate-spin')}
                disabled={loading || refreshing}
                onClick={() => void fetchWorkflows(true)}
              >
                {tr('刷新', 'Refresh')}
              </RowAction>
            )}
            {tab === 'canvas' && canMutate && (
              <Button variant="primary" size="sm" className="h-7 px-3 text-[11.5px]" onClick={() => setCreating(true)}>
                <Plus size={12} aria-hidden />
                {tr('新建工作流', 'New workflow')}
              </Button>
            )}
          </>
        }
      />

      {tab === 'canvas' ? (
        <>
          <FilterBar className="mx-6 mt-3">
            <SearchInput
              value={query}
              onChange={setQuery}
              placeholder={tr('搜索名称 / 描述', 'Search name / description')}
              ariaLabel={tr('搜索工作流', 'Search workflows')}
              className="min-w-[16rem] flex-1"
            />
            <span className="pl-1 text-[11px] text-zinc-500">{tr('状态', 'Status')}</span>
            <ChoiceChip
              active={status === ''}
              label={tr('全部', 'All')}
              onClick={() => {
                setStatus('');
                setPage(0);
              }}
            />
            <ChoiceChip
              active={status === 'draft'}
              label={tr('草稿', 'Draft')}
              onClick={() => {
                setStatus('draft');
                setPage(0);
              }}
            />
            <ChoiceChip
              active={status === 'published'}
              label={tr('已发布', 'Published')}
              onClick={() => {
                setStatus('published');
                setPage(0);
              }}
            />
            <span className="ml-auto text-[11px] tabular-nums text-zinc-500">
              {tr(`共 ${total} 个工作流`, `${total} workflows`)}
            </span>
          </FilterBar>

          <div className="flex min-h-0 flex-1 flex-col px-6 py-4">
            {error !== null && <ErrorBar message={error} />}
            {loading ? (
              <div className="flex h-40 items-center justify-center text-[12px] text-zinc-500">{tr('加载中…', 'Loading…')}</div>
            ) : total === 0 ? (
              <EmptyStateCard
                icon={WorkflowIcon}
                title={hasFilter ? tr('没有匹配的工作流', 'No matching workflows') : tr('还没有工作流', 'No workflows yet')}
                hint={
                  hasFilter
                    ? tr('换个关键字，或清除状态筛选', 'Try another keyword or clear the status filter')
                    : tr('从模板开始：空白画布 / 知识问答 / 人工审批', 'Start from a template: blank / knowledge Q&A / approval')
                }
                action={
                  hasFilter ? (
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 px-3 text-[11.5px]"
                      onClick={() => {
                        setQuery('');
                        setStatus('');
                      }}
                    >
                      {tr('清除筛选', 'Clear filters')}
                    </Button>
                  ) : canMutate ? (
                    <Button variant="primary" size="sm" className="h-7 px-3 text-[11.5px]" onClick={() => setCreating(true)}>
                      <Plus size={12} aria-hidden />
                      {tr('新建工作流', 'New workflow')}
                    </Button>
                  ) : undefined
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
                    loading={loading}
                    onPageChange={setPage}
                    pageSizeOptions={[20, 50, 100]}
                    onPageSizeChange={(size) => {
                      setPage(0);
                      setPageSize(size);
                    }}
                  />
                }
              >
                <table className="og-action-table w-full text-sm">
                  <thead className="sticky top-0 z-10 border-b border-zinc-800/60 bg-zinc-900/95 text-[11px] text-zinc-500 backdrop-blur">
                    <tr className="whitespace-nowrap">
                      <th className="px-3 py-2 text-left font-medium">{tr('工作流', 'Workflow')}</th>
                      <th className="px-3 py-2 text-left font-medium">{tr('结构', 'Structure')}</th>
                      <th className="px-3 py-2 text-left font-medium">{tr('状态', 'Status')}</th>
                      <th className="px-3 py-2 text-left font-medium">{tr('运行状态', 'Run status')}</th>
                      <th className="px-3 py-2 text-left font-medium">{tr('版本', 'Version')}</th>
                      <th className="px-3 py-2 text-left font-medium" aria-sort={sortDir === 'desc' ? 'descending' : 'ascending'}>
                        <button
                          type="button"
                          onClick={() => {
                            setSortDir((current) => (current === 'desc' ? 'asc' : 'desc'));
                            setPage(0);
                          }}
                          className="inline-flex items-center gap-1 text-[11px] font-medium text-zinc-500 hover:text-zinc-300"
                        >
                          {tr('更新时间', 'Updated')}
                          {sortDir === 'desc' ? <ArrowDown size={11} aria-hidden /> : <ArrowUp size={11} aria-hidden />}
                        </button>
                      </th>
                      <th className="px-3 py-2 text-right font-medium">{tr('操作', 'Actions')}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/40">
                    {items.map((item) => (
                      <tr key={item.id} className="group transition-colors hover:bg-zinc-900/40">
                        <td className="px-3 py-2">
                          <div className="min-w-0">
                            <button
                              type="button"
                              onClick={() => navigate(`/workflows/${item.id}`)}
                              className="block max-w-[32rem] truncate text-left text-[12.5px] font-medium text-zinc-200 hover:text-indigo-300"
                            >
                              {item.name}
                            </button>
                            <div className="mt-0.5 line-clamp-1 max-w-[38rem] text-[10.5px] text-zinc-500">
                              {item.description || tr('没有描述', 'No description')}
                            </div>
                            {item.warnings.length > 0 && (
                              <div className="mt-1 flex items-center gap-1 text-[10px] text-warn">
                                <span className="rounded bg-warn/10 px-1 leading-4">{tr(`${item.warnings.length} 条提示`, `${item.warnings.length} warning(s)`)}</span>
                              </div>
                            )}
                          </div>
                        </td>
                        <td className="whitespace-nowrap px-3 py-2">
                          <div className="flex items-center gap-1 text-[11px] text-zinc-400">
                            <span className="tabular-nums">{item.node_count}</span>
                            {tr('节点', 'nodes')}
                            <span className="text-zinc-700">/</span>
                            <span className="tabular-nums">{item.edge_count}</span>
                            {tr('连线', 'edges')}
                          </div>
                        </td>
                        <td className="whitespace-nowrap px-3 py-2">
                          <span
                            className={cn(
                              'inline-flex items-center gap-1 rounded-full border px-1.5 text-[10.5px] leading-4',
                              item.status === 'published'
                                ? 'border-ok/30 bg-ok/10 text-ok'
                                : 'border-zinc-700 bg-zinc-800/40 text-zinc-400',
                            )}
                          >
                            <span className={cn('h-1 w-1 rounded-full', item.status === 'published' ? 'bg-ok' : 'bg-zinc-500')} aria-hidden />
                            {item.status === 'published' ? tr('已发布', 'Published') : tr('草稿', 'Draft')}
                          </span>
                        </td>
                        <td className="whitespace-nowrap px-3 py-2">
                          <LastRunCell run={item.last_run} onOpen={(uid) => navigate(`/workflows/runs/${uid}`)} />
                        </td>
                        <td className="whitespace-nowrap px-3 py-2 font-mono text-[11px] text-zinc-500">v{item.version}</td>
                        <td className="whitespace-nowrap px-3 py-2 text-[11px] tabular-nums text-zinc-500">{fullDateTime(item.updated_at)}</td>
                        <td className="whitespace-nowrap px-3 py-2 text-right">
                          <div className="flex items-center justify-end gap-1.5">
                            <RowAction icon={Play} tone="positive" disabled={loadingRunFor === item.id} onClick={() => void openRun(item.id)}>
                              {tr('运行', 'Run')}
                            </RowAction>
                            <RowAction icon={Pencil} onClick={() => navigate(`/workflows/${item.id}`)}>
                              {tr('编排', 'Edit')}
                            </RowAction>
                            {/* 图标按钮在这里说不清点下去会发生什么，改成和「运行 / 编排」同款文字操作 */}
                            <RowAction
                              icon={History}
                              onClick={() => {
                                const draft = new URLSearchParams();
                                draft.set('tab', 'runs');
                                draft.set('workflow', String(item.id));
                                setParams(draft);
                              }}
                            >
                              {tr('历史', 'History')}
                            </RowAction>
                            {canMutate && (
                              <RowActionIcon icon={Trash2} label={tr('删除', 'Delete')} tone="danger" onClick={() => setDeleting(item)} />
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
        </>
      ) : (
        <RunsTab
          workflowFilter={workflowFilter}
          firstWorkflowId={items[0]?.id ?? null}
          onWorkflowFilterChange={(id) => {
            const draft = new URLSearchParams(params);
            if (id === null) draft.delete('workflow');
            else draft.set('workflow', String(id));
            setParams(draft, { replace: true });
          }}
          onClearFilter={() => {
            const draft = new URLSearchParams(params);
            draft.delete('workflow');
            setParams(draft, { replace: true });
          }}
          onOpenWorkflow={(id) => navigate(`/workflows/${id}`)}
          onRerun={(id) => void openRun(id)}
          onOpenRun={(uid) => navigate(`/workflows/runs/${uid}`)}
          error={error}
        />
      )}

      {creating && (
        <CreateWorkflowDialog
          onClose={() => setCreating(false)}
          onCreated={(created) => {
            setCreating(false);
            flash('ok', zh ? `已创建「${created.name}」` : `Created "${created.name}"`);
            navigate(`/workflows/${created.id}`);
          }}
        />
      )}

      {running !== null && (
        <RunWorkflowDialog
          workflowId={running.id}
          workflowName={running.name}
          definition={running.definition}
          onClose={() => setRunning(null)}
          onStarted={(values) => {
            // 列表页没有画布可点亮，直接发起一次普通运行，进详情页看结果。
            // 弹窗只负责收集输入，真正的执行请求必须在这里发出去。
            const target = running;
            if (target === null) return;
            setRunning(null);
            void (async () => {
              try {
                const run = await runWorkflow(target.id, values);
                navigate(`/workflows/runs/${run.run_uid}`);
              } catch (error) {
                flash('err', error instanceof ApiError ? error.message : (error as Error).message);
              }
            })();
          }}
        />
      )}

      <ConfirmDialog
        open={deleting !== null}
        title={tr('删除工作流', 'Delete workflow')}
        description={tr(
          `确定删除「${deleting?.name ?? ''}」？画布定义与历史运行记录会一并删除，无法恢复。`,
          `Delete "${deleting?.name ?? ''}"? Its definition and run history are removed permanently.`,
        )}
        confirmLabel={tr('删除', 'Delete')}
        onClose={() => setDeleting(null)}
        onConfirm={async () => {
          if (deleting === null) return;
          await deleteWorkflow(deleting.id);
          flash('ok', tr(`已删除「${deleting.name}」`, `Deleted "${deleting.name}"`));
          // 删掉本页最后一条时回到上一页，否则原地刷新
          if (items.length === 1 && page > 0) setPage(page - 1);
          else await fetchWorkflows(true);
        }}
      />

      {toast !== null && <Toast tone={toast.tone} text={toast.text} />}
    </main>
  );
}

// 运行状态单元格：最近一次运行的状态，点击进运行详情；从未运行时给「未运行」。
function LastRunCell({ run, onOpen }: { run: WorkflowLastRun | null; onOpen(uid: string): void }) {
  const { tr } = useI18n();
  if (run === null) {
    return <span className="text-[11px] text-zinc-600">{tr('未运行', 'Never run')}</span>;
  }
  return (
    <button type="button" onClick={() => onOpen(run.run_uid)} title={run.run_uid}>
      <RunStatusPill status={run.status} />
    </button>
  );
}

function ErrorBar({ message }: { message: string }) {
  const { tr } = useI18n();
  return (
    <div role="alert" className="mb-3 rounded-xl border border-red-500/40 bg-red-500/5 px-4 py-3 text-[12px] text-red-300">
      {tr('操作失败：', 'Operation failed: ')}
      {message}
    </div>
  );
}

function RunsTab({
  workflowFilter,
  firstWorkflowId,
  onWorkflowFilterChange,
  onClearFilter,
  onOpenWorkflow,
  onRerun,
  onOpenRun,
  error,
}: {
  workflowFilter: string;
  /** 运行记录为空时用它打开第一个工作流。 */
  firstWorkflowId: number | null;
  /** 切换工作流筛选：空值表示看全部工作流。 */
  onWorkflowFilterChange(id: number | null): void;
  onClearFilter(): void;
  onOpenWorkflow(id: number): void;
  onRerun(id: number): void;
  onOpenRun(uid: string): void;
  error: string | null;
}) {
  const { tr } = useI18n();
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(20);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<string | null>(null);
  // 关键字、状态、工作流都交给后端筛：筛完的 total 才是「共 N 次运行」的口径。
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<'' | RunStatus>('');
  // 工作流下拉的候选：不分页取一批名字，够用且不跟画布列表的分页联动。
  const [workflowOptions, setWorkflowOptions] = useState<{ value: string; label: string }[]>([]);

  const workflowId = workflowFilter === '' ? undefined : Number(workflowFilter);
  const hasFilter = query.trim() !== '' || status !== '' || workflowId !== undefined;

  useEffect(() => {
    void (async () => {
      try {
        const result = await listWorkflows({ limit: 200, sort: 'asc' });
        setWorkflowOptions(result.items.map((item) => ({ value: String(item.id), label: item.name })));
      } catch (err) {
        setFailure(err instanceof ApiError ? err.message : (err as Error).message);
      }
    })();
  }, []);

  const fetchRuns = useCallback(async () => {
    setLoading(true);
    try {
      const result = await listRuns({
        workflowId,
        status,
        query,
        limit: pageSize,
        offset: page * pageSize,
      });
      setRuns(result.items);
      setTotal(result.total);
      setFailure(null);
    } catch (err) {
      setFailure(err instanceof ApiError ? err.message : (err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, query, status, workflowId]);

  useEffect(() => {
    void fetchRuns();
  }, [fetchRuns]);

  return (
    <>
      <FilterBar className="mx-6 mt-3">
        <SearchInput
          value={query}
          onChange={(next) => {
            setQuery(next);
            setPage(0);
          }}
          placeholder={tr('搜索工作流名 / run_id / 触发人', 'Search workflow / run_id / triggered by')}
          ariaLabel={tr('搜索运行记录', 'Search runs')}
          className="min-w-[16rem] flex-1"
        />
        <Select
          value={workflowFilter}
          onValueChange={(next) => {
            onWorkflowFilterChange(next === '' ? null : Number(next));
            setPage(0);
          }}
          options={[{ value: '', label: tr('全部工作流', 'All workflows') }, ...workflowOptions]}
          aria-label={tr('按工作流筛选', 'Filter by workflow')}
          className="w-[13rem]"
        />
        <span className="pl-1 text-[11px] text-zinc-500">{tr('状态', 'Status')}</span>
        {RUN_STATUS_FILTERS.map((item) => (
          <ChoiceChip
            key={item.value}
            active={status === item.value}
            label={item.label(tr)}
            onClick={() => {
              setStatus(item.value);
              setPage(0);
            }}
          />
        ))}
        <span className="ml-auto text-[11px] tabular-nums text-zinc-500">{tr(`共 ${total} 次运行`, `${total} runs`)}</span>
      </FilterBar>

      <div className="flex min-h-0 flex-1 flex-col px-6 py-4">
        {error !== null && <ErrorBar message={error} />}
        {failure !== null && <ErrorBar message={failure} />}
        {loading ? (
          <div className="flex h-40 items-center justify-center text-[12px] text-zinc-500">{tr('加载中…', 'Loading…')}</div>
        ) : runs.length === 0 ? (
          <EmptyStateCard
            icon={History}
            title={hasFilter ? tr('没有匹配的运行记录', 'No matching runs') : tr('还没有运行记录', 'No runs yet')}
            hint={
              hasFilter
                ? tr('换个关键字、状态，或清除工作流筛选', 'Try another keyword or status, or clear the workflow filter')
                : tr('在编排页点「运行」，或从工作流列表直接发起一次运行', 'Hit Run in the editor, or start one from the workflow list')
            }
            action={
              hasFilter ? (
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 px-3 text-[11.5px]"
                  onClick={() => {
                    setQuery('');
                    setStatus('');
                    if (workflowId !== undefined) onClearFilter();
                  }}
                >
                  {tr('清除筛选', 'Clear filters')}
                </Button>
              ) : firstWorkflowId !== null ? (
                <Button variant="outline" size="sm" className="h-7 px-3 text-[11.5px]" onClick={() => onOpenWorkflow(firstWorkflowId)}>
                  {tr('打开一个工作流', 'Open a workflow')}
                </Button>
              ) : undefined
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
                shown={runs.length}
                loading={loading}
                onPageChange={setPage}
                pageSizeOptions={[20, 50, 100]}
                onPageSizeChange={(size) => {
                  setPage(0);
                  setPageSize(size);
                }}
              />
            }
          >
            <table className="og-action-table w-full text-sm">
              <thead className="sticky top-0 z-10 border-b border-zinc-800/60 bg-zinc-900/95 text-[11px] text-zinc-500 backdrop-blur">
                <tr className="whitespace-nowrap">
                  <th className="px-3 py-2 text-left font-medium">{tr('运行', 'Run')}</th>
                  <th className="px-3 py-2 text-left font-medium">{tr('版本', 'Version')}</th>
                  <th className="px-3 py-2 text-left font-medium">{tr('状态', 'Status')}</th>
                  <th className="px-3 py-2 text-left font-medium">{tr('触发人', 'Triggered by')}</th>
                  <th className="px-3 py-2 text-left font-medium">{tr('开始时间', 'Started')}</th>
                  <th className="px-3 py-2 text-left font-medium">{tr('耗时', 'Duration')}</th>
                  <th className="px-3 py-2 text-right font-medium">{tr('操作', 'Actions')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/40">
                {runs.map((run) => {
                  return (
                    <tr key={run.run_uid} className="transition-colors hover:bg-zinc-900/40">
                      <td className="px-3 py-2">
                        <button
                          type="button"
                          onClick={() => onOpenRun(run.run_uid)}
                          className="block max-w-[24rem] truncate text-left text-[12.5px] text-zinc-200 hover:text-indigo-300"
                        >
                          {run.workflow_name}
                        </button>
                        <div className="mt-0.5 flex items-center gap-1 font-mono text-[10px] text-zinc-600">
                          <span>{run.run_uid}</span>
                          {run.trigger === 'debug' && (
                            <span className="ml-1 rounded-sm bg-zinc-700/40 px-1 font-sans text-[10px] text-zinc-400">
                              {tr('节点试跑', 'Node test')}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="whitespace-nowrap px-3 py-2">
                        <span className="font-mono text-[11px] text-zinc-400">v{run.workflow_version}</span>
                        {run.workflow_status === 'draft' && <DraftTag className="ml-1.5" />}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2">
                        <RunStatusPill status={run.status} />
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-[11px] text-zinc-400">{run.triggered_by || '—'}</td>
                      <td className="whitespace-nowrap px-3 py-2 text-[11px] tabular-nums text-zinc-500">{fullDateTime(run.created_at)}</td>
                      <td className="whitespace-nowrap px-3 py-2 font-mono text-[11px] text-zinc-500">{formatDuration(run.duration_ms)}</td>
                      <td className="whitespace-nowrap px-3 py-2 text-right">
                        <div className="flex items-center justify-end gap-1.5">
                          <RowAction icon={Play} onClick={() => onRerun(run.workflow_id)}>
                            {tr('再次运行', 'Run again')}
                          </RowAction>
                          <RowAction icon={ArrowUpRight} onClick={() => onOpenRun(run.run_uid)}>
                            {tr('详情', 'Details')}
                          </RowAction>
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
    </>
  );
}

function CreateWorkflowDialog({ onClose, onCreated }: { onClose(): void; onCreated(created: { id: number; name: string }): void }) {
  const { tr } = useI18n();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [template, setTemplate] = useState<TemplateKey>('blank');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // 知识问答模板要带 LLM 节点，而后端要求 LLM 节点保存时就有模型；
  // 这里先取目录里的第一个模型，用户进编排页可以再换。
  const [catalog, setCatalog] = useState<WorkflowCatalog | null>(null);

  useEffect(() => {
    let alive = true;
    getCatalog()
      .then((result) => {
        if (alive) setCatalog(result);
      })
      .catch(() => {
        // 目录拿不到时按「没有模型」处理：模板退化成不含 LLM 的结构，创建仍可成功。
        if (alive) setCatalog(null);
      });
    return () => {
      alive = false;
    };
  }, []);

  const defaultModel = catalog?.models[0] ?? null;
  const knowledgeNeedsModel = template === 'knowledge' && defaultModel === null;

  const submit = async () => {
    if (name.trim() === '') {
      setErr(tr('请填写工作流名称', 'Name is required'));
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const created = await createWorkflow({
        name: name.trim(),
        description,
        definition: buildTemplate(template, defaultModel),
      });
      onCreated({ id: created.id, name: created.name });
    } catch (error) {
      setErr(error instanceof ApiError ? error.message : (error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={() => {
        if (!busy) onClose();
      }}
      title={tr('新建工作流', 'New workflow')}
      size="md"
      footer={
        <>
          <Button variant="outline" size="sm" className="h-7 px-3 text-[11.5px]" onClick={onClose} disabled={busy}>
            {tr('取消', 'Cancel')}
          </Button>
          <Button variant="primary" size="sm" className="h-7 px-3 text-[11.5px]" onClick={() => void submit()} disabled={busy}>
            {busy ? tr('创建中…', 'Creating…') : tr('创建并打开', 'Create & open')}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {err !== null && (
          <div role="alert" className="rounded-md border border-red-500/40 bg-red-500/5 px-3 py-2 text-[11.5px] text-red-300">
            {err}
          </div>
        )}
        <div className="space-y-1">
          <div className="text-[10.5px] font-medium text-zinc-500">
            {tr('名称', 'Name')}
            <span className="ml-1 text-danger">*</span>
          </div>
          <Input value={name} onChange={(event) => setName(event.target.value)} placeholder={tr('例如：工单巡检', 'e.g. Ticket triage')} className="h-8 text-[12px]" autoFocus />
        </div>
        <div className="space-y-1">
          <div className="text-[10.5px] font-medium text-zinc-500">{tr('描述', 'Description')}</div>
          <Textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={2} className="text-[11.5px]" />
        </div>
        <div className="space-y-1">
          <div className="text-[10.5px] font-medium text-zinc-500">{tr('起始模板', 'Template')}</div>
          <div className="grid grid-cols-3 gap-2">
            {TEMPLATES.map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => setTemplate(item.key)}
                aria-pressed={template === item.key}
                className={cn(
                  'rounded-lg border px-2.5 py-2 text-left transition-colors',
                  template === item.key
                    ? 'border-indigo-500/60 bg-indigo-500/[0.08]'
                    : 'border-zinc-800 bg-zinc-900/40 hover:border-zinc-700',
                )}
              >
                <div className="text-[11.5px] font-medium text-zinc-200">{tr(item.zh, item.en)}</div>
                <div className="mt-0.5 text-[10px] leading-4 text-zinc-500">{tr(item.hintZh, item.hintEn)}</div>
              </button>
            ))}
          </div>
        </div>
        <div className="rounded-md border border-zinc-800/60 bg-zinc-900/30 px-2.5 py-2 text-[10.5px] leading-4 text-zinc-500">
          {tr(
            '模板只预置结构，模型、检索范围等参数在编排页填写；保存时后端会做完整校验。',
            'Templates only preset structure; fill in models and retrieval scope in the editor. Saving validates everything.',
          )}
        </div>
        {template === 'knowledge' && defaultModel !== null && (
          <div className="text-[10.5px] leading-4 text-zinc-500">
            {tr(`LLM 节点先用「${defaultModel.title}」，可在编排页更换`, `The LLM node starts with "${defaultModel.title}"; change it in the editor`)}
          </div>
        )}
        {knowledgeNeedsModel && (
          <div className="rounded-md border border-warn/30 bg-warn/5 px-2.5 py-2 text-[10.5px] leading-4 text-warn">
            {tr(
              '还没有启用任何模型：模板只连「开始 → 检索 → 结束」，配置模型后再加 LLM 节点。',
              'No model is enabled: the template links Start → Retrieval → End; add an LLM node after configuring a model.',
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}

const TEMPLATES: { key: TemplateKey; zh: string; en: string; hintZh: string; hintEn: string }[] = [
  { key: 'blank', zh: '空白画布', en: 'Blank', hintZh: '开始 → 结束', hintEn: 'Start → End' },
  { key: 'knowledge', zh: '知识问答', en: 'Knowledge Q&A', hintZh: '检索 + LLM 作答', hintEn: 'Retrieval + LLM' },
  { key: 'approval', zh: '人工审批', en: 'Approval', hintZh: 'LLM 结论 + 确认', hintEn: 'LLM + human gate' },
];

/**
 * 新建时的起始画布：都是「能通过保存校验」的最小结构。
 * 空白画布只放开始与结束；知识问答 / 人工审批把常用链路先连好，进编排页只需要填参数。
 * 后端要求 LLM 节点保存时就有模型，所以模板带上目录里的第一个模型（没有模型时不放 LLM 节点）。
 */
function buildTemplate(
  template: TemplateKey,
  model: { provider: string; model: string } | null,
): WorkflowDefinition {
  const blank = blankDefinition();
  const start = blank.nodes[0];
  if (template === 'knowledge') {
    start.variables = [{ name: 'question', type: 'string', description: '要回答的问题', required: true, default: null }];
    const knowledge = emptyNode('knowledge', 'retrieve', { x: 0, y: 0 });
    knowledge.label = '检索资料';
    knowledge.query = '{{question}}';
    knowledge.limit = 5;
    knowledge.output_key = 'context';
    const end = emptyNode('end', 'end', { x: 0, y: 0 });
    end.output_key = 'answer';
    if (model === null) {
      // 没有可用模型：只连检索链路，等用户在编排页补 LLM 节点。
      return withLayout({
        nodes: [start, knowledge, end],
        edges: [
          { id: 'e_start_retrieve', source: 'start', target: 'retrieve', route: '' },
          { id: 'e_retrieve_end', source: 'retrieve', target: 'end', route: '' },
        ],
      });
    }
    const agent = emptyNode('agent', 'answer', { x: 0, y: 0 });
    agent.label = '作答';
    agent.provider = model.provider;
    agent.model = model.model;
    agent.instruction = '根据检索到的资料回答问题，引用编号标注来源。\n\n资料：{{context}}\n\n问题：{{question}}';
    agent.output_key = 'answer';
    return withLayout({
      nodes: [start, knowledge, agent, end],
      edges: [
        { id: 'e_start_retrieve', source: 'start', target: 'retrieve', route: '' },
        { id: 'e_retrieve_answer', source: 'retrieve', target: 'answer', route: '' },
        { id: 'e_answer_end', source: 'answer', target: 'end', route: '' },
      ],
    });
  }
  if (template === 'approval') {
    start.variables = [{ name: 'action', type: 'string', description: '要执行的动作', required: true, default: null }];
    const gate = emptyNode('approval', 'gate', { x: 0, y: 0 });
    gate.label = '人工确认';
    gate.prompt = '确认执行：{{action}}';
    const assign = emptyNode('assign', 'record', { x: 0, y: 0 });
    assign.label = '记录结论';
    assign.assignments = { decision: 'approved' };
    const end = emptyNode('end', 'end', { x: 0, y: 0 });
    end.output_key = 'decision';
    return withLayout({
      nodes: [start, gate, assign, end],
      edges: [
        { id: 'e_start_gate', source: 'start', target: 'gate', route: '' },
        { id: 'e_gate_record', source: 'gate', target: 'record', route: '' },
        { id: 'e_record_end', source: 'record', target: 'end', route: '' },
      ],
    });
  }
  const end = emptyNode('end', 'end', { x: 0, y: 0 });
  return withLayout({
    nodes: [start, end],
    edges: [{ id: 'e_start_end', source: 'start', target: 'end', route: '' }],
  });
}

/** 模板坐标统一走分层布局，和「整理布局」按钮的排布一致。 */
function withLayout(graph: WorkflowDefinition): WorkflowDefinition {
  const positions = layeredLayout(graph);
  return {
    ...graph,
    nodes: graph.nodes.map((node) => ({ ...node, x: positions[node.id]?.x ?? node.x, y: positions[node.id]?.y ?? node.y })),
  };
}
