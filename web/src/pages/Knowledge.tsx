// Knowledge page — 知识库工作台。
//
// 骨架（与 docs/UI设计规范.md 一致）：
//   PageHeader   标题 / 计数 / 刷新
//   FilterBar    统一搜索框（回车检索）+ 来源筛选胶囊 + 检索词胶囊
//   aside        来源分组 + 目录树（组织 / 内置两棵树常驻）
//   TableCard    文档表（文档 / 更新时间 / 参与检索 / 操作）+ 分页，检索后换成命中表
//
// 目录树由文档自身的 path 分段临时构建，不再依赖 /knowledge/paths 聚合接口；
// 每个来源组各自一棵树，切换分组即切换右侧列表。
// 建/改/上传文档时后端已同步重切块并重嵌入，因此页面不再提供「重建索引」手动入口。
import { useAssistantPage } from '@/lib/assistantPage';
import { usePermissions } from '@/store/me';
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from 'react';
import {
  ArrowDown,
  ArrowUp,
  BookOpen,
  ChevronRight,
  Copy,
  Eye,
  FileText,
  Folder,
  FolderOpen,
  GripVertical,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  SearchX,
  Trash2,
  Upload,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { cn } from '@/lib/cn';
import { splitFrontmatter } from '@/lib/frontmatter';
import type { IconType } from '@/lib/icon';
import { Modal } from '@/components/Modal';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import {
  Button,
  Chip,
  EmptyStateCard,
  FilterBar,
  FilterChip,
  FilterPill,
  Input,
  PageHeader,
  RowAction,
  RowActionIcon,
  SearchInput,
  Select,
  Switch,
  TableCard,
  TablePager,
  Textarea,
  Toast,
} from '@/components/ui';
import { Hint } from '@/components/ui/Tooltip';
import { fullDateTime } from '@/lib/format';
import {
  createDoc,
  deleteDoc,
  listDocs,
  localizedDocTitle,
  localizedPath,
  localizedPathSegment,
  moveDoc,
  searchKnowledge,
  setDocEnabled,
  updateDoc,
  uploadDoc,
  type KnowledgeDoc,
  type DocSource,
  type PathRow,
  type SearchHit,
} from '@/api/knowledge';
import { ApiError } from '@/api/client';
import { useI18n } from '@/i18n/locale';

// 拖拽移动组织文档时的 dataTransfer MIME；值为文档 id。
const DOC_DND_MIME = 'application/x-ongrid-doc-id';

// 表头统一规格：11px、sticky、80% 表面。
const TABLE_HEAD =
  'sticky top-0 z-10 border-b border-zinc-800/60 bg-zinc-900/95 text-[11px] text-zinc-500 backdrop-blur';

type SourceGroup = 'org' | 'builtin';

type TreeNode = {
  name: string;
  path: string; // 累计路径（与文档 path 一致）
  count: number; // 该目录直属文档数
  subtreeCount: number; // 含子目录的文档数
  children: Map<string, TreeNode>;
};

function buildTree(rows: PathRow[]): TreeNode {
  const root: TreeNode = { name: '__root__', path: '', count: 0, subtreeCount: 0, children: new Map() };
  for (const r of rows) {
    if (!r.path) continue;
    const parts = r.path.split('/');
    let cur = root;
    let cumulative = '';
    for (let i = 0; i < parts.length; i++) {
      const seg = parts[i];
      cumulative = cumulative ? `${cumulative}/${seg}` : seg;
      let child = cur.children.get(seg);
      if (!child) {
        child = { name: seg, path: cumulative, count: 0, subtreeCount: 0, children: new Map() };
        cur.children.set(seg, child);
      }
      child.subtreeCount += r.count;
      if (i === parts.length - 1) child.count += r.count;
      cur = child;
    }
    root.subtreeCount += r.count;
  }
  return root;
}

function isBuiltinDoc(doc: KnowledgeDoc): boolean {
  return doc.source_type === 'vault';
}

function sourceLabel(doc: KnowledgeDoc, tr: (zh: string, en: string) => string): string {
  if (doc.source_type === 'vault') return tr('内置', 'built-in');
  if (doc.source_type === 'upload') return tr('上传', 'upload');
  if (doc.source_type === 'repo') return 'repo';
  return tr('手动', 'manual');
}

function errorText(e: unknown): string {
  return e instanceof ApiError ? e.message : (e as Error).message;
}

/** 页面与弹窗共用的错误条；`role="alert"` 走无语义容器，避免与标题层级冲突。 */
function ErrorBanner({ text }: { text: string }) {
  return (
    <div role="alert" className="rounded-lg border border-red-500/40 bg-red-500/5 px-3 py-2 text-[11.5px] leading-5 text-red-300">
      {text}
    </div>
  );
}

/** 表单字段：说明文字用 `htmlFor` 关联控件，避免把控件内容并进可访问名。 */
function Field({
  label,
  htmlFor,
  children,
  className,
}: {
  label: ReactNode;
  htmlFor: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('space-y-1.5', className)}>
      <label htmlFor={htmlFor} className="block text-[11px] leading-4 text-zinc-500">
        {label}
      </label>
      {children}
    </div>
  );
}

export default function KnowledgePage() {
  const { tr } = useI18n();
  const { canMutate } = usePermissions();
  const [items, setItems] = useState<KnowledgeDoc[]>([]);
  const [group, setGroup] = useState<SourceGroup>('org');
  const [activePath, setActivePath] = useState(''); // '' = 整个分组
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState<KnowledgeDoc | 'create' | null>(null);
  const [deleting, setDeleting] = useState<KnowledgeDoc | null>(null);
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<SearchHit[]>([]);
  // 文档列表前端分页；默认 20 条/页
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(20);
  // 来源筛选（空串 = 全部来源）；与侧边栏分组正交，只落在组织文档这一侧
  const [sourceFilter, setSourceFilter] = useState<DocSource | ''>('');
  // 更新时间排序方向；默认按最近更新在前
  const [updatedSort, setUpdatedSort] = useState<'desc' | 'asc'>('desc');
  // 已完成检索的关键词；非 null 时右侧进入结果态
  const [searchedQuery, setSearchedQuery] = useState<string | null>(null);
  // 打开文档时定位到的命中块；关闭编辑器即清空
  const [locate, setLocate] = useState<{ chunkIndex: number; excerpt: string } | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const fetchAll = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true);
    else setLoading(true);
    try {
      const res = await listDocs();
      setItems(res.items ?? []);
      setErr(null);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void fetchAll();
  }, [fetchAll]);

  // 轻提示 5 秒自动消失，与 MCP / 技能页同一节奏
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 5000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const builtinItems = useMemo(() => items.filter(isBuiltinDoc), [items]);
  const orgItems = useMemo(() => items.filter((d) => !isBuiltinDoc(d)), [items]);

  const treeFrom = useCallback((docs: KnowledgeDoc[]): TreeNode => {
    const counts = new Map<string, number>();
    for (const d of docs) {
      const p = d.path ?? '';
      if (p) counts.set(p, (counts.get(p) ?? 0) + 1);
    }
    return buildTree([...counts.entries()].map(([path, count]) => ({ path, count })));
  }, []);

  const builtinTree = useMemo(() => treeFrom(builtinItems), [builtinItems, treeFrom]);
  const orgTree = useMemo(() => treeFrom(orgItems), [orgItems, treeFrom]);

  const scopedItems = group === 'builtin' ? builtinItems : orgItems;

  // 右侧列表：当前分组 + 来源筛选 + 选中目录（含子目录）的文档，按更新时间排序
  const visibleDocs = useMemo(() => {
    const bySource = sourceFilter === '' ? scopedItems : scopedItems.filter((d) => d.source_type === sourceFilter);
    const scoped = !activePath
      ? bySource
      : bySource.filter((d) => d.path === activePath || (d.path ?? '').startsWith(`${activePath}/`));
    // 更新时间列可点排序；同一时刻按 id 兜底，保证顺序稳定可复现
    return [...scoped].sort((a, b) => {
      const diff = new Date(a.updated_at).getTime() - new Date(b.updated_at).getTime();
      if (diff === 0) return a.id.localeCompare(b.id);
      return updatedSort === 'asc' ? diff : -diff;
    });
  }, [scopedItems, activePath, sourceFilter, updatedSort]);

  const counts = useMemo(
    () => ({ builtin: builtinItems.length, org: orgItems.length, total: items.length }),
    [builtinItems.length, orgItems.length, items.length],
  );

  // 选目录 = 切分组 + 切路径；旧检索结果的作用域已失效，退回文档列表
  const pickFolder = useCallback((next: SourceGroup, path: string) => {
    setGroup(next);
    setActivePath(path);
    setHits([]);
    setSearchedQuery(null);
    setLocate(null);
  }, []);

  const onUploadFiles = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return;
      setUploading(true);
      setErr(null);
      try {
        for (const file of Array.from(files)) {
          await uploadDoc(file);
        }
        pickFolder('org', '');
        await fetchAll(true);
        setToast({ kind: 'ok', text: tr(`已上传 ${files.length} 个文件`, `Uploaded ${files.length} file(s)`) });
      } catch (e) {
        setErr(errorText(e));
      } finally {
        setUploading(false);
        if (fileInputRef.current) fileInputRef.current.value = '';
      }
    },
    [fetchAll, pickFolder, tr],
  );

  const onMoveDoc = useCallback(
    async (docId: string, targetPath: string) => {
      setErr(null);
      try {
        await moveDoc(docId, targetPath);
        await fetchAll(true);
      } catch (e) {
        setErr(errorText(e));
      }
    },
    [fetchAll],
  );

  // 文档启停只影响检索：停用后切块保留，可随时恢复
  const onToggleDocEnabled = useCallback(
    async (doc: KnowledgeDoc, enabled: boolean) => {
      setErr(null);
      try {
        const saved = await setDocEnabled(doc.id, enabled);
        setItems((prev) => prev.map((d) => (d.id === saved.id ? saved : d)));
        setToast({
          kind: 'ok',
          text: enabled ? tr('已启用，重新参与检索', 'Enabled for retrieval') : tr('已停用，不再参与检索', 'Excluded from retrieval'),
        });
      } catch (e) {
        setErr(errorText(e));
      }
    },
    [tr],
  );

  const runSearch = async () => {
    const nextQuery = query.trim();
    if (!nextQuery) {
      setHits([]);
      setSearchedQuery(null);
      return;
    }
    setLocate(null);
    try {
      const res = await searchKnowledge(nextQuery, {
        limit: 10,
        pathPrefix: activePath || undefined,
        builtin: group === 'builtin',
        // 来源筛选只在组织文档下有意义；内置组的所有文档都是 vault
        sourceType: group === 'org' && sourceFilter !== '' ? sourceFilter : undefined,
      });
      setHits(res.items ?? []);
      setSearchedQuery(nextQuery);
    } catch (e) {
      setErr(errorText(e));
    }
  };

  const resetSearch = useCallback(() => {
    setQuery('');
    setHits([]);
    setSearchedQuery(null);
    setLocate(null);
  }, []);

  // 分页：列表在前端分页（文档量级有限，后端一次取回）；切换筛选时回到第 1 页
  useEffect(() => {
    setPage(0);
  }, [group, activePath, sourceFilter, searchedQuery, pageSize]);

  // 删除或筛选后页数变少，把页码夹回最后一页，避免停在空白页
  useEffect(() => {
    const lastPage = Math.max(0, Math.ceil(visibleDocs.length / pageSize) - 1);
    if (page > lastPage) setPage(lastPage);
  }, [visibleDocs.length, pageSize, page]);

  const pageDocs = useMemo(
    () => visibleDocs.slice(page * pageSize, page * pageSize + pageSize),
    [visibleDocs, page, pageSize],
  );

  useAssistantPage('知识库', 'Knowledge', {
    source: group,
    folder: activePath,
    visible_documents: visibleDocs.length,
    sample_document_ids: visibleDocs.slice(0, 6).map((doc) => doc.id).join(', '),
    loading,
  });

  const searchPlaceholder = activePath
    ? tr(`在「${localizedPath(activePath)}」下语义检索`, `Search under "${localizedPath(activePath)}"`)
    : group === 'builtin'
      ? tr('语义检索内置知识库', 'Search the built-in knowledge base')
      : tr('语义检索组织知识库', 'Search organization knowledge');

  // 已生效条件只保留检索词：分组与目录在左侧树里已经是可见的选中态，
  // 再在输入框下面重复一遍只是噪音；来源筛选有独立的胶囊控件，不需要再列一遍。
  const activeChips: ReactNode[] = [];
  if (searchedQuery !== null) {
    activeChips.push(
      <FilterChip key="q" label={tr(`检索：${searchedQuery}`, `Search: ${searchedQuery}`)} onClear={resetSearch} />,
    );
  }

  // 来源筛选：组织文档里 manual / upload / repo 混在一起，检索与列表都要能按来源收口；
  // 内置组只有 vault，给筛选反而制造「选了也一样」的假控件，因此不显示。
  const sourceOptions = [
    { value: '', label: tr('全部来源', 'All sources') },
    { value: 'manual', label: tr('手动', 'Manual') },
    { value: 'upload', label: tr('上传', 'Uploaded') },
    { value: 'repo', label: 'repo' },
  ];

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tr('知识库', 'Knowledge base')}
        subtitle={tr(
          `共 ${counts.total} 篇 · 组织 ${counts.org} · 内置 ${counts.builtin}`,
          `${counts.total} docs · ${counts.org} org · ${counts.builtin} built-in`,
        )}
        actions={
          <>
            <input
              ref={fileInputRef}
              type="file"
              accept=".md,.txt,.pdf,.docx"
              multiple
              className="hidden"
              onChange={(e) => void onUploadFiles(e.target.files)}
            />
            <RowAction
              icon={RefreshCw}
              iconClassName={refreshing ? 'animate-spin' : undefined}
              disabled={loading || refreshing}
              onClick={() => void fetchAll(true)}
            >
              {tr('刷新', 'Refresh')}
            </RowAction>
          </>
        }
      />

      <FilterBar
        className="mx-6 mt-3"
        footer={
          activeChips.length > 0 ? (
            <>
              {activeChips}
              <Button variant="ghost" size="sm" onClick={resetSearch}>
                {tr('清除检索', 'Clear search')}
              </Button>
            </>
          ) : null
        }
      >
        <SearchInput
          value={query}
          onChange={setQuery}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void runSearch();
          }}
          placeholder={searchPlaceholder}
          ariaLabel={tr('检索', 'Search')}
          className="min-w-[15rem] flex-1"
        />
        {group === 'org' && (
          <FilterPill
            label={tr('来源', 'Source')}
            active={sourceFilter !== ''}
            onClear={() => setSourceFilter('')}
            width="w-[8.5rem]"
          >
            <Select
              aria-label={tr('来源', 'Source')}
              value={sourceFilter}
              onValueChange={(v) => setSourceFilter(v as DocSource | '')}
              options={sourceOptions}
              variant="ghost"
              className="h-7 min-w-0 border-0 bg-transparent px-0 text-[12px] shadow-none"
            />
          </FilterPill>
        )}
        <span className="ml-auto shrink-0 pr-1 text-[11px] tabular-nums text-zinc-500">
          {searchedQuery !== null
            ? tr(`命中 ${hits.length} 条`, `${hits.length} hit(s)`)
            : tr(`${visibleDocs.length} 篇`, `${visibleDocs.length} docs`)}
        </span>
      </FilterBar>

      <div className="flex min-h-0 flex-1 overflow-hidden">
        <aside className="hidden w-[15rem] shrink-0 flex-col overflow-y-auto border-r border-zinc-800/60 px-2 py-3 md:flex">
          <SourceGroupSection
            label={tr('组织知识库', 'Organization')}
            icon={FileText}
            tree={orgTree}
            total={orgItems.length}
            active={group === 'org'}
            activePath={activePath}
            onPick={(path) => pickFolder('org', path)}
            onMoveDoc={onMoveDoc}
            actions={
              canMutate && (
                <>
                  <RowActionIcon
                    icon={Upload}
                    label={tr('上传文件', 'Upload file')}
                    disabled={uploading}
                    onClick={() => fileInputRef.current?.click()}
                  />
                  <RowActionIcon
                    icon={Plus}
                    label={tr('新建文档', 'New doc')}
                    onClick={() => {
                      setGroup('org');
                      setEditing('create');
                    }}
                  />
                </>
              )
            }
          />
          <div className="mx-2 my-2 h-px bg-zinc-800/60" />
          <SourceGroupSection
            label={tr('内置知识库', 'Built-in')}
            icon={BookOpen}
            tree={builtinTree}
            total={builtinItems.length}
            active={group === 'builtin'}
            activePath={activePath}
            onPick={(path) => pickFolder('builtin', path)}
          />
          {canMutate && (
            <p className="mt-auto px-2 pt-6 text-[10px] leading-relaxed text-zinc-600">
              {tr('拖动文档行首把手到目录上可移动。', 'Drag a doc handle onto a folder to move it.')}
            </p>
          )}
        </aside>

        <section className="flex min-w-0 flex-1 flex-col overflow-hidden px-6 pb-5 pt-3">
          {err && (
            <div className="mb-3 shrink-0">
              <ErrorBanner text={err} />
            </div>
          )}

          {loading ? (
            <div className="flex h-40 items-center justify-center text-[11.5px] text-zinc-500">{tr('加载中…', 'Loading…')}</div>
          ) : searchedQuery !== null ? (
            hits.length > 0 ? (
              <TableCard className="min-h-0 flex-1">
                <table className="og-action-table w-full text-sm">
                  <thead className={TABLE_HEAD}>
                    <tr className="whitespace-nowrap">
                      <th className="w-12 px-3 py-2 text-left font-medium">#</th>
                      <th className="w-full max-w-0 px-3 py-2 text-left font-medium">{tr('文档', 'Document')}</th>
                      <th className="px-3 py-2 text-right font-medium">{tr('检索得分', 'Score')}</th>
                      <th className="px-3 py-2 text-right font-medium">{tr('操作', 'Actions')}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/40">
                    {hits.map((hit, index) => (
                      <SearchHitRow
                        key={hit.doc.id}
                        rank={index + 1}
                        hit={hit}
                        onOpen={() => {
                          setLocate({ chunkIndex: hit.chunk_index, excerpt: hit.excerpt });
                          setEditing(hit.doc);
                        }}
                      />
                    ))}
                  </tbody>
                </table>
              </TableCard>
            ) : (
              <EmptyStateCard
                icon={SearchX}
                title={tr(`没有命中「${searchedQuery}」的切块`, `No chunks matched "${searchedQuery}"`)}
                hint={
                  activePath
                    ? tr('换个更具体的说法，或把目录切回整个分组再试。', 'Try a more specific phrase, or search the whole group.')
                    : tr('换个更具体的说法，或在「设置 → 知识库」里调低最低 RRF 分。', 'Try a more specific phrase, or lower the minimum RRF score in Settings → Knowledge.')
                }
                action={
                  <Button variant="outline" size="sm" onClick={resetSearch}>
                    {tr('返回文档列表', 'Back to documents')}
                  </Button>
                }
              />
            )
          ) : visibleDocs.length === 0 ? (
            <EmptyStateCard
              icon={BookOpen}
              title={
                activePath
                  ? tr(`「${localizedPath(activePath)}」里还没有文档`, `No docs in "${localizedPath(activePath)}" yet`)
                  : group === 'builtin'
                    ? tr('内置知识库为空', 'Built-in knowledge base is empty')
                    : tr('组织知识库还没有内容', 'No organization docs yet')
              }
              hint={!canMutate ? tr('请联系管理员添加或同步知识库。', 'Ask an admin to add or sync knowledge.') : undefined}
              action={
                canMutate && (
                  <div className="flex items-center gap-2">
                    <Button variant="outline" size="sm" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
                      <Upload size={12} /> {tr('上传文件', 'Upload')}
                    </Button>
                    <Button variant="primary" size="sm" onClick={() => setEditing('create')}>
                      <Plus size={12} /> {tr('新建文档', 'New doc')}
                    </Button>
                  </div>
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
                  total={visibleDocs.length}
                  shown={pageDocs.length}
                  loading={refreshing}
                  onPageChange={setPage}
                  pageSizeOptions={[10, 20, 50, 100]}
                  onPageSizeChange={(size) => {
                    setPage(0);
                    setPageSize(size);
                  }}
                />
              }
            >
              <table className="og-action-table w-full text-sm">
                <thead className={TABLE_HEAD}>
                  <tr className="whitespace-nowrap">
                    <th className="w-6 px-2 py-2">
                      <span className="sr-only">{tr('排序', 'Reorder')}</span>
                    </th>
                    <th className="w-full max-w-0 px-3 py-2 text-left font-medium">{tr('文档', 'Document')}</th>
                    <th
                      className="w-[10.5rem] px-3 py-2 text-left font-medium"
                      aria-sort={updatedSort === 'asc' ? 'ascending' : 'descending'}
                    >
                      <button
                        type="button"
                        className="inline-flex items-center gap-1 hover:text-zinc-200"
                        onClick={() => {
                          setPage(0);
                          setUpdatedSort((d) => (d === 'desc' ? 'asc' : 'desc'));
                        }}
                      >
                        {tr('更新时间', 'Updated')}
                        {updatedSort === 'desc' ? (
                          <ArrowDown size={12} aria-hidden />
                        ) : (
                          <ArrowUp size={12} aria-hidden />
                        )}
                      </button>
                    </th>
                    <th className="w-24 px-3 py-2 text-left font-medium">{tr('参与检索', 'Retrieval')}</th>
                    <th className="w-20 px-3 py-2 text-right font-medium">{tr('操作', 'Actions')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/40">
                  {pageDocs.map((doc) => (
                    <DocRow
                      key={doc.id}
                      doc={doc}
                      canMutate={canMutate}
                      onEdit={() => {
                        setLocate(null);
                        setEditing(doc);
                      }}
                      onDelete={() => setDeleting(doc)}
                      onToggleEnabled={(enabled) => void onToggleDocEnabled(doc, enabled)}
                    />
                  ))}
                </tbody>
              </table>
            </TableCard>
          )}
        </section>
      </div>

      {editing && (
        <DocEditor
          mode={editing === 'create' ? 'create' : 'edit'}
          existing={editing === 'create' ? null : editing}
          defaultPath={editing === 'create' ? activePath : undefined}
          locate={locate}
          onClose={() => {
            setEditing(null);
            setLocate(null);
          }}
          onSaved={() => {
            setEditing(null);
            setLocate(null);
            void fetchAll(true);
          }}
        />
      )}

      {deleting && (
        <ConfirmDialog
          open
          title={tr(`删除「${localizedDocTitle(deleting)}」`, `Delete "${localizedDocTitle(deleting)}"`)}
          description={
            <p>
              {tr(
                '删除后文档与切块一并移除，无法恢复。若只是暂时不想被检索到，可以改用「参与检索」开关停用。',
                'The document and its chunks are removed permanently. To pause retrieval only, turn off "Include in retrieval" instead.',
              )}
            </p>
          }
          onConfirm={async () => {
            await deleteDoc(deleting.id);
            setToast({ kind: 'ok', text: tr('文档已删除', 'Document deleted') });
            setDeleting(null);
            await fetchAll(true);
          }}
          onClose={() => setDeleting(null)}
        />
      )}

      {toast && <Toast tone={toast.kind} text={toast.text} />}
    </main>
  );
}

// 来源分组：分组头本身是「查看整个分组」的入口，也是拖放到根目录的放置区。
function SourceGroupSection({
  label,
  icon: Icon,
  tree,
  total,
  active,
  activePath,
  onPick,
  onMoveDoc,
  actions,
}: {
  label: string;
  icon: IconType;
  tree: TreeNode;
  total: number;
  active: boolean;
  activePath: string;
  onPick(path: string): void;
  onMoveDoc?: (docId: string, path: string) => void;
  actions?: ReactNode;
}) {
  const [dragOver, setDragOver] = useState(false);
  const accepts = (e: DragEvent) => !!onMoveDoc && e.dataTransfer.types.includes(DOC_DND_MIME);
  const rootActive = active && activePath === '';

  return (
    <div>
      <div className="flex items-center gap-0.5">
        <button
          type="button"
          onClick={() => onPick('')}
          onDragOver={
            onMoveDoc
              ? (e) => {
                  if (!accepts(e)) return;
                  e.preventDefault();
                  e.dataTransfer.dropEffect = 'move';
                  setDragOver(true);
                }
              : undefined
          }
          onDragLeave={onMoveDoc ? () => setDragOver(false) : undefined}
          onDrop={
            onMoveDoc
              ? (e) => {
                  setDragOver(false);
                  if (!accepts(e)) return;
                  e.preventDefault();
                  const id = e.dataTransfer.getData(DOC_DND_MIME);
                  if (id) onMoveDoc(id, '');
                }
              : undefined
          }
          className={cn(
            'relative flex h-7 min-w-0 flex-1 items-center gap-2 rounded-md px-2 text-left transition-colors',
            dragOver
              ? 'bg-sky-500/10 ring-1 ring-inset ring-sky-400/70'
              : rootActive
                ? 'bg-indigo-500/10 text-zinc-100'
                : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200',
          )}
        >
          {rootActive && <span aria-hidden className="absolute inset-y-1 left-0 w-0.5 rounded-full bg-indigo-400" />}
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded border border-zinc-800/60 bg-zinc-900/50 text-zinc-500">
            <Icon size={11} aria-hidden />
          </span>
          <span className="min-w-0 flex-1 truncate text-[11.5px] font-medium">{label}</span>
          <span className="shrink-0 font-mono text-[10px] tabular-nums text-zinc-500">{total}</span>
        </button>
        {actions && <div className="flex shrink-0 items-center gap-0.5 pr-0.5">{actions}</div>}
      </div>
      <div className="mt-0.5">
        {[...tree.children.values()]
          .sort((a, b) => a.name.localeCompare(b.name))
          .map((node) => (
            <FolderNode
              key={node.path}
              node={node}
              depth={0}
              activePath={active ? activePath : ''}
              onPick={onPick}
              onMoveDoc={onMoveDoc}
            />
          ))}
      </div>
    </div>
  );
}

function FolderNode({
  node,
  depth,
  activePath,
  onPick,
  onMoveDoc,
}: {
  node: TreeNode;
  depth: number;
  activePath: string;
  onPick(path: string): void;
  onMoveDoc?: (docId: string, path: string) => void;
}) {
  const { tr } = useI18n();
  const isActive = activePath === node.path;
  const isAncestor = activePath.startsWith(`${node.path}/`);
  const [expanded, setExpanded] = useState(isActive || isAncestor);
  const [dragOver, setDragOver] = useState(false);
  const hasChildren = node.children.size > 0;
  const ChevronIcon = expanded ? FolderOpen : Folder;

  useEffect(() => {
    if (isActive || isAncestor) setExpanded(true);
  }, [isActive, isAncestor]);

  const accepts = (e: DragEvent) => !!onMoveDoc && e.dataTransfer.types.includes(DOC_DND_MIME);

  return (
    <div>
      <div
        onDragOver={
          onMoveDoc
            ? (e) => {
                if (!accepts(e)) return;
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                setDragOver(true);
              }
            : undefined
        }
        onDragLeave={onMoveDoc ? () => setDragOver(false) : undefined}
        onDrop={
          onMoveDoc
            ? (e) => {
                setDragOver(false);
                if (!accepts(e)) return;
                e.preventDefault();
                const id = e.dataTransfer.getData(DOC_DND_MIME);
                if (id) onMoveDoc(id, node.path);
              }
            : undefined
        }
        style={{ paddingLeft: 6 + depth * 12 }}
        className={cn(
          'relative flex items-center gap-0.5 rounded-md pr-1 transition-colors',
          dragOver
            ? 'bg-sky-500/10 ring-1 ring-inset ring-sky-400/70'
            : isActive
              ? 'bg-indigo-500/10 text-zinc-100'
              : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200',
        )}
      >
        {isActive && <span aria-hidden className="absolute inset-y-1 left-0 w-0.5 rounded-full bg-indigo-400" />}
        {hasChildren ? (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            aria-label={expanded ? tr('折叠', 'Collapse') : tr('展开', 'Expand')}
            className="inline-flex h-6 w-5 shrink-0 items-center justify-center rounded text-zinc-600 transition-colors hover:text-zinc-300"
          >
            <ChevronRight size={11} className={cn('transition-transform', expanded && 'rotate-90')} aria-hidden />
          </button>
        ) : (
          <span aria-hidden className="h-6 w-5 shrink-0" />
        )}
        <button
          type="button"
          onClick={() => onPick(node.path)}
          title={localizedPath(node.path)}
          className="flex min-w-0 flex-1 items-center gap-1.5 py-1 text-left text-[11.5px]"
        >
          <ChevronIcon size={11.5} className={cn('shrink-0', isActive ? 'text-indigo-300' : 'text-zinc-500')} aria-hidden />
          <span className="truncate">{localizedPathSegment(node.name)}</span>
          <span
            className={cn(
              'ml-auto shrink-0 font-mono text-[10px] tabular-nums',
              isActive ? 'text-indigo-300' : 'text-zinc-600',
            )}
          >
            {node.subtreeCount}
          </span>
        </button>
      </div>
      {expanded && hasChildren && (
        <div>
          {[...node.children.values()]
            .sort((a, b) => a.name.localeCompare(b.name))
            .map((child) => (
              <FolderNode
                key={child.path}
                node={child}
                depth={depth + 1}
                activePath={activePath}
                onPick={onPick}
                onMoveDoc={onMoveDoc}
              />
            ))}
        </div>
      )}
    </div>
  );
}

// 文档行：主信息一行（来源胶囊 + 标题），元信息一行（目录 / 来源地址）；
// 标签单独成列，统一用中性胶囊；整行不响应点击，打开文档只走行内「编辑 / 查看」。
function DocRow({
  doc,
  canMutate,
  onEdit,
  onDelete,
  onToggleEnabled,
}: {
  doc: KnowledgeDoc;
  canMutate: boolean;
  onEdit(): void;
  onDelete(): void;
  onToggleEnabled(enabled: boolean): void;
}) {
  const { tr } = useI18n();
  const editable = doc.source_type === 'manual' || doc.source_type === 'upload';
  const title = localizedDocTitle(doc);
  const DocIcon = isBuiltinDoc(doc) ? BookOpen : FileText;
  const tags = doc.tags ?? [];

  // 拖拽预览只保留标题胶囊，避免把整行的开关与操作按钮一起拎起
  const onDragStart = (e: DragEvent<HTMLSpanElement>) => {
    e.dataTransfer.setData(DOC_DND_MIME, doc.id);
    e.dataTransfer.effectAllowed = 'move';
    const ghost = document.createElement('div');
    ghost.className =
      'pointer-events-none fixed -left-[9999px] top-0 max-w-[280px] truncate rounded-lg border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-[11.5px] font-medium text-zinc-100 shadow-xl';
    ghost.textContent = title;
    document.body.appendChild(ghost);
    e.dataTransfer.setDragImage(ghost, 12, 12);
    window.setTimeout(() => ghost.remove(), 0);
  };

  return (
    <tr className={cn('group', !doc.enabled && 'opacity-60')}>
      <td className="w-6 py-2 pl-2 pr-0">
        {editable ? (
          <span
            draggable
            onDragStart={onDragStart}
            aria-hidden
            title={tr('拖动到左侧目录移动', 'Drag onto a folder to move')}
            className="inline-flex h-7 w-4 cursor-grab items-center justify-center text-zinc-600 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 active:cursor-grabbing"
          >
            <GripVertical size={12} />
          </span>
        ) : (
          <span aria-hidden className="inline-block h-7 w-4" />
        )}
      </td>
      <td className="px-3 py-[7px]">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-zinc-800/60 bg-zinc-900/50 text-zinc-500">
            <DocIcon size={12} aria-hidden />
          </span>
          <span className="truncate text-[12.5px] font-medium text-zinc-100" title={title}>
            {title}
          </span>
          {!doc.enabled && <Chip dense tone="warning">{tr('已停用', 'Disabled')}</Chip>}
        </div>
        <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 pl-8 text-[10.5px] leading-4">
          <Chip dense>{sourceLabel(doc, tr)}</Chip>
          {doc.path ? (
            <span className="inline-flex min-w-0 items-center gap-1 text-zinc-500" title={localizedPath(doc.path)}>
              <Folder size={10} className="shrink-0 text-zinc-600" aria-hidden />
              <span className="truncate font-mono">{localizedPath(doc.path)}</span>
            </span>
          ) : (
            <span className="text-zinc-600">{tr('未归目录', 'No folder')}</span>
          )}
          {tags.slice(0, 3).map((tag) => (
            <Chip key={tag} dense className="max-w-[9rem] truncate" title={`#${tag}`}>
              #{tag}
            </Chip>
          ))}
          {tags.length > 3 && (
            <Chip dense title={tags.slice(3).map((t) => `#${t}`).join(' ')}>
              +{tags.length - 3}
            </Chip>
          )}
          {doc.url && (
            <span className="max-w-[18rem] truncate font-mono text-zinc-600" title={doc.url}>
              {doc.url}
            </span>
          )}
        </div>
      </td>
      <td className="px-3 py-[7px]">
        {/* 完整日期时间：相对时间无法核对与排序，列表里一律不用 */}
        <span className="whitespace-nowrap text-[11.5px] tabular-nums text-zinc-500">
          {fullDateTime(doc.updated_at)}
        </span>
      </td>
      <td className="px-3 py-[7px]">
        <Switch
          checked={doc.enabled}
          onCheckedChange={onToggleEnabled}
          disabled={!canMutate}
          aria-label={tr(`参与检索 ${title}`, `Include ${title} in retrieval`)}
        />
      </td>
      <td className="px-3 py-[7px]">
        <div className="flex items-center justify-end gap-0.5 opacity-60 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
          {editable ? (
            <>
              <RowActionIcon
                icon={Pencil}
                label={tr('编辑', 'Edit')}
                aria-label={tr(`编辑 ${title}`, `Edit ${title}`)}
                onClick={onEdit}
              />
              <RowActionIcon
                icon={Trash2}
                label={tr('删除', 'Delete')}
                aria-label={tr(`删除 ${title}`, `Delete ${title}`)}
                tone="danger"
                onClick={onDelete}
              />
            </>
          ) : (
            <RowActionIcon
              icon={Eye}
              label={tr('查看', 'View')}
              aria-label={tr(`查看 ${title}`, `View ${title}`)}
              onClick={onEdit}
            />
          )}
        </div>
      </td>
    </tr>
  );
}

function SearchHitRow({ hit, rank, onOpen }: { hit: SearchHit; rank: number; onOpen(): void }) {
  const { tr } = useI18n();
  const title = localizedDocTitle(hit.doc);
  return (
    <tr>
      <td className="px-3 py-2.5 align-top font-mono text-[10.5px] tabular-nums text-zinc-600">
        {String(rank).padStart(2, '0')}
      </td>
      <td className="px-3 py-2.5">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <span className="truncate text-[12.5px] font-medium text-zinc-100" title={title}>
            {title}
          </span>
          {hit.doc.path && (
            <span className="max-w-[14rem] truncate font-mono text-[10px] text-zinc-500">
              {localizedPath(hit.doc.path)}
            </span>
          )}
          <Chip dense>{tr(`命中块 ${hit.chunk_index + 1}`, `chunk ${hit.chunk_index + 1}`)}</Chip>
        </div>
        <p className="mt-1 line-clamp-2 whitespace-pre-wrap break-words text-[11.5px] leading-5 text-zinc-400">
          {hit.excerpt}
        </p>
      </td>
      <td className="whitespace-nowrap px-3 py-2.5 text-right align-top font-mono text-[10px] leading-4 text-zinc-500">
        <span className="block">
          RRF <span className="text-zinc-300">{hit.score.toFixed(4)}</span>
        </span>
        {hit.vector_score !== null && (
          <span className="block">
            {tr('向量相似度', 'vector sim')} <span className="text-zinc-300">{hit.vector_score.toFixed(3)}</span>
          </span>
        )}
        {hit.keyword_rank !== null && (
          <span className="block">
            {tr('关键词排名', 'keyword rank')} <span className="text-zinc-300">#{hit.keyword_rank}</span>
          </span>
        )}
      </td>
      <td className="px-3 py-2.5 text-right align-top">
        <RowAction
          icon={Search}
          onClick={onOpen}
          aria-label={tr(`打开并定位 ${title}`, `Open & locate ${title}`)}
        >
          {tr('打开并定位', 'Open & locate')}
        </RowAction>
      </td>
    </tr>
  );
}

// 阅读态正文：先剥离 YAML frontmatter 再渲染；元信息以弱化键值行单独展示。
function DocBody({ content }: { content: string }) {
  const fm = useMemo(() => splitFrontmatter(content), [content]);
  return (
    <div className="md-body text-sm text-zinc-200">
      {fm && (
        <dl className="mb-4 space-y-1 border-b border-zinc-800 pb-3">
          {fm.meta.map(([key, value]) => (
            <div key={key} className="flex items-baseline gap-2 text-xs">
              <dt className="shrink-0 font-mono text-[11px] text-zinc-500">{key}</dt>
              {Array.isArray(value) ? (
                <dd className="flex flex-wrap gap-1">
                  {value.map((v) => (
                    <Chip key={v}>{v}</Chip>
                  ))}
                </dd>
              ) : (
                <dd className="text-zinc-400">{value}</dd>
              )}
            </div>
          ))}
        </dl>
      )}
      {/* remark-gfm：表格 / 删除线 / 任务列表等 GFM 语法 */}
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{fm ? fm.body : content}</ReactMarkdown>
    </div>
  );
}

function LocateBanner({
  locate,
  readOnly,
}: {
  locate: { chunkIndex: number; excerpt: string } | null | undefined;
  readOnly: boolean;
}) {
  const { tr } = useI18n();
  if (!locate) return null;
  return (
    <div className="rounded-lg border border-indigo-500/30 bg-indigo-500/5 px-3 py-2 text-[11.5px] leading-5 text-zinc-300">
      {readOnly
        ? tr(`检索命中块 ${locate.chunkIndex + 1}`, `Matched chunk ${locate.chunkIndex + 1}`)
        : tr(
            `检索命中块 ${locate.chunkIndex + 1}，已在正文中选中`,
            `Matched chunk ${locate.chunkIndex + 1}, selected in the body`,
          )}
      {readOnly && (
        <div className="mt-1 line-clamp-4 whitespace-pre-wrap break-words font-mono text-[10.5px] text-zinc-400">
          {locate.excerpt}
        </div>
      )}
    </div>
  );
}

function DocEditor({
  mode,
  existing,
  defaultPath,
  locate,
  onClose,
  onSaved,
}: {
  mode: 'create' | 'edit';
  existing: KnowledgeDoc | null;
  defaultPath?: string;
  locate?: { chunkIndex: number; excerpt: string } | null;
  onClose(): void;
  onSaved(): void;
}) {
  const { tr } = useI18n();
  const [title, setTitle] = useState(existing?.title ?? '');
  const [titleEN, setTitleEN] = useState(existing?.title_en ?? '');
  const [content, setContent] = useState(existing?.content ?? '');
  const [url, setUrl] = useState(existing?.url ?? '');
  const [path, setPath] = useState(existing?.path ?? defaultPath ?? '');
  const [tagsText, setTagsText] = useState((existing?.tags ?? []).join(', '));
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const locatedRef = useRef(false);

  // 内置（vault）与 repo 文档随同步刷新，就地修改会被覆盖（后端 PATCH 也会拒绝）；
  // 只读查看 + 「复制为组织文档」是受支持的自定义方式。
  const sourceReadOnly = existing?.source_type === 'repo' || existing?.source_type === 'vault';
  const [forked, setForked] = useState(false);
  const readOnly = mode === 'edit' && sourceReadOnly && !forked;
  // 上传文档的 url 是它的身份（文件名），后端不允许改，这里只读展示
  const urlLocked = mode === 'edit' && existing?.source_type === 'upload';

  useEffect(() => {
    if (mode === 'edit' && existing && !content) {
      setLoading(true);
      void (async () => {
        try {
          const res = await import('@/api/knowledge').then((m) => m.getDoc(existing.id));
          setContent(res.content ?? '');
        } catch (e) {
          setErr(errorText(e));
        } finally {
          setLoading(false);
        }
      })();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, existing?.id]);

  // 命中块定位：正文加载完成后选中 excerpt，并把编辑框滚到对应行
  useEffect(() => {
    if (!locate || !content || readOnly || locatedRef.current) return;
    const el = textareaRef.current;
    if (!el) return;
    const start = content.indexOf(locate.excerpt);
    if (start < 0) return;
    locatedRef.current = true;
    el.focus();
    el.setSelectionRange(start, start + locate.excerpt.length);
    const line = content.slice(0, start).split('\n').length;
    el.scrollTop = Math.max(0, (line - 3) * 19);
  }, [locate, content, readOnly]);

  const submit = async () => {
    setSubmitting(true);
    setErr(null);
    try {
      const tags = tagsText
        .split(/[,，]/)
        .map((t) => t.trim())
        .filter(Boolean);
      const cleanPath = path
        .split('/')
        .map((s) => s.trim())
        .filter(Boolean)
        .join('/');
      if (mode === 'create' || forked) {
        await createDoc({
          title: title.trim(),
          title_en: titleEN.trim() || undefined,
          content: content.trim(),
          url: forked ? undefined : url.trim() || undefined,
          path: cleanPath || undefined,
          tags: tags.length ? tags : undefined,
        });
      } else if (existing) {
        await updateDoc(existing.id, {
          title: title.trim(),
          title_en: titleEN.trim() || undefined,
          content: content.trim(),
          path: cleanPath || undefined,
          tags: tags.length ? tags : undefined,
        });
      }
      onSaved();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setSubmitting(false);
    }
  };

  // 只读查看器：渲染 markdown 正文 + 顶部元信息条
  if (readOnly) {
    const isVault = existing?.source_type === 'vault';
    return (
      <Modal
        open
        onClose={onClose}
        // 内置 runbook 普遍带宽表格，md(448px) 下没法看，默认给 max-w-4xl 且可拖宽
        size="xl"
        resizable
        title={existing ? localizedDocTitle(existing) : tr('文档', 'Document')}
        footer={
          <>
            <Button variant="outline" size="sm" onClick={onClose}>
              {tr('关闭', 'Close')}
            </Button>
            <Hint
              content={
                isVault
                  ? tr(
                      '内置文档随同步更新、不可直接修改；复制一份到组织知识库后即可编辑',
                      'Built-in docs are refreshed on sync and not directly editable; copy into the org knowledge base to edit',
                    )
                  : tr(
                      'repo 文档随同步更新、不可直接修改；复制一份到组织知识库后即可编辑',
                      'Repo docs are refreshed on sync and not directly editable; copy into the org knowledge base to edit',
                    )
              }
            >
              <Button
                variant="primary"
                size="sm"
                onClick={() => {
                  // 副本是新组织文档，不带内置/repo 的来源地址
                  setUrl('');
                  setForked(true);
                }}
                disabled={loading || !content}
              >
                <Copy size={11} />
                {tr('复制为组织文档', 'Copy as org doc')}
              </Button>
            </Hint>
          </>
        }
      >
        <div className="space-y-3">
          <LocateBanner locate={locate} readOnly />
          <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
            <Chip>{isVault ? tr('内置', 'built-in') : 'repo'}</Chip>
            {existing?.path && <Chip>{localizedPath(existing.path)}</Chip>}
            {(existing?.tags ?? []).map((tag) => (
              <Chip key={tag}>{tag}</Chip>
            ))}
            {existing?.url &&
              (existing.url.startsWith('http') ? (
                <a
                  href={existing.url}
                  target="_blank"
                  rel="noreferrer"
                  title={existing.url}
                  className="ml-auto max-w-[24rem] truncate font-mono text-[10px] text-zinc-500 underline-offset-2 hover:text-zinc-300 hover:underline"
                >
                  {existing.url}
                </a>
              ) : (
                <span className="ml-auto max-w-[24rem] truncate font-mono text-[10px] text-zinc-500" title={existing.url}>
                  {existing.url}
                </span>
              ))}
          </div>
          <div className="rounded-lg border border-zinc-800/60 bg-zinc-950/40 p-4">
            {loading ? (
              <div className="text-[11.5px] text-zinc-500">{tr('加载中…', 'Loading…')}</div>
            ) : err ? (
              <ErrorBanner text={err} />
            ) : content ? (
              <DocBody content={content} />
            ) : (
              <div className="text-[11.5px] text-zinc-500">{tr('该文档没有正文内容', 'This document has no body content')}</div>
            )}
          </div>
        </div>
      </Modal>
    );
  }

  return (
    <Modal
      open
      onClose={onClose}
      size="xl"
      resizable
      title={
        mode === 'create'
          ? tr('新建知识文档', 'New knowledge doc')
          : forked
            ? tr(
                `复制「${existing ? localizedDocTitle(existing) : ''}」为组织文档`,
                `Copy "${existing ? localizedDocTitle(existing) : ''}" as org doc`,
              )
            : tr(`编辑 ${existing ? localizedDocTitle(existing) : ''}`, `Edit ${existing ? localizedDocTitle(existing) : ''}`)
      }
      footer={
        <>
          <Button variant="outline" size="sm" onClick={onClose}>
            {tr('取消', 'Cancel')}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => void submit()}
            disabled={submitting || title.trim() === '' || content.trim() === ''}
          >
            {submitting ? tr('保存中…', 'Saving…') : tr('保存', 'Save')}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {err && <ErrorBanner text={err} />}
        <LocateBanner locate={locate} readOnly={false} />
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={tr('标题 *', 'Title *')} htmlFor="knowledge-doc-title">
            <Input
              id="knowledge-doc-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={tr('例：nginx 重启 SOP', 'e.g. nginx restart SOP')}
              maxLength={256}
            />
          </Field>
          <Field
            label={tr('英文标题（en-US 下显示，可选）', 'English title (shown in en-US, optional)')}
            htmlFor="knowledge-doc-title-en"
          >
            <Input
              id="knowledge-doc-title-en"
              value={titleEN}
              onChange={(e) => setTitleEN(e.target.value)}
              placeholder={tr('例：nginx restart SOP', 'e.g. nginx restart SOP')}
              maxLength={256}
            />
          </Field>
          <Field label={tr('目录（用 / 分隔，可选）', 'Folder (separated by /, optional)')} htmlFor="knowledge-doc-path">
            <Input
              id="knowledge-doc-path"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder={tr('网络/DNS', 'Network/DNS')}
            />
          </Field>
          <Field label={tr('标签（逗号分隔，可选）', 'Tags (comma separated, optional)')} htmlFor="knowledge-doc-tags">
            <Input
              id="knowledge-doc-tags"
              value={tagsText}
              onChange={(e) => setTagsText(e.target.value)}
              placeholder="dns, resolv"
            />
          </Field>
        </div>
        <Field
          htmlFor="knowledge-doc-url"
          label={
            urlLocked
              ? tr('来源文件（不可修改）', 'Source file (read-only)')
              : tr('来源 URL（可选）', 'Source URL (optional)')
          }
        >
          <Input
            id="knowledge-doc-url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            readOnly={urlLocked}
            placeholder="https://wiki.internal/runbook/nginx-restart"
            className={cn('font-mono text-[11.5px]', urlLocked && 'cursor-not-allowed')}
          />
        </Field>
        <Field label={tr('内容（markdown） *', 'Content (markdown) *')} htmlFor="knowledge-doc-content">
          <Textarea
            id="knowledge-doc-content"
            ref={textareaRef}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder={tr(
              '支持 Markdown；助手按标题与正文做语义匹配',
              'Markdown supported; the assistant matches on title and content semantically',
            )}
            className="h-80 w-full resize-y font-mono text-[11.5px] leading-5"
          />
        </Field>
      </div>
    </Modal>
  );
}
