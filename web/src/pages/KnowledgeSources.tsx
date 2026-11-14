// 外部知识源：URL / Git 接入、后台增量同步与文档归属。
//
// 同步在服务端后台执行，页面只负责触发与观察状态：有来源处于 syncing 时
// 每 2 秒静默刷新一次，直到整张表稳定；同步出的文档在知识库页只读。
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  FileText,
  FolderGit2,
  Globe,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
} from 'lucide-react';
import {
  Button,
  Chip,
  COL,
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
  Select,
  StatusPill,
  TableCard,
  TableHead,
  TablePager,
  Toast,
  type StatusTone,
  type ToastTone,
  tableLayout,
} from '@/components/ui';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { Modal } from '@/components/Modal';
import { errorText } from '@/lib/errors';
import { fullDateTime } from '@/lib/format';
import { useI18n } from '@/i18n/locale';
import { usePermissions } from '@/store/me';
import { useTimeout } from '@/lib/useTimeout';
import { listDocs, localizedDocTitle, localizedPath, type KnowledgeDoc } from '@/api/knowledge';
import {
  createKnowledgeSource,
  deleteKnowledgeSource,
  listKnowledgeSources,
  syncKnowledgeSource,
  updateKnowledgeSource,
  type KnowledgeSource,
  type KnowledgeSourceKind,
  type KnowledgeSourceSyncStatus,
} from '@/api/knowledgeSources';

const DEFAULT_PAGE_SIZE = 20;
const PAGE_SIZE_OPTIONS = [20, 50, 100];

type SyncMeta = { tone: StatusTone; zh: string; en: string };

// 同步状态只有四种，颜色与文案全站统一：进行中脉冲、失败常驻展示原因。
const SYNC_META: Record<KnowledgeSourceSyncStatus, SyncMeta> = {
  idle: { tone: 'neutral', zh: '未同步', en: 'Not synced' },
  syncing: { tone: 'info', zh: '同步中', en: 'Syncing' },
  ok: { tone: 'ok', zh: '已同步', en: 'Synced' },
  error: { tone: 'danger', zh: '同步失败', en: 'Failed' },
};

function formatDuration(ms: number | null): string {
  if (ms === null) return '';
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

export default function KnowledgeSourcesPage() {
  const { tr } = useI18n();
  const { canMutate } = usePermissions();
  const [items, setItems] = useState<KnowledgeSource[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [kindFilter, setKindFilter] = useState<KnowledgeSourceKind | ''>('');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [editor, setEditor] = useState<{ source: KnowledgeSource | null } | null>(null);
  const [deleting, setDeleting] = useState<KnowledgeSource | null>(null);
  const [docsOf, setDocsOf] = useState<KnowledgeSource | null>(null);
  const [toast, setToast] = useState<{ tone: ToastTone; text: string } | null>(null);
  // 正在触发同步的来源：请求返回前禁用按钮，避免连点重复派发
  const [syncingId, setSyncingId] = useState<string | null>(null);

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
      const result = await listKnowledgeSources();
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

  const anySyncing = (items ?? []).some((row) => row.sync_status === 'syncing');
  useEffect(() => {
    if (!anySyncing) return;
    const timer = window.setInterval(() => {
      void load(true);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [anySyncing, load]);

  const filtered = useMemo(() => {
    const rows = items ?? [];
    const needle = query.trim().toLowerCase();
    return rows.filter((row) => {
      if (kindFilter !== '' && row.kind !== kindFilter) return false;
      if (needle === '') return true;
      return [row.name, row.location, row.knowledge_path].some((value) =>
        value.toLowerCase().includes(needle),
      );
    });
  }, [items, query, kindFilter]);
  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, pageCount - 1);
  const paged = filtered.slice(safePage * pageSize, (safePage + 1) * pageSize);

  const triggerSync = useCallback(
    async (source: KnowledgeSource) => {
      setSyncingId(source.id);
      try {
        const row = await syncKnowledgeSource(source.id);
        setItems((prev) => prev?.map((item) => (item.id === row.id ? row : item)) ?? prev);
        flash('ok', tr(`已开始同步「${source.name}」`, `Sync started for "${source.name}"`));
      } catch (e) {
        flash('err', errorText(e));
      } finally {
        setSyncingId(null);
      }
    },
    [flash, tr],
  );

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tr('外部知识源', 'External sources')}
        subtitle={tr(
          '把 URL 页面与 Git 仓库里的文档增量同步进知识库；同步文档只读，随来源刷新',
          'Incrementally sync docs from URL pages and Git repos; synced docs are read-only and refreshed from their source',
        )}
        actions={
          <>
            <RowAction icon={RefreshCw} disabled={loading} onClick={() => void load()}>
              {tr('刷新', 'Refresh')}
            </RowAction>
            {canMutate && (
              <Button
                variant="primary"
                size="sm"
                onClick={() => setEditor({ source: null })}
              >
                <Plus size={12} />
                {tr('新建来源', 'New source')}
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
          placeholder={tr('按名称 / 地址搜索…', 'Search name or location…')}
          ariaLabel={tr('搜索外部知识源', 'Search external sources')}
        />
        <FilterPill
          label={tr('类型', 'Type')}
          active={kindFilter !== ''}
          onClear={() => {
            setKindFilter('');
            setPage(0);
          }}
          width="w-[7.5rem]"
        >
          <Select
            aria-label={tr('类型', 'Type')}
            value={kindFilter}
            onValueChange={(value) => {
              setKindFilter(value as KnowledgeSourceKind | '');
              setPage(0);
            }}
            options={[
              { value: '', label: tr('全部类型', 'All types') },
              { value: 'url', label: 'URL' },
              { value: 'git', label: 'Git' },
            ]}
            variant="ghost"
            className="h-7 min-w-0 border-0 bg-transparent px-0 text-[12px] shadow-none"
          />
        </FilterPill>
        <span className="ml-auto shrink-0 pr-1 text-[11px] tabular-nums text-zinc-500">
          {tr(`共 ${filtered.length} 个来源`, `${filtered.length} source(s)`)}
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
            icon={FolderGit2}
            title={tr('还没有外部知识源', 'No external sources yet')}
            hint={tr(
              '接入团队 Wiki 页面或 Git 仓库，按内容哈希增量同步；重复同步不会重复嵌入未变化的文档。',
              'Connect a team wiki page or Git repo; sync is incremental by content hash, so unchanged docs are not re-embedded.',
            )}
            action={
              canMutate ? (
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => setEditor({ source: null })}
                >
                  <Plus size={12} />
                  {tr('新建来源', 'New source')}
                </Button>
              ) : undefined
            }
          />
        ) : filtered.length === 0 ? (
          <EmptyStateCard
            icon={FolderGit2}
            title={tr('没有匹配的来源', 'No matching sources')}
            action={
              <Button
                size="sm"
                onClick={() => {
                  setQuery('');
                  setKindFilter('');
                }}
              >
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
            <table {...tableLayout(62)}>
              <colgroup>
                <col className={COL.main} />
                <col className={COL.detail} />
                <col className={COL.tag} />
                <col className={COL.detail} />
                <col className={COL.actionsIconWide} />
              </colgroup>
              <TableHead>
                <tr className="whitespace-nowrap">
                  <th scope="col" className="px-3 py-2 text-left font-medium">
                    {tr('来源', 'Source')}
                  </th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">
                    {tr('知识路径', 'Knowledge path')}
                  </th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">
                    {tr('文档', 'Docs')}
                  </th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">
                    {tr('同步状态', 'Sync status')}
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    {tr('操作', 'Actions')}
                  </th>
                </tr>
              </TableHead>
              <tbody className="divide-y divide-zinc-800/40">
                {paged.map((source) => {
                  const sync = SYNC_META[source.sync_status];
                  const Icon = source.kind === 'git' ? FolderGit2 : Globe;
                  const busy = source.sync_status === 'syncing';
                  return (
                    <tr key={source.id} className="group">
                      <td className="px-3 py-2">
                        <div className="flex min-w-0 items-center gap-2">
                          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded border border-zinc-800/60 bg-zinc-900/50 text-zinc-500">
                            <Icon size={11} aria-hidden />
                          </span>
                          <span className="truncate text-[12.5px] font-medium text-zinc-100">
                            {source.name}
                          </span>
                          <Chip dense className="shrink-0 font-sans">
                            {source.kind === 'git' ? 'Git' : 'URL'}
                          </Chip>
                          {!source.enabled && (
                            <StatusPill tone="neutral" label={tr('已停用', 'Disabled')} />
                          )}
                        </div>
                        <div
                          className="mt-0.5 truncate pl-7 font-sans text-[10.5px] text-zinc-500"
                          title={source.location}
                        >
                          {source.location}
                        </div>
                      </td>
                      <td className="px-3 py-2">
                        <div className="truncate text-[11px] text-zinc-400" title={source.knowledge_path}>
                          {source.knowledge_path || '—'}
                        </div>
                        {source.kind === 'git' && (source.branch || source.path_prefix) && (
                          <div className="mt-0.5 truncate text-[10px] text-zinc-600">
                            {[
                              source.branch ? tr(`分支 ${source.branch}`, `branch ${source.branch}`) : '',
                              source.path_prefix ? source.path_prefix : '',
                            ]
                              .filter(Boolean)
                              .join(' · ')}
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <span className="whitespace-nowrap text-[11px] tabular-nums text-zinc-400">
                          {tr(`${source.document_count} 篇`, `${source.document_count} docs`)}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <StatusPill
                          tone={sync.tone}
                          label={tr(sync.zh, sync.en)}
                          pulse={source.sync_status === 'syncing'}
                        />
                        {source.sync_status === 'error' && source.last_error && (
                          <div
                            className="mt-0.5 line-clamp-2 text-[10px] leading-4 text-danger/90"
                            title={source.last_error}
                          >
                            {source.last_error}
                          </div>
                        )}
                        <div
                          className="mt-0.5 whitespace-nowrap text-[10px] tabular-nums text-zinc-600"
                          title={
                            source.last_synced_at
                              ? tr(
                                  `最近同步 ${fullDateTime(source.last_synced_at)}`,
                                  `Last sync ${fullDateTime(source.last_synced_at)}`,
                                )
                              : tr('尚未同步', 'Never synced')
                          }
                        >
                          {source.last_synced_at ? fullDateTime(source.last_synced_at) : '—'}
                          {source.last_duration_ms !== null && (
                            <> · {formatDuration(source.last_duration_ms)}</>
                          )}
                        </div>
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center justify-end gap-0.5 opacity-60 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                          <RowActionIcon
                            icon={RefreshCw}
                            label={
                              busy
                                ? tr(`「${source.name}」同步中`, `"${source.name}" is syncing`)
                                : tr(`同步 ${source.name}`, `Sync ${source.name}`)
                            }
                            className={busy || syncingId === source.id ? '[&_svg]:animate-spin' : undefined}
                            disabled={!canMutate || busy || syncingId !== null}
                            onClick={() => void triggerSync(source)}
                          />
                          <RowActionIcon
                            icon={FileText}
                            label={tr(`查看 ${source.name} 的文档`, `View docs of ${source.name}`)}
                            onClick={() => setDocsOf(source)}
                          />
                          {canMutate && (
                            <>
                              <RowActionIcon
                                icon={Pencil}
                                label={tr(`编辑 ${source.name}`, `Edit ${source.name}`)}
                                onClick={() => setEditor({ source })}
                              />
                              <RowActionIcon
                                icon={Trash2}
                                label={tr(`删除 ${source.name}`, `Delete ${source.name}`)}
                                tone="danger"
                                onClick={() => setDeleting(source)}
                              />
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

      {editor && (
        <SourceEditor
          source={editor.source}
          onClose={() => setEditor(null)}
          onSaved={(tone, text) => {
            setEditor(null);
            flash(tone, text);
            void load(true);
          }}
        />
      )}

      {docsOf && <SourceDocsModal source={docsOf} onClose={() => setDocsOf(null)} />}

      {deleting && (
        <ConfirmDialog
          open
          title={tr(`删除「${deleting.name}」`, `Delete "${deleting.name}"`)}
          description={
            <p>
              {tr(
                `来源下已同步的 ${deleting.document_count} 篇文档会连同切块一起删除，无法恢复；来源配置本身也会移除。`,
                `All ${deleting.document_count} synced docs and their chunks will be removed permanently, along with the source configuration.`,
              )}
            </p>
          }
          onConfirm={async () => {
            try {
              await deleteKnowledgeSource(deleting.id);
              setDeleting(null);
              flash('ok', tr('外部源已删除', 'Source deleted'));
              await load(true);
            } catch (e) {
              // 抛回 ConfirmDialog，让失败原因显示在弹窗内并保持可重试
              throw e;
            }
          }}
          onClose={() => setDeleting(null)}
        />
      )}

      {toast && <Toast tone={toast.tone} text={toast.text} />}
    </main>
  );
}

function SourceEditor({
  source,
  onClose,
  onSaved,
}: {
  source: KnowledgeSource | null;
  onClose(): void;
  onSaved(tone: ToastTone, text: string): void;
}) {
  const { tr } = useI18n();
  const editing = source !== null;
  const [name, setName] = useState(source?.name ?? '');
  const [kind, setKind] = useState<KnowledgeSourceKind>(source?.kind ?? 'url');
  const [location, setLocation] = useState(source?.location ?? '');
  const [branch, setBranch] = useState(source?.branch ?? '');
  const [pathPrefix, setPathPrefix] = useState(source?.path_prefix ?? '');
  const [knowledgePath, setKnowledgePath] = useState(source?.knowledge_path ?? '');
  const [enabled, setEnabled] = useState(source?.enabled ?? true);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = async () => {
    setSaving(true);
    setErr(null);
    try {
      const payload = {
        name: name.trim(),
        kind,
        location: location.trim(),
        branch: kind === 'git' ? branch.trim() : '',
        path_prefix: kind === 'git' ? pathPrefix.trim() : '',
        knowledge_path: knowledgePath.trim(),
        enabled,
      };
      if (editing) {
        await updateKnowledgeSource(source.id, payload);
        onSaved('ok', tr('来源已保存', 'Source saved'));
      } else {
        const created = await createKnowledgeSource(payload);
        // 新建后立即同步一次：空来源停在列表里没有信息量，抓取结果才是配置是否正确的反馈
        try {
          await syncKnowledgeSource(created.id);
          onSaved('ok', tr('来源已创建，正在后台同步', 'Source created; syncing in background'));
        } catch (syncErr) {
          onSaved(
            'err',
            tr(
              `来源已创建，但同步未能启动：${errorText(syncErr)}`,
              `Source created, but sync could not start: ${errorText(syncErr)}`,
            ),
          );
        }
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
      title={editing ? tr('编辑外部来源', 'Edit external source') : tr('新建外部来源', 'New external source')}
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
            disabled={saving || name.trim() === '' || location.trim() === ''}
          >
            {saving
              ? tr('保存中…', 'Saving…')
              : editing
                ? tr('保存修改', 'Save changes')
                : tr('创建并同步', 'Create & sync')}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {err && <ErrorBanner>{err}</ErrorBanner>}
        <div className="space-y-1.5">
          <Label htmlFor="knowledge-source-name">{tr('名称', 'Name')}</Label>
          <Input
            id="knowledge-source-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={tr('例如 运维 Wiki', 'e.g. Ops wiki')}
            maxLength={128}
            className="w-full"
            autoFocus
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="knowledge-source-kind">{tr('类型', 'Type')}</Label>
          <Select
            id="knowledge-source-kind"
            value={kind}
            onValueChange={(value) => setKind(value as KnowledgeSourceKind)}
            options={[
              { value: 'url', label: tr('URL 页面', 'URL page') },
              { value: 'git', label: tr('Git 仓库', 'Git repository') },
            ]}
            disabled={editing}
            className="w-full"
          />
          {editing && (
            <p className="text-[10.5px] leading-4 text-zinc-600">
              {tr('类型创建后不可修改；需要换类型请新建来源。', 'Type cannot be changed; create a new source instead.')}
            </p>
          )}
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="knowledge-source-location">
            {kind === 'git' ? tr('仓库地址', 'Repository URL') : tr('页面地址', 'Page URL')}
          </Label>
          <Input
            id="knowledge-source-location"
            value={location}
            onChange={(e) => setLocation(e.target.value)}
            placeholder={
              kind === 'git'
                ? 'https://git.example.com/ops/runbooks.git'
                : 'https://wiki.internal/runbook/nginx'
            }
            className="w-full font-sans text-[11.5px]"
          />
        </div>
        {kind === 'git' && (
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="knowledge-source-branch">{tr('分支（可选）', 'Branch (optional)')}</Label>
              <Input
                id="knowledge-source-branch"
                value={branch}
                onChange={(e) => setBranch(e.target.value)}
                placeholder="main"
                className="w-full font-sans text-[11.5px]"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="knowledge-source-prefix">{tr('目录前缀（可选）', 'Path prefix (optional)')}</Label>
              <Input
                id="knowledge-source-prefix"
                value={pathPrefix}
                onChange={(e) => setPathPrefix(e.target.value)}
                placeholder="docs"
                className="w-full font-sans text-[11.5px]"
              />
            </div>
          </div>
        )}
        <div className="space-y-1.5">
          <Label htmlFor="knowledge-source-path">{tr('知识库路径（可选）', 'Knowledge path (optional)')}</Label>
          <Input
            id="knowledge-source-path"
            value={knowledgePath}
            onChange={(e) => setKnowledgePath(e.target.value)}
            placeholder={tr('留空则使用「外部源/<名称>」', 'Defaults to "External/<name>"')}
            className="w-full"
          />
          <p className="text-[10.5px] leading-4 text-zinc-600">
            {tr(
              '同步出的文档归到这个目录，便于在知识库里按目录隔离检索范围。',
              'Synced docs are filed under this folder so retrieval scopes can target them.',
            )}
          </p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="knowledge-source-enabled">{tr('同步文档参与检索', 'Include synced docs in retrieval')}</Label>
          <Select
            id="knowledge-source-enabled"
            value={enabled ? 'on' : 'off'}
            onValueChange={(value) => setEnabled(value === 'on')}
            options={[
              { value: 'on', label: tr('启用', 'Enabled') },
              { value: 'off', label: tr('停用', 'Disabled') },
            ]}
            className="w-full"
          />
          <p className="text-[10.5px] leading-4 text-zinc-600">
            {tr(
              '切换后立即作用于该来源下的全部已同步文档；关闭不会停止手动同步。',
              'Applies immediately to all docs under this source; disabling does not block manual sync.',
            )}
          </p>
        </div>
      </div>
    </Modal>
  );
}

function SourceDocsModal({
  source,
  onClose,
}: {
  source: KnowledgeSource;
  onClose(): void;
}) {
  const { tr } = useI18n();
  const [items, setItems] = useState<KnowledgeDoc[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const result = await listDocs({ external_source_id: source.id, limit: 100 });
        if (!cancelled) setItems(result.items);
      } catch (e) {
        if (!cancelled) setErr(errorText(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [source.id]);

  return (
    <Modal
      open
      onClose={onClose}
      title={tr(`「${source.name}」的文档`, `Docs of "${source.name}"`)}
      size="lg"
      footer={
        <Button variant="outline" size="sm" onClick={onClose}>
          {tr('关闭', 'Close')}
        </Button>
      }
    >
      <div className="space-y-3">
        {err ? (
          <ErrorBanner>{err}</ErrorBanner>
        ) : items === null ? (
          <div className="flex items-center justify-center py-8 text-[11px] text-zinc-500">
            <Loader2 size={12} className="mr-2 animate-spin" />
            {tr('加载中…', 'Loading…')}
          </div>
        ) : items.length === 0 ? (
          <div className="rounded-lg border border-zinc-800/60 bg-zinc-950/40 px-4 py-8 text-center">
            <p className="text-[11.5px] text-zinc-400">{tr('还没有同步出文档', 'No synced docs yet')}</p>
            <p className="mt-1 text-[10.5px] text-zinc-600">
              {tr('触发一次同步后回到这里查看结果。', 'Trigger a sync, then come back to see the results.')}
            </p>
          </div>
        ) : (
          <div className="max-h-[24rem] overflow-y-auto rounded-lg border border-zinc-800/60">
            <table className="w-full text-left">
              <thead className="sticky top-0 bg-zinc-950/95 text-[10.5px] text-zinc-500">
                <tr className="border-b border-zinc-800/60">
                  <th scope="col" className="px-3 py-2 font-medium">{tr('文档', 'Document')}</th>
                  <th scope="col" className="px-3 py-2 font-medium">{tr('目录', 'Folder')}</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">{tr('切块', 'Chunks')}</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">{tr('参与检索', 'Retrieval')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/40">
                {items.map((doc) => (
                  <tr key={doc.id}>
                    <td className="max-w-0 px-3 py-2">
                      <div className="truncate text-[11.5px] text-zinc-200" title={localizedDocTitle(doc)}>
                        {localizedDocTitle(doc)}
                      </div>
                      {doc.external_key && (
                        <div className="mt-0.5 truncate font-sans text-[10px] text-zinc-600" title={doc.external_key}>
                          {doc.external_key}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2 text-[10.5px] text-zinc-500">
                      {doc.path ? localizedPath(doc.path) : '—'}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-right text-[10.5px] tabular-nums text-zinc-500">
                      {doc.chunk_count === undefined ? '—' : doc.chunk_count}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <StatusPill
                        tone={doc.enabled ? 'ok' : 'neutral'}
                        label={doc.enabled ? tr('参与', 'On') : tr('停用', 'Off')}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Modal>
  );
}
