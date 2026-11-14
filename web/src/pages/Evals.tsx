// 评测集列表：评测集是回归用例的容器，运行记录在详情页查看与对比。
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ExternalLink,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  TestTube2,
  Trash2,
} from 'lucide-react';
import {
  Button,
  Chip,
  EmptyStateCard,
  ErrorBanner,
  FilterBar,
  Input,
  Label,
  PageHeader,
  RowAction,
  RowActionIcon,
  SearchInput,
  SortableHead,
  StatusPill,
  TableCard,
  TableHead,
  TablePager,
  Textarea,
  Toast,
  type ToastTone,
  COL,
  tableLayout,
} from '@/components/ui';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { Modal } from '@/components/Modal';
import { Hint } from '@/components/ui/Tooltip';
import { errorText } from '@/lib/errors';
import { fullDateTime } from '@/lib/format';
import { RUN_STATUS_META } from '@/lib/evalStatus';
import { compareNullableTime } from '@/lib/sort';
import { useTimeout } from '@/lib/useTimeout';
import { useI18n } from '@/i18n/locale';
import { usePermissions } from '@/store/me';
import {
  createEvalDataset,
  deleteEvalDataset,
  listEvalDatasets,
  updateEvalDataset,
  type EvalDatasetSummary,
} from '@/api/evals';

const DEFAULT_PAGE_SIZE = 20;
const PAGE_SIZE_OPTIONS = [20, 50, 100];

export default function EvalsPage() {
  const { tr } = useI18n();
  const navigate = useNavigate();
  const { canMutate } = usePermissions();
  const [items, setItems] = useState<EvalDatasetSummary[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [editor, setEditor] = useState<null | { dataset: EvalDatasetSummary | null }>(
    null,
  );
  const [deleting, setDeleting] = useState<EvalDatasetSummary | null>(null);
  const [toast, setToast] = useState<{ tone: ToastTone; text: string } | null>(null);

  const flashTimer = useTimeout();
  const flash = useCallback(
    (tone: ToastTone, text: string) => {
      setToast({ tone, text });
      flashTimer(() => setToast(null), 4000);
    },
    [flashTimer],
  );

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const result = await listEvalDatasets();
      setItems(result.items);
      setErr(null);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    const rows = items ?? [];
    const needle = query.trim().toLowerCase();
    if (needle === '') return rows;
    return rows.filter((row) =>
      [row.name, row.description].some((value) =>
        value.toLowerCase().includes(needle),
      ),
    );
  }, [items, query]);
  const sorted = useMemo(
    () =>
      [...filtered].sort((a, b) =>
        compareNullableTime(a.updated_at, b.updated_at, sortDir),
      ),
    [filtered, sortDir],
  );
  const pageCount = Math.max(1, Math.ceil(sorted.length / pageSize));
  const safePage = Math.min(page, pageCount - 1);
  const paged = sorted.slice(safePage * pageSize, (safePage + 1) * pageSize);

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tr('评测', 'Evals')}
        subtitle={tr(
          '用真实助理会话跑回归用例；关键词、工具、时延与 token 全量留痕',
          'Run regression cases against real assistant sessions; keywords, tools, latency and tokens are recorded',
        )}
        actions={
          <>
            <RowAction icon={RefreshCw} onClick={() => void load()}>
              {tr('刷新', 'Refresh')}
            </RowAction>
            {canMutate && (
              <Button
                variant="primary"
                size="sm"
                onClick={() => setEditor({ dataset: null })}
              >
                <Plus size={12} />
                {tr('新建评测集', 'New dataset')}
              </Button>
            )}
          </>
        }
      />

      <FilterBar className="mx-6 mt-3">
        <SearchInput
          value={query}
          onChange={(value) => {
            setQuery(value);
            setPage(0);
          }}
          placeholder={tr('按名称 / 说明搜索…', 'Search name or description…')}
          ariaLabel={tr('搜索评测集', 'Search datasets')}
        />
        <span className="ml-auto text-[11px] tabular-nums text-zinc-500">
          {tr(`共 ${filtered.length} 个评测集`, `${filtered.length} datasets`)}
        </span>
      </FilterBar>

      <div className="flex min-h-0 flex-1 flex-col px-6 py-4">
        {err && <ErrorBanner className="mb-3">{err}</ErrorBanner>}
        {loading && items === null ? (
          <div className="flex flex-1 items-center justify-center text-[11px] text-zinc-500">
            <Loader2 size={12} className="mr-2 animate-spin" />
            {tr('加载中…', 'Loading…')}
          </div>
        ) : items !== null && items.length === 0 ? (
          <EmptyStateCard
            icon={TestTube2}
            title={tr('还没有评测集', 'No eval datasets yet')}
            hint={tr(
              '把黄金问题与断言收进评测集，改人设、模型或知识范围后先跑一遍回归。',
              'Collect golden questions and assertions; run a regression before changing personas, models or knowledge.',
            )}
            action={
              canMutate ? (
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => setEditor({ dataset: null })}
                >
                  <Plus size={12} />
                  {tr('新建评测集', 'New dataset')}
                </Button>
              ) : undefined
            }
          />
        ) : filtered.length === 0 ? (
          <EmptyStateCard
            icon={TestTube2}
            title={tr('没有匹配的评测集', 'No matching datasets')}
            action={
              <Button size="sm" onClick={() => setQuery('')}>
                {tr('清除筛选', 'Clear filters')}
              </Button>
            }
          />
        ) : (
          <TableCard
            className="min-h-0 flex-1"
            footer={
              <TablePager
                page={safePage}
                pageSize={pageSize}
                total={filtered.length}
                shown={paged.length}
                onPageChange={setPage}
                pageSizeOptions={PAGE_SIZE_OPTIONS}
                onPageSizeChange={(size) => {
                  setPage(0);
                  setPageSize(size);
                }}
              />
            }
          >
            <table {...tableLayout(58)}>
              <colgroup>
                <col className={COL.main} />
                <col className={COL.detail} />
                <col className={COL.tag} />
                <col className={COL.time} />
                <col className={COL.actionsIcon} />
              </colgroup>
              <TableHead>
                <tr className="whitespace-nowrap">
                  <th scope="col" className="px-3 py-2 text-left font-medium">
                    {tr('名称', 'Name')}
                  </th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">
                    {tr('最近运行', 'Last run')}
                  </th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">
                    {tr('用例', 'Cases')}
                  </th>
                  <SortableHead
                    label={tr('更新时间', 'Updated')}
                    active
                    order={sortDir}
                    onClick={() => {
                      setPage(0);
                      setSortDir((current) => (current === 'desc' ? 'asc' : 'desc'));
                    }}
                  />
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    {tr('操作', 'Actions')}
                  </th>
                </tr>
              </TableHead>
              <tbody className="divide-y divide-zinc-800/40">
                {paged.map((dataset) => {
                  const last = dataset.last_run;
                  const meta = last ? RUN_STATUS_META[last.status] : null;
                  return (
                    <tr
                      key={dataset.id}
                      className="group transition-colors hover:bg-zinc-900/40"
                    >
                      <td className="px-3 py-2">
                        <button
                          type="button"
                          onClick={() => navigate(`/evals/${dataset.id}`)}
                          className="block max-w-full truncate text-left text-[12.5px] font-medium text-zinc-100 transition-colors hover:text-indigo-300"
                          title={tr(`打开 ${dataset.name}`, `Open ${dataset.name}`)}
                        >
                          {dataset.name}
                        </button>
                        <span className="block truncate text-[10px] leading-4 text-zinc-500">
                          {dataset.description || tr('未填写说明', 'No description')}
                        </span>
                        {dataset.baseline_run_uid !== '' && (
                          <div className="mt-1">
                            <Chip dense tone="info">
                              {tr(
                                `门禁基线 · 回归≤${dataset.gate_max_regressions} · 通过率≥${Math.round(dataset.gate_min_pass_rate * 100)}%`,
                                `Gate · ≤${dataset.gate_max_regressions} regressions · ≥${Math.round(dataset.gate_min_pass_rate * 100)}% pass`,
                              )}
                            </Chip>
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        {last && meta ? (
                          <div className="space-y-0.5">
                            <div className="flex items-center gap-1.5">
                              <StatusPill tone={meta.tone} label={tr(meta.zh, meta.en)} />
                              <span className="text-[11px] tabular-nums text-zinc-300">
                                {last.passed_cases}/{last.total_cases}
                              </span>
                            </div>
                            <span className="block text-[10px] text-zinc-500">
                              {last.started_at ? fullDateTime(last.started_at) : '—'}
                            </span>
                          </div>
                        ) : (
                          <span className="text-[11px] text-zinc-500">
                            {tr('未运行', 'Not run')}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-[11px] tabular-nums text-zinc-300">
                        {tr(
                          `${dataset.case_count} 条 · ${dataset.enabled_case_count} 启用`,
                          `${dataset.case_count} · ${dataset.enabled_case_count} enabled`,
                        )}
                      </td>
                      <td className="px-3 py-2 text-[11px] text-zinc-500">
                        {dataset.updated_at ? fullDateTime(dataset.updated_at) : '—'}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <div className="flex items-center justify-end gap-0.5 opacity-60 transition-opacity group-hover:opacity-100">
                          <Hint content={tr('打开', 'Open')}>
                            <RowActionIcon
                              icon={ExternalLink}
                              label={tr('打开', 'Open')}
                              onClick={() => navigate(`/evals/${dataset.id}`)}
                            />
                          </Hint>
                          {canMutate && (
                            <>
                              <Hint content={tr('编辑', 'Edit')}>
                                <RowActionIcon
                                  icon={Pencil}
                                  label={tr('编辑', 'Edit')}
                                  onClick={() => setEditor({ dataset })}
                                />
                              </Hint>
                              <Hint content={tr('删除', 'Delete')}>
                                <RowActionIcon
                                  icon={Trash2}
                                  label={tr('删除', 'Delete')}
                                  tone="danger"
                                  onClick={() => setDeleting(dataset)}
                                />
                              </Hint>
                            </>
                          )}
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

      {editor !== null && (
        <DatasetEditor
          dataset={editor.dataset}
          onClose={() => setEditor(null)}
          onSaved={(dataset) => {
            setEditor(null);
            void load(true);
            flash(
              'ok',
              editor.dataset === null
                ? tr('评测集已创建', 'Dataset created')
                : tr('评测集已更新', 'Dataset updated'),
            );
            if (editor.dataset === null) {
              navigate(`/evals/${dataset.id}`);
            }
          }}
        />
      )}

      {deleting !== null && (
        <ConfirmDialog
          open
          title={tr('删除评测集', 'Delete dataset')}
          description={tr(
            `删除「${deleting.name}」会同时删除用例与全部运行记录，且不可恢复。`,
            `Deleting "${deleting.name}" also removes its cases and run history.`,
          )}
          confirmLabel={tr('删除', 'Delete')}
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            await deleteEvalDataset(deleting.id);
            await load(true);
            flash('ok', tr('评测集已删除', 'Dataset deleted'));
          }}
        />
      )}

      {toast && <Toast tone={toast.tone} text={toast.text} />}
    </main>
  );
}

function DatasetEditor({
  dataset,
  onClose,
  onSaved,
}: {
  dataset: EvalDatasetSummary | null;
  onClose(): void;
  onSaved(dataset: { id: number }): void;
}) {
  const { tr } = useI18n();
  const editing = dataset !== null;
  const [name, setName] = useState(dataset?.name ?? '');
  const [description, setDescription] = useState(dataset?.description ?? '');
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = async () => {
    setSaving(true);
    setErr(null);
    try {
      if (dataset !== null) {
        const updated = await updateEvalDataset(dataset.id, {
          name: name.trim(),
          description: description.trim(),
        });
        onSaved(updated);
      } else {
        const created = await createEvalDataset({
          name: name.trim(),
          description: description.trim(),
        });
        onSaved(created);
      }
    } catch (e) {
      setErr(errorText(e));
      setSaving(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={editing ? tr('编辑评测集', 'Edit dataset') : tr('新建评测集', 'New dataset')}
      size="md"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose}>
            {tr('取消', 'Cancel')}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => void save()}
            disabled={saving || name.trim() === ''}
          >
            {saving
              ? tr('保存中…', 'Saving…')
              : editing
                ? tr('保存修改', 'Save changes')
                : tr('创建', 'Create')}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {err && <ErrorBanner>{err}</ErrorBanner>}
        <div className="space-y-1.5">
          <Label htmlFor="eval-dataset-name">{tr('名称', 'Name')}</Label>
          <Input
            id="eval-dataset-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={tr('例如 客服黄金问答', 'e.g. support golden questions')}
            maxLength={128}
            className="w-full"
            autoFocus
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="eval-dataset-desc">{tr('说明', 'Description')}</Label>
          <Textarea
            id="eval-dataset-desc"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder={tr(
              '这组用例覆盖什么场景、改动哪些配置后需要重跑',
              'What this suite covers and when it must be re-run',
            )}
            rows={3}
          />
        </div>
      </div>
    </Modal>
  );
}
