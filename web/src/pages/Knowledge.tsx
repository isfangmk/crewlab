// Knowledge page — 知识库工作台。
//
// 骨架（与 docs/UI设计规范.md 一致）：
//   PageHeader   标题 / 计数 / 刷新 + 页级 Tabs（文档 / 语义检索）
//   FilterBar    标题筛选 + 来源胶囊；第二行放目录条件
//   aside        来源分组 + 目录树（组织 / 内置两棵树常驻）
//   TableCard    文档表（文档 / 更新时间 / 参与检索 / 操作）+ 分页
//
// 目录树由 /knowledge/paths 按来源组聚合；建/改/上传文档时后端已同步重切块并重嵌入，
// 因此页面不再提供「重建索引」手动入口。
import { useAssistantPage } from '@/lib/assistantPage';
import { usePermissions } from '@/store/me';
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import {
  closestCenter,
  DndContext,
  DragOverlay,
  pointerWithin,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type CollisionDetection,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core';
import {
  BookOpen,
  ChevronRight,
  Copy,
  FileText,
  Folder,
  FolderOpen,
  GripVertical,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
  Upload,
  Download,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import rehypeMermaid from 'rehype-mermaid';
import remarkGfm from 'remark-gfm';
import { useThemeMode } from '@/store/mode';
import { cn } from '@/lib/cn';
import { splitFrontmatter } from '@/lib/frontmatter';
import type { IconType } from '@/lib/icon';
import { Modal } from '@/components/Modal';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { flash, Button, ChoiceChip, Chip, COL, EmptyStateCard, ErrorBanner, FilterBar, FilterChip, FilterPill, Input, PageHeader, RowAction, RowActionIcon, SearchInput, Select, SortableHead, StatusPill, TableCard, TableHead, TablePager, Tabs, TabsList, TabsTrigger, Textarea, tableLayout } from '@/components/ui';
import { Hint } from '@/components/ui/Tooltip';
import { LocateBanner, SemanticSearchPanel } from '@/components/knowledge/SearchPanel';
import { formatBytes, fullDateTime } from '@/lib/format';
import { listAgents, type AgentSummary } from '@/api/agents';
import {
  createDoc,
  deleteDoc,
  listDocs,
  listPaths,
  localizedDocTitle,
  localizedPath,
  localizedPathSegment,
  moveDoc,
  searchKnowledge,
  setDocEnabled,
  updateDoc,
  uploadDoc,
  downloadDocSource,
  type KnowledgeDoc,
  type DocSource,
  type PathRow,
  type SearchHit,
} from '@/api/knowledge';
import { useI18n } from '@/i18n/locale';
import { useRequestSeq } from '@/lib/requestSeq';
import { errorText } from '@/lib/errors';

// 拖放（@dnd-kit）：文档把手是 drag source，目录行是 drop target。
// 目标 id 带来源分组前缀——组织库与内置库有同名目录，不加前缀会互相顶掉注册。
const folderDropId = (group: 'org' | 'builtin', path: string) => `folder:${group}:${path}`;
// 从 droppable id 还原目录路径（'folder:org:concepts/dns' → 'concepts/dns'，根 → ''）。
const folderPathOf = (dropId: string) => dropId.split(':').slice(2).join(':');

// 目录行很窄且彼此紧邻：以指针命中为准（pointerWithin），指针落在行间缝隙时
// 才回落最近行（closestCenter），避免默认的 closestCenter 在行边界吸附到邻近目录。
const collision: CollisionDetection = (args) => {
  const hits = pointerWithin(args);
  return hits.length > 0 ? hits : closestCenter(args);
};

// 分页规格与全站一致：默认 20 条/页，可选 20/50/100。
const DEFAULT_PAGE_SIZE = 20;
const PAGE_SIZE_OPTIONS = [20, 50, 100];

// 视图模式：文档浏览（列表 + 筛选）与语义检索（命中列表）是两个页级视图，
// 用共享 Tabs 承载，不把检索塞进 FilterBar 的筛选条件里。
type ViewMode = 'docs' | 'search';
// 语义检索的范围模式：当前目录（跟随浏览上下文）/ 全库 / 按助理知识范围
type ScopeMode = 'dir' | 'all' | 'agent';

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

// 行首类型标签：来源类型优先，上传文档按文件名后缀细分。
// 用 Chip 的语义色区分文件格式，不用多色图标；来源胶囊仍保持中性。
function docKindLabel(doc: KnowledgeDoc): string {
  if (doc.source_type === 'url') return 'URL';
  if (doc.source_type === 'git') return 'GIT';
  if (doc.source_type === 'repo') return 'REPO';
  // 只有上传文档带原件名；手动 / 内置文档的正文本身就是 markdown
  const fileName = doc.source_filename;
  if (!fileName) return 'MD';
  const ext = fileName.split('.').pop()?.toLowerCase() ?? '';
  if (ext === 'pdf') return 'PDF';
  if (ext === 'doc' || ext === 'docx') return 'DOC';
  if (ext === 'txt') return 'TXT';
  if (ext === 'md' || ext === 'markdown') return 'MD';
  // 未知后缀不能硬猜成 MD：给中性标签，避免格式信息失真
  return 'FILE';
}

// 常见格式各占一个语义色；外部源 / 未知格式属于来源或兜底，保持中性
const DOC_KIND_TONE: Record<string, 'accent' | 'info' | 'warning' | 'success' | 'default'> = {
  MD: 'accent',
  TXT: 'info',
  PDF: 'warning',
  DOC: 'success',
  REPO: 'default',
  URL: 'default',
  GIT: 'default',
  FILE: 'default',
};

// 列表只回传正文 UTF-8 字节数，按 lib/format 的全站口径展示
function formatDocBytes(bytes: number | undefined): string {
  return bytes === undefined ? '—' : formatBytes(bytes);
}

function sourceLabel(doc: KnowledgeDoc, tr: (zh: string, en: string) => string): string {
  if (doc.source_type === 'vault') return tr('内置', 'built-in');
  if (doc.source_type === 'upload') return tr('上传', 'upload');
  // URL / Git 文档同属外部同步；类型已由行首 GIT / URL 标签表达，这里不重复
  if (doc.source_type === 'url' || doc.source_type === 'git') return tr('外部源', 'external');
  if (doc.source_type === 'repo') return 'repo';
  return tr('手动', 'manual');
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
  // 筛选条件会连续变化：路数守卫保证只有最新一次请求的结果落进状态
  const reqSeq = useRequestSeq();
  // 当前页的文档（服务端筛选 + 分页后的一页）
  const [items, setItems] = useState<KnowledgeDoc[]>([]);
  const [group, setGroup] = useState<SourceGroup>('org');
  const [activePath, setActivePath] = useState(''); // '' = 整个分组
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // 打开的文档弹窗：'create' 新建；其余为某篇文档，mode 决定是查看还是编辑。
  const [docModal, setDocModal] = useState<{ doc: KnowledgeDoc | 'create'; mode: 'create' | 'edit' | 'view' } | null>(null);
  const [deleting, setDeleting] = useState<KnowledgeDoc | null>(null);
  // 文档标题筛选（服务端 q 过滤）
  const [titleQuery, setTitleQuery] = useState('');
  // 语义检索是独立功能，不是筛选条件：单独的输入框 + 触发按钮
  const [searchQuery, setSearchQuery] = useState('');
  const [hits, setHits] = useState<SearchHit[]>([]);
  // 视图模式：文档浏览 / 语义检索
  const [view, setView] = useState<ViewMode>('docs');
  // 服务端分页；默认 20 条/页
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [total, setTotal] = useState(0);
  // 来源筛选（空串 = 全部来源）；与侧边栏分组正交，只落在组织文档这一侧
  const [sourceFilter, setSourceFilter] = useState<DocSource | ''>('');
  // 更新时间排序方向；默认按最近更新在前
  const [updatedSort, setUpdatedSort] = useState<'desc' | 'asc'>('desc');
  // 已完成语义检索的关键词；非 null 时右侧进入结果态
  const [searchedQuery, setSearchedQuery] = useState<string | null>(null);
  // 打开文档时定位到的命中块；关闭编辑器即清空
  const [locate, setLocate] = useState<{ chunkIndex: number; excerpt: string } | null>(null);
  // 试调旋钮（默认折叠）：返回条数 / RRF 阈值覆盖（空串 = 全局阈值）/ 范围模式
  const [tuningOpen, setTuningOpen] = useState(false);
  const [limit, setLimit] = useState(10);
  const [minScoreText, setMinScoreText] = useState('');
  const [scopeMode, setScopeMode] = useState<ScopeMode>('dir');
  const [agentName, setAgentName] = useState('');
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  // 检索请求在途：空态不误报「没有命中」，计数区显示检索中
  const [searching, setSearching] = useState(false);
  // 目录树按来源组聚合，各树计数互不混算
  const [orgPaths, setOrgPaths] = useState<PathRow[]>([]);
  const [builtinPaths, setBuiltinPaths] = useState<PathRow[]>([]);
  const [orgTotal, setOrgTotal] = useState(0);
  const [builtinTotal, setBuiltinTotal] = useState(0);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  // 语义检索自己的请求序号：与文档列表互不干扰，避免旧检索结果覆盖新结果
  const searchSeq = useRef(0);
  // 拖拽中的文档（DragOverlay 显示标题胶囊）；null = 未在拖拽
  const [dragDoc, setDragDoc] = useState<KnowledgeDoc | null>(null);
  // 拖拽在移动 6px 后才激活，避免行内点击被误判成拖拽
  const dndSensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));

  // 文档列表：服务端筛选（来源组 / 目录 / 来源类型 / 标题关键字）+ 分页
  const fetchList = useCallback(async (silent = false) => {
    const current = reqSeq.next();
    if (silent) setRefreshing(true);
    else setLoading(true);
    try {
      const res = await listDocs({
        builtin: group === 'builtin',
        path_prefix: activePath === '' ? undefined : activePath,
        source_type: group === 'org' && sourceFilter !== '' ? sourceFilter : undefined,
        q: titleQuery.trim() === '' ? undefined : titleQuery.trim(),
        sort_dir: updatedSort,
        limit: pageSize,
        offset: page * pageSize,
      });
      if (reqSeq.isStale(current)) return;
      setItems(res.items);
      setTotal(res.total);
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
  }, [group, activePath, sourceFilter, titleQuery, updatedSort, page, pageSize, reqSeq]);

  // 目录树计数与分组总数：按来源组分别聚合，不再一次拉回全部文档
  const fetchTrees = useCallback(async () => {
    try {
      const [org, builtin] = await Promise.all([
        listPaths(false),
        listPaths(true),
      ]);
      setOrgPaths(org.items);
      setBuiltinPaths(builtin.items);
      setOrgTotal(org.total);
      setBuiltinTotal(builtin.total);
      setErr(null);
    } catch (e) {
      setErr(errorText(e));
    }
  }, []);

  useEffect(() => {
    // 与搜索共享同一 250ms 防抖：输入过程中不打满后端。
    const timer = window.setTimeout(() => {
      void fetchList();
    }, 250);
    return () => window.clearTimeout(timer);
  }, [fetchList]);

  useEffect(() => {
    void fetchTrees();
  }, [fetchTrees]);

  // 助理下拉数据：按需拉取（展开试调时才请求），按助理范围检索取的是它真实的 knowledge_scope
  const [agentsLoaded, setAgentsLoaded] = useState(false);
  useEffect(() => {
    if (!tuningOpen || agentsLoaded) return;
    let cancelled = false;
    void listAgents()
      .then((res) => {
        if (!cancelled) {
          setAgents(res.items ?? []);
          setAgentsLoaded(true);
        }
      })
      .catch((e) => {
        if (!cancelled) setErr(errorText(e));
      });
    return () => {
      cancelled = true;
    };
  }, [tuningOpen, agentsLoaded]);



  const treeFrom = useCallback((rows: PathRow[]): TreeNode => {
    return buildTree(rows);
  }, []);

  const builtinTree = useMemo(() => treeFrom(builtinPaths), [builtinPaths, treeFrom]);
  const orgTree = useMemo(() => treeFrom(orgPaths), [orgPaths, treeFrom]);

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
        // 新文档按更新时间排在首页：停在后面的分页会看不到刚上传的内容
        setPage(0);
        await Promise.all([fetchList(true), fetchTrees()]);
        flash('ok', tr(`已上传 ${files.length} 个文件`, `Uploaded ${files.length} file(s)`));
      } catch (e) {
        setErr(errorText(e));
      } finally {
        setUploading(false);
        if (fileInputRef.current) fileInputRef.current.value = '';
      }
    },
    [fetchList, fetchTrees, pickFolder, tr],
  );

  // 下载上传原件：blob 落成临时链触发浏览器保存；文件名取自 Content-Disposition
  const downloadSource = useCallback(
    async (doc: KnowledgeDoc) => {
      try {
        const { blob, filename } = await downloadDocSource(doc.id);
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = filename || doc.source_filename || 'download';
        anchor.click();
        URL.revokeObjectURL(url);
        flash('ok', tr(`已开始下载 ${filename}`, `Downloading ${filename}`));
      } catch (e) {
        flash('err', tr(`下载失败：${errorText(e)}`, `Download failed: ${errorText(e)}`));
      }
    },
    [tr],
  );

  const onMoveDoc = useCallback(
    async (docId: string, targetPath: string) => {
      setErr(null);
      try {
        await moveDoc(docId, targetPath);
        await Promise.all([fetchList(true), fetchTrees()]);
      } catch (e) {
        setErr(errorText(e));
      }
    },
    [fetchList, fetchTrees],
  );

  // dnd-kit 生命周期：起拖记住文档给 DragOverlay，松手落在目录上才真正移动。
  const onDragStart = useCallback((event: DragStartEvent) => {
    const docId = String(event.active.id).slice('doc:'.length);
    setDragDoc(items.find((d) => d.id === docId) ?? null);
  }, [items]);

  const onDragEnd = useCallback(
    (event: DragEndEvent) => {
      setDragDoc(null);
      const overId = event.over?.id;
      if (typeof overId !== 'string') return;
      const docId = String(event.active.id).slice('doc:'.length);
      if (docId !== '') void onMoveDoc(docId, folderPathOf(overId));
    },
    [onMoveDoc],
  );

  // 文档启停只影响检索：停用后切块保留，可随时恢复
  const onToggleDocEnabled = useCallback(
    async (doc: KnowledgeDoc, enabled: boolean) => {
      setErr(null);
      try {
        const saved = await setDocEnabled(doc.id, enabled);
        // PATCH 返回的是 wire 文档（不含列表统计）：合并而不是整体覆盖，
        // 否则启停一次就会把分块数、正文大小与索引状态抹成「—」
        setItems((prev) => prev.map((d) => (d.id === saved.id ? { ...d, ...saved } : d)));
        flash('ok', enabled ? tr('已启用，重新参与检索', 'Enabled for retrieval') : tr('已停用，不再参与检索', 'Excluded from retrieval'));
      } catch (e) {
        setErr(errorText(e));
      }
    },
    [tr],
  );

  // 按助理范围检索的 wire 形态；空范围不传，与后端「空 = 不限」口径一致
  const searchScopeWire = useMemo(() => {
    if (scopeMode !== 'agent') return undefined;
    const scope = agents.find((a) => a.name === agentName)?.knowledge_scope;
    if (!scope || (scope.paths.length === 0 && scope.doc_ids.length === 0)) return undefined;
    return { paths: [...scope.paths], doc_ids: [...scope.doc_ids] };
  }, [scopeMode, agentName, agents]);

  // 语义检索：独立功能，不是筛选条件。范围默认跟随浏览上下文（来源组 + 当前目录），
  // 可在试调里切成全库或按助理知识范围
  const runSearch = async () => {
    const current = ++searchSeq.current;
    const nextQuery = searchQuery.trim();
    if (nextQuery === '') {
      setHits([]);
      setSearchedQuery(null);
      return;
    }
    // 阈值非法显式报错，不悄悄退回全局——试调就是要看到真实生效的口径
    let minScore: number | undefined;
    const raw = minScoreText.trim();
    if (raw !== '') {
      minScore = Number(raw);
      if (!Number.isFinite(minScore) || minScore < 0) {
        setErr(tr('RRF 阈值必须是 >= 0 的数字', 'RRF threshold must be a number >= 0'));
        return;
      }
    }
    setErr(null);
    setLocate(null);
    // 提交即记录查询词并清掉旧结果：请求在途显示「检索中」，空态不会挂着上一次的查询词
    setSearchedQuery(nextQuery);
    setHits([]);
    setSearching(true);
    try {
      const res = await searchKnowledge(nextQuery, {
        limit,
        pathPrefix: scopeMode === 'dir' ? activePath || undefined : undefined,
        builtin: scopeMode === 'dir' ? group === 'builtin' : undefined,
        // 来源筛选只在组织文档下有意义；内置组的所有文档都是 vault
        sourceType: group === 'org' && sourceFilter !== '' ? sourceFilter : undefined,
        scope: searchScopeWire,
        minScore,
      });
      // 连续检索时只有最后一次结果可以落到界面上
      if (searchSeq.current !== current) return;
      setHits(res.items ?? []);
      setSearching(false);
    } catch (e) {
      if (searchSeq.current !== current) return;
      setSearching(false);
      setErr(errorText(e));
    }
  };

  const resetSearch = useCallback(() => {
    searchSeq.current += 1;
    setSearchQuery('');
    setHits([]);
    setSearchedQuery(null);
    setSearching(false);
    setLocate(null);
  }, []);

  // 筛选条件变化时回到第 1 页
  useEffect(() => {
    setPage(0);
  }, [group, activePath, sourceFilter, titleQuery, updatedSort, searchedQuery, pageSize]);
  // 删除或筛选后页数变少，把页码夹回最后一页，避免停在空白页
  useEffect(() => {
    const lastPage = Math.max(0, Math.ceil(total / pageSize) - 1);
    if (page > lastPage) setPage(lastPage);
  }, [total, pageSize, page]);

  useAssistantPage('知识库', 'Knowledge', {
    source: group,
    folder: activePath,
    visible_documents: total,
    sample_document_ids: items.slice(0, 6).map((doc) => doc.id).join(', '),
    loading,
    scope_mode: scopeMode,
    has_min_score: minScoreText.trim() !== '',
    search_hits: hits.length,
  });

  // 检索面板占位符随范围模式变化，让「搜的是谁的知识」始终可见
  const searchScopeLabel =
    scopeMode === 'all'
      ? tr('全库', 'entire knowledge base')
      : scopeMode === 'agent'
        ? (() => {
            const agent = agents.find((a) => a.name === agentName);
            const label = agent ? `${agent.display_name}（${agent.name}）` : tr('未选助理', 'none');
            return searchScopeWire
              ? tr(`助理 ${label} 的知识范围`, `assistant ${label} scope`)
              : tr(`助理 ${label} · 空范围 = 全库`, `assistant ${label} · empty scope = all`);
          })()
        : activePath
          ? tr(`「${localizedPath(activePath)}」`, `"${localizedPath(activePath)}"`)
          : group === 'builtin'
            ? tr('内置知识库', 'built-in knowledge base')
            : tr('组织知识库', 'organization knowledge');

  // 来源筛选：组织文档里 manual / upload / URL / Git 混在一起，检索与列表都要能按来源收口；
  // 内置组只有 vault，给筛选反而制造「选了也一样」的假控件，因此不显示。
  const sourceOptions = [
    { value: '', label: tr('全部来源', 'All sources') },
    { value: 'manual', label: tr('手动', 'Manual') },
    { value: 'upload', label: tr('上传', 'Uploaded') },
    { value: 'url', label: 'URL' },
    { value: 'git', label: 'Git' },
    { value: 'repo', label: 'repo' },
  ];

  // 目录条件来自左栏，没有就地清除入口；放到筛选卡第二行统一撤销
  const resetFilters = useCallback(() => {
    setActivePath('');
    setTitleQuery('');
    setSourceFilter('');
    setPage(0);
  }, []);

  const selectedSourceLabel =
    sourceOptions.find((option) => option.value === sourceFilter)?.label ?? sourceFilter;
  const hasActiveFilters =
    activePath !== '' || sourceFilter !== '' || titleQuery.trim() !== '';

  // 筛选卡只承载「文档标题」筛选；语义检索走独立的检索视图
  return (
    <DndContext sensors={dndSensors} collisionDetection={collision} onDragStart={onDragStart} onDragEnd={onDragEnd}>
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      {/* Tabs 只包页头：视图切换的标签栏与页头共用分隔线，样式与 PageHeader.navigation 一致。 */}
      <Tabs value={view} onValueChange={(v) => setView(v as ViewMode)} className="contents">
        <PageHeader
          title={tr('知识库', 'Knowledge base')}
          subtitle={tr(
            `共 ${orgTotal + builtinTotal} 篇 · 组织 ${orgTotal} · 内置 ${builtinTotal}`,
            `${orgTotal + builtinTotal} docs · ${orgTotal} org · ${builtinTotal} built-in`,
          )}
          navigation={
            // 文档浏览与语义检索是两个页级视图，用共享 Tabs 承载；
            // 语义检索因此是「功能」而不是筛选卡里的一个条件。
            <TabsList aria-label={tr('知识库视图', 'Knowledge views')}>
              <TabsTrigger value="docs">{tr('文档', 'Documents')}</TabsTrigger>
              <TabsTrigger value="search">{tr('语义检索', 'Semantic search')}</TabsTrigger>
            </TabsList>
          }
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
                onClick={() => void Promise.all([fetchList(true), fetchTrees()])}
              >
                {tr('刷新', 'Refresh')}
              </RowAction>
            </>
          }
        />
      </Tabs>

      {view === 'search' ? (
        <>
          {err && (
            <div className="mx-6 mt-3">
              <ErrorBanner>{err}</ErrorBanner>
            </div>
          )}
          <SemanticSearchPanel
            query={searchQuery}
            onQueryChange={setSearchQuery}
            onRun={() => void runSearch()}
            onReset={resetSearch}
            searchedQuery={searchedQuery}
            hits={hits}
            scopeLabel={searchScopeLabel}
            searching={searching}
            extraControls={
              // 试调参数默认折叠：日常检索只看检索框，需要调口径时再展开
              <ChoiceChip
                active={tuningOpen}
                label={tr('试调', 'Tuning')}
                onClick={() => setTuningOpen((open) => !open)}
              />
            }
            footer={
              tuningOpen ? (
                <>
                  <FilterPill
                    label={tr('返回条数', 'Limit')}
                    active={limit !== 10}
                    onClear={() => setLimit(10)}
                    width="w-[8rem]"
                  >
                    <Select
                      aria-label={tr('返回条数', 'Limit')}
                      value={String(limit)}
                      onValueChange={(v) => setLimit(Number(v))}
                      options={[
                        { value: '5', label: '5' },
                        { value: '10', label: '10' },
                        { value: '20', label: '20' },
                      ]}
                      variant="ghost"
                      className="h-7 min-w-0 border-0 bg-transparent px-0 text-[12px] shadow-none"
                    />
                  </FilterPill>
                  <FilterPill
                    label={tr('RRF 阈值', 'RRF min')}
                    active={minScoreText.trim() !== ''}
                    onClear={() => setMinScoreText('')}
                    width="w-[7.5rem]"
                  >
                    <Input
                      type="number"
                      min={0}
                      step="any"
                      value={minScoreText}
                      onChange={(e) => setMinScoreText(e.target.value)}
                      placeholder={tr('全局', 'global')}
                      aria-label={tr('RRF 阈值覆盖', 'RRF threshold override')}
                      className="h-7 w-full min-w-0 border-0 bg-transparent px-0 text-[12px]"
                    />
                  </FilterPill>
                  <FilterPill
                    label={tr('范围', 'Scope')}
                    active={scopeMode !== 'dir'}
                    onClear={() => {
                      setScopeMode('dir');
                      setAgentName('');
                    }}
                    width="w-[9.5rem]"
                  >
                    <Select
                      aria-label={tr('范围模式', 'Scope mode')}
                      value={scopeMode}
                      onValueChange={(v) => {
                        setScopeMode(v as ScopeMode);
                        setAgentName('');
                      }}
                      options={[
                        { value: 'dir', label: tr('当前目录', 'Current folder') },
                        { value: 'all', label: tr('全库', 'Entire base') },
                        { value: 'agent', label: tr('按助理范围', 'By assistant') },
                      ]}
                      variant="ghost"
                      className="h-7 min-w-0 border-0 bg-transparent px-0 text-[12px] shadow-none"
                    />
                  </FilterPill>
                  {scopeMode === 'agent' && (
                    <FilterPill
                      label={tr('助理', 'Assistant')}
                      active={agentName !== ''}
                      onClear={() => setAgentName('')}
                      width="w-[11rem]"
                    >
                      <Select
                        aria-label={tr('助理', 'Assistant')}
                        value={agentName}
                        onValueChange={setAgentName}
                        options={[
                          { value: '', label: tr('选择助理…', 'Pick an assistant…') },
                          ...agents.map((a) => ({ value: a.name, label: `${a.display_name}（${a.name}）` })),
                        ]}
                        variant="ghost"
                        className="h-7 min-w-0 border-0 bg-transparent px-0 text-[12px] shadow-none"
                      />
                    </FilterPill>
                  )}
                </>
              ) : null
            }
            onOpenHit={(hit) => {
              setLocate({ chunkIndex: hit.chunk_index, excerpt: hit.excerpt });
              setDocModal({ doc: hit.doc, mode: 'edit' });
            }}
          />
        </>
      ) : (
        <>
          <FilterBar
            className="mx-6 mt-3"
            footer={
              hasActiveFilters ? (
                <>
                  {activePath !== '' ? (
                    <FilterChip
                      label={tr(`目录：${localizedPath(activePath)}`, `Folder: ${localizedPath(activePath)}`)}
                      onClear={() => setActivePath('')}
                    />
                  ) : null}
                  {sourceFilter !== '' ? (
                    <FilterChip
                      label={tr(`来源：${selectedSourceLabel}`, `Source: ${sourceFilter}`)}
                      onClear={() => {
                        setSourceFilter('');
                        setPage(0);
                      }}
                    />
                  ) : null}
                  <Button variant="ghost" size="sm" className="ml-auto" onClick={resetFilters}>
                    {tr('清除全部', 'Clear all')}
                  </Button>
                </>
              ) : null
            }
          >
            <SearchInput
              value={titleQuery}
              onChange={(value) => {
                setTitleQuery(value);
                setPage(0);
              }}
              placeholder={tr('按文档标题筛选…', 'Filter by document title…')}
              ariaLabel={tr('按标题筛选', 'Filter by title')}
            />
            {group === 'org' && (
              <FilterPill
                label={tr('来源', 'Source')}
                active={sourceFilter !== ''}
                onClear={() => {
                  setSourceFilter('');
                  setPage(0);
                }}
                width="w-[8.5rem]"
              >
                <Select
                  aria-label={tr('来源', 'Source')}
                  value={sourceFilter}
                  onValueChange={(v) => {
                    setSourceFilter(v as DocSource | '');
                    setPage(0);
                  }}
                  options={sourceOptions}
                  variant="ghost"
                  className="h-7 min-w-0 border-0 bg-transparent px-0 text-[12px] shadow-none"
                />
              </FilterPill>
            )}
            <span className="ml-auto shrink-0 pr-1 text-[11px] tabular-nums text-zinc-500">
              {tr(`共 ${total} 篇`, `${total} docs`)}
            </span>
          </FilterBar>

            <div className="flex min-h-0 flex-1 overflow-hidden">
        <aside className="hidden w-[15rem] shrink-0 flex-col overflow-y-auto border-r border-zinc-800/60 px-2 py-3 md:flex">
          <SourceGroupSection
            group="org"
            label={tr('组织知识库', 'Organization')}
            icon={FileText}
            tree={orgTree}
            total={orgTotal}
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
                      setDocModal({ doc: 'create', mode: 'create' });
                    }}
                  />
                </>
              )
            }
          />
          <div className="mx-2 my-2 h-px bg-zinc-800/60" />
          <SourceGroupSection
            group="builtin"
            label={tr('内置知识库', 'Built-in')}
            icon={BookOpen}
            tree={builtinTree}
            total={builtinTotal}
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
              <ErrorBanner>{err}</ErrorBanner>
            </div>
          )}

          {loading ? (
            <div className="flex h-40 items-center justify-center text-[11px] text-zinc-500">{tr('加载中…', 'Loading…')}</div>
          ) : err !== null && total === 0 ? null : total === 0 ? (
            <EmptyStateCard
              icon={BookOpen}
              title={
                // 有筛选条件时是「没匹配上」，没有条件才是「这个分组真的空」
                titleQuery.trim() !== '' || sourceFilter !== ''
                  ? tr('没有匹配的文档', 'No matching documents')
                  : activePath
                    ? tr(`「${localizedPath(activePath)}」里还没有文档`, `No docs in "${localizedPath(activePath)}" yet`)
                    : group === 'builtin'
                      ? tr('内置知识库为空', 'Built-in knowledge base is empty')
                      : tr('组织知识库还没有内容', 'No organization docs yet')
              }
              hint={
                titleQuery.trim() !== '' || sourceFilter !== ''
                  ? tr('换个关键字或清除筛选条件。', 'Try a different keyword or clear filters.')
                  : !canMutate
                    ? tr('请联系管理员添加或同步知识库。', 'Ask an admin to add or sync knowledge.')
                    : undefined
              }
              action={
                titleQuery.trim() !== '' || sourceFilter !== '' ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setTitleQuery('');
                      setSourceFilter('');
                    }}
                  >
                    {tr('清除筛选', 'Clear filters')}
                  </Button>
                ) : canMutate ? (
                  <div className="flex items-center gap-2">
                    <Button variant="outline" size="sm" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
                      <Upload size={12} /> {tr('上传文件', 'Upload')}
                    </Button>
                    <Button variant="primary" size="sm" onClick={() => setDocModal({ doc: 'create', mode: 'create' })}>
                      <Plus size={12} /> {tr('新建文档', 'New doc')}
                    </Button>
                  </div>
                ) : null
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
                  loading={refreshing}
                  onPageChange={setPage}
                  pageSizeOptions={PAGE_SIZE_OPTIONS}
                  onPageSizeChange={(size) => {
                    setPage(0);
                    setPageSize(size);
                  }}
                />
              }
            >
                <table {...tableLayout(64)}>
                  <colgroup>
                    <col className={COL.handle} />
                    <col className={COL.main} />
                    <col className={COL.time} />
                    <col className={COL.detail} />
                    <col className={COL.tag} />
                    <col className={COL.actionsIconWide} />
                  </colgroup>
                  <TableHead>
                    <tr className="whitespace-nowrap">
                      <th scope="col" className="px-2 py-2">
                        <span className="sr-only">{tr('拖动把手', 'Drag handle')}</span>
                      </th>
                      <th scope="col" className="px-3 py-2 text-left font-medium">{tr('文档', 'Document')}</th>
                      <SortableHead
                        label={tr('更新时间', 'Updated')}
                        active
                        order={updatedSort}
                        onClick={() => {
                          setPage(0);
                          setUpdatedSort((d) => (d === 'desc' ? 'asc' : 'desc'));
                        }}
                      />
                      <th scope="col" className="px-3 py-2 text-left font-medium">{tr('索引', 'Index')}</th>
                      <th scope="col" className="px-3 py-2 text-left font-medium">{tr('参与检索', 'Retrieval')}</th>
                      <th scope="col" className="px-3 py-2 text-right font-medium">{tr('操作', 'Actions')}</th>
                    </tr>
                  </TableHead>
                <tbody className="divide-y divide-zinc-800/40">
                  {items.map((doc) => (
                    <DocRow
                      key={doc.id}
                      doc={doc}
                      canMutate={canMutate}
                      onView={() => {
                        setLocate(null);
                        setDocModal({ doc, mode: 'view' });
                      }}
                      onEdit={() => {
                        setLocate(null);
                        setDocModal({ doc, mode: 'edit' });
                      }}
                      onDelete={() => setDeleting(doc)}
                      onDownload={() => void downloadSource(doc)}
                      onToggleEnabled={(enabled) => void onToggleDocEnabled(doc, enabled)}
                    />
                  ))}
                </tbody>
              </table>
            </TableCard>
          )}
          </section>
          </div>
        </>
      )}

      {docModal && (
        <DocEditor
          mode={docModal.mode}
          existing={docModal.doc === 'create' ? null : docModal.doc}
          defaultPath={docModal.doc === 'create' ? activePath : undefined}
          locate={locate}
          onClose={() => {
            setDocModal(null);
            setLocate(null);
          }}
          onSaved={() => {
            setDocModal(null);
            setLocate(null);
            void Promise.all([fetchList(true), fetchTrees()]);
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
            try {
              await deleteDoc(deleting.id);
              flash('ok', tr('文档已删除', 'Document deleted'));
              setDeleting(null);
              await Promise.all([fetchList(true), fetchTrees()]);
            } catch (e) {
              // 保持对话框打开，并把失败原因显式反馈，而不是让异常静默冒泡
              setErr(errorText(e));
              flash('err', errorText(e));
            }
          }}
          onClose={() => setDeleting(null)}
        />
      )}

    </main>
    {/* 拖拽预览只保留标题胶囊，避免把整行的开关与操作按钮一起拎起 */}
    <DragOverlay dropAnimation={null}>
      {dragDoc !== null && (
        <div className="max-w-[280px] truncate rounded-lg border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-[11.5px] font-medium text-zinc-100 shadow-xl">
          {localizedDocTitle(dragDoc)}
        </div>
      )}
    </DragOverlay>
    </DndContext>
  );
}

// 来源分组：分组头本身是「查看整个分组」的入口，也是拖放到根目录的放置区。
function SourceGroupSection({
  group,
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
  group: 'org' | 'builtin';
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
  // 根目录是有效落点（移动到分组根 = 清空目录）；无 onMoveDoc 的组不接受放置。
  const { setNodeRef, isOver } = useDroppable({ id: folderDropId(group, ''), disabled: !onMoveDoc });
  const rootActive = active && activePath === '';

  return (
    <div>
      <div className="flex items-center gap-0.5">
        <button
          ref={setNodeRef}
          type="button"
          onClick={() => onPick('')}
          className={cn(
            'relative flex h-7 min-w-0 flex-1 items-center gap-2 rounded-md px-2 text-left transition-colors',
            isOver
              ? 'bg-info/10 ring-1 ring-inset ring-info/70'
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
          <span className="shrink-0 font-sans text-[10px] tabular-nums text-zinc-500">{total}</span>
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
              group={group}
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
  group,
  activePath,
  onPick,
  onMoveDoc,
}: {
  node: TreeNode;
  depth: number;
  group: 'org' | 'builtin';
  activePath: string;
  onPick(path: string): void;
  onMoveDoc?: (docId: string, path: string) => void;
}) {
  const { tr } = useI18n();
  const isActive = activePath === node.path;
  const isAncestor = activePath.startsWith(`${node.path}/`);
  const [expanded, setExpanded] = useState(isActive || isAncestor);
  const { setNodeRef, isOver } = useDroppable({ id: folderDropId(group, node.path), disabled: !onMoveDoc });
  const hasChildren = node.children.size > 0;
  const ChevronIcon = expanded ? FolderOpen : Folder;

  useEffect(() => {
    if (isActive || isAncestor) setExpanded(true);
  }, [isActive, isAncestor]);

  return (
    <div>
      <div
        ref={setNodeRef}
        style={{ paddingLeft: 6 + depth * 12 }}
        className={cn(
          'relative flex items-center gap-0.5 rounded-md pr-1 transition-colors',
          isOver
            ? 'bg-info/10 ring-1 ring-inset ring-info/70'
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
              'ml-auto shrink-0 font-sans text-[10px] tabular-nums',
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
                group={group}
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

// 文档行：主信息一行（类型字形 + 标题），元信息一行（来源 · 目录 · 标签 · 地址）；
// 状态由 StatusPill 表达并兼作开关；整行不响应点击，看详情只点标题。
function DocRow({
  doc,
  canMutate,
  onView,
  onEdit,
  onDelete,
  onDownload,
  onToggleEnabled,
}: {
  doc: KnowledgeDoc;
  canMutate: boolean;
  onView(): void;
  onEdit(): void;
  onDelete(): void;
  onDownload(): void;
  onToggleEnabled(enabled: boolean): void;
}) {
  const { tr } = useI18n();
  const editable = doc.source_type === 'manual' || doc.source_type === 'upload';
  const title = localizedDocTitle(doc);
  const kind = docKindLabel(doc);
  const tags = doc.tags ?? [];
  const chunkCount = doc.chunk_count;
  // 0 块 = 正文从未成功索引；字段缺省（详情 / 检索结果）时不给状态，只显示 —
  const indexTone: 'ok' | 'warn' | null = chunkCount === undefined ? null : chunkCount > 0 ? 'ok' : 'warn';

  // 拖拽把手（@dnd-kit）：拖动预览由页面级 DragOverlay 提供标题胶囊，
  // 不再手搓幽灵 DOM + setDragImage。仅手动/上传文档可拖（同编辑权限）。
  const { listeners, setNodeRef } = useDraggable({ id: `doc:${doc.id}`, disabled: !editable });

  return (
    <tr className="group">
      <td className="relative w-6 py-2 pl-2 pr-0">
        {/* hover 时行首 2px 条与左栏目录选中态呼应，指明当前扫读的行 */}
        <span
          aria-hidden
          className="absolute inset-y-1 left-0 hidden w-0.5 rounded-full bg-indigo-400 group-hover:block group-focus-within:block"
        />
        {editable ? (
          <span
            ref={setNodeRef}
            {...listeners}
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
      <td className="px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <Chip
            dense
            tone={DOC_KIND_TONE[kind] ?? 'default'}
            aria-label={tr(`文档类型 ${kind}`, `Document type ${kind}`)}
            title={tr(`文档类型 ${kind}`, `Document type ${kind}`)}
            className="inline-flex min-w-[24px] shrink-0 items-center justify-center font-sans"
          >
            {kind}
          </Chip>
          <button
            type="button"
            onClick={onView}
            className={cn(
              'truncate text-[12.5px] font-medium transition-colors hover:text-indigo-300',
              doc.enabled ? 'text-zinc-100' : 'text-zinc-300',
            )}
            title={title}
          >
            {title}
          </button>
        </div>
        {/* 元信息按「来源 · 目录 · 标签 · 地址」分组，保持单行，避免行高随内容跳动 */}
        <div className="mt-0.5 flex min-w-0 items-center gap-1.5 overflow-hidden pl-8 text-[10.5px] leading-4">
          <Chip dense className="shrink-0">{sourceLabel(doc, tr)}</Chip>
          <span aria-hidden className="shrink-0 text-zinc-600">·</span>
          {doc.path ? (
            <span className="inline-flex min-w-0 shrink items-center gap-1 text-zinc-500" title={localizedPath(doc.path)}>
              <Folder size={10} className="shrink-0 text-zinc-600" aria-hidden />
              <span className="truncate">{localizedPath(doc.path)}</span>
            </span>
          ) : (
            <span className="shrink-0 text-zinc-600">{tr('未归目录', 'No folder')}</span>
          )}
          {tags.length > 0 && (
            <>
              <span aria-hidden className="shrink-0 text-zinc-600">·</span>
              <span className="flex shrink-0 items-center gap-1">
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
              </span>
            </>
          )}
          {(doc.source_filename || doc.url) && (
            <>
              <span aria-hidden className="shrink-0 text-zinc-600">·</span>
              {/* 大小统一由索引列展示，元信息行只保留原件名 / URL 与 MIME；
                  文件名允许中文，不套等宽字体（中文会回落成异形字形） */}
              <span
                className="min-w-0 flex-1 truncate text-zinc-600"
                title={
                  doc.source_filename
                    ? [doc.source_filename, doc.source_mime].filter(Boolean).join(' · ')
                    : doc.url
                }
              >
                {doc.source_filename || doc.url}
              </span>
            </>
          )}
        </div>
      </td>
      <td className="px-3 py-2">
        {/* 完整日期时间：相对时间无法核对与排序，列表里一律不用 */}
        <span className="whitespace-nowrap text-[11px] tabular-nums text-zinc-500">
          {fullDateTime(doc.updated_at)}
        </span>
      </td>
      <td className="px-3 py-2">
        {/* 索引可观测性：分块数 + 文件大小，异常（0 块）用状态色说话 */}
        <div
          className="flex items-center gap-1.5 whitespace-nowrap text-[11px] tabular-nums text-zinc-400"
          title={[
            doc.content_chars === undefined
              ? ''
              : tr(
                  // tooltip 给精确数值，不再重复可见列里已经格式化过的大小
                  `${doc.content_chars} 字符 · ${doc.content_bytes ?? 0} 字节`,
                  `${doc.content_chars} chars · ${doc.content_bytes ?? 0} bytes`,
                ),
            doc.indexed_at
              ? tr(`最后索引 ${fullDateTime(doc.indexed_at)}`, `Indexed ${fullDateTime(doc.indexed_at)}`)
              : tr('尚无索引记录', 'No index record'),
            doc.last_hit_at
              ? tr(`最近命中 ${fullDateTime(doc.last_hit_at)}`, `Last hit ${fullDateTime(doc.last_hit_at)}`)
              : tr('尚未被检索命中', 'Never hit'),
          ]
            .filter(Boolean)
            .join(' · ')}
        >
          <span>{chunkCount === undefined ? '—' : tr(`${chunkCount} 块`, `${chunkCount} chunks`)}</span>
          <span aria-hidden className="text-zinc-600">·</span>
          <span>{formatDocBytes(doc.content_bytes)}</span>
        </div>
        {indexTone && (
          <div className="mt-0.5 flex items-center gap-1.5">
            <StatusPill
              tone={indexTone}
              label={indexTone === 'ok' ? tr('已索引', 'Indexed') : tr('未索引', 'Not indexed')}
            />
            {doc.hit_count !== undefined && doc.hit_count > 0 && (
              <span className="whitespace-nowrap text-[10px] tabular-nums text-zinc-600">
                {tr(`${doc.hit_count} 次命中`, `${doc.hit_count} hits`)}
              </span>
            )}
          </div>
        )}
      </td>
      <td className="px-3 py-2">
        {/* 状态用语义色说话，胶囊本身即开关，不再并排放一个裸 Switch */}
        <button
          type="button"
          role="switch"
          aria-checked={doc.enabled}
          aria-label={tr(`参与检索 ${title}`, `Include ${title} in retrieval`)}
          disabled={!canMutate}
          onClick={() => onToggleEnabled(!doc.enabled)}
          title={tr('点击切换是否参与检索', 'Toggle retrieval')}
          className="inline-flex rounded-full transition-opacity hover:opacity-75 focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-2 focus-visible:outline-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <StatusPill
            tone={doc.enabled ? 'ok' : 'neutral'}
            label={doc.enabled ? tr('参与检索', 'Included') : tr('已停用', 'Disabled')}
          />
        </button>
      </td>
      <td className="px-3 py-2">
        <div className="flex items-center justify-end gap-0.5 opacity-60 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
          {doc.source_type === 'upload' && doc.source_filename ? (
            <RowActionIcon
              icon={Download}
              label={tr('下载源文件', 'Download source file')}
              aria-label={tr(`下载源文件 ${title}`, `Download source file ${title}`)}
              onClick={onDownload}
            />
          ) : null}
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
          ) : null}
        </div>
      </td>
    </tr>
  );
}

// 阅读态正文：先剥离 YAML frontmatter 再渲染；元信息以弱化键值行单独展示。
function DocBody({ content }: { content: string }) {
  const fm = useMemo(() => splitFrontmatter(content), [content]);
  const { resolved } = useThemeMode();
  const body = fm ? fm.body : content;
  // mermaid 围栏由 rehype-mermaid 归一为 pre.mermaid；这里按官方客户端用法
  // 调 mermaid.run 渲染，主题用 mermaid 内置主题跟随明暗模式。
  // 明暗切换时整段重挂（key）让 pre.mermaid 重新生成再渲染。
  useLayoutEffect(() => {
    let cancelled = false;
    void import('mermaid')
      .then(({ default: mermaid }) => {
        if (cancelled) return;
        mermaid.initialize({
          startOnLoad: false,
          theme: resolved === 'dark' ? 'dark' : 'default',
        });
        return mermaid.run({ querySelector: 'pre.mermaid' });
      })
      .catch(() => {
        // 渲染失败（语法错/环境缺能力）：标出来让源码可见
        document
          .querySelectorAll('pre.mermaid')
          .forEach((el) => el.classList.add('mermaid-failed'));
      });
    return () => {
      cancelled = true;
    };
  }, [resolved, body]);
  return (
    <div className="md-body text-sm text-zinc-200">
      {fm && (
        <dl className="mb-4 space-y-1 border-b border-zinc-800 pb-3">
          {fm.meta.map(([key, value]) => (
            <div key={key} className="flex items-baseline gap-2 text-xs">
              <dt className="shrink-0 font-sans text-[11px] text-zinc-500">{key}</dt>
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
      {/* remark-gfm：表格 / 删除线 / 任务列表等 GFM 语法；mermaid 围栏交给 rehype-mermaid */}
      <div key={resolved}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[[rehypeMermaid, { strategy: 'pre-mermaid' }]]}
        >
          {body}
        </ReactMarkdown>
      </div>
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
  mode: 'create' | 'edit' | 'view';
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
  const [url, setUrl] = useState(existing?.source_filename ?? existing?.url ?? '');
  const [path, setPath] = useState(existing?.path ?? defaultPath ?? '');
  const [tagsText, setTagsText] = useState((existing?.tags ?? []).join(', '));
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const locatedRef = useRef(false);

  // 内置（vault）与外部源（url / git / repo）文档随同步刷新，就地修改会被覆盖
  //（后端 PATCH 也会拒绝）；只读查看 + 「复制为组织文档」是受支持的自定义方式。
  const sourceReadOnly =
    existing?.source_type === 'repo' ||
    existing?.source_type === 'url' ||
    existing?.source_type === 'git' ||
    existing?.source_type === 'vault';
  const [forked, setForked] = useState(false);
  // 「查看」模式进来后可以就地切到编辑表单，不必关掉弹窗再点一次编辑。
  const [toEditor, setToEditor] = useState(false);
  // 走查看器的三种情况：显式查看模式；或编辑外部同步文档（内容不可改）。
  // 复制成组织文档后进入编辑表单，不再停在只读查看器。
  const viewer =
    !forked && !toEditor && (mode === 'view' || (mode === 'edit' && sourceReadOnly));
  // 后端 PATCH 不接收 url：编辑态一律只读，避免「改了却没生效」。
  // 上传文档的来源就是原件名（url 为 None），优先展示它。
  const urlReadOnly = mode !== 'create' && !forked;

  useEffect(() => {
    if (mode !== 'create' && existing && !content) {
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
    if (!locate || !content || viewer || locatedRef.current) return;
    const el = textareaRef.current;
    if (!el) return;
    const start = content.indexOf(locate.excerpt);
    if (start < 0) return;
    locatedRef.current = true;
    el.focus();
    el.setSelectionRange(start, start + locate.excerpt.length);
    const line = content.slice(0, start).split('\n').length;
    el.scrollTop = Math.max(0, (line - 3) * 19);
  }, [locate, content, viewer]);

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
  if (viewer && !toEditor) {
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
            {/* 可编辑的文档给「编辑」入口；外部同步文档只能复制成组织文档后改。 */}
            {mode === 'view' && !sourceReadOnly ? (
              <Button variant="primary" size="sm" onClick={() => setToEditor(true)}>
                <Pencil size={11} />
                {tr('编辑', 'Edit')}
              </Button>
            ) : (
              <Hint
                content={tr(
                  '外部同步文档随来源更新、不可直接修改；复制一份到组织知识库后即可编辑',
                  'Synced docs are refreshed from their source and not directly editable; copy into the org knowledge base to edit',
                )}
              >
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => {
                    // 副本是新组织文档，不带外部源的来源地址
                    setUrl('');
                    setForked(true);
                  }}
                  disabled={loading || !content}
                >
                  <Copy size={11} />
                  {tr('复制为组织文档', 'Copy as org doc')}
                </Button>
              </Hint>
            )}
          </>
        }
      >
        <div className="space-y-3">
          <LocateBanner locate={locate} readOnly />
          <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
            <Chip>{existing ? sourceLabel(existing, tr) : ''}</Chip>
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
                  className="ml-auto max-w-[24rem] truncate font-sans text-[10px] text-zinc-500 underline-offset-2 hover:text-zinc-300 hover:underline"
                >
                  {existing.url}
                </a>
              ) : (
                <span className="ml-auto max-w-[24rem] truncate font-sans text-[10px] text-zinc-500" title={existing.url}>
                  {existing.url}
                </span>
              ))}
          </div>
          <div className="rounded-lg border border-zinc-800/60 bg-zinc-950/40 p-4">
            {loading ? (
              <div className="text-[11.5px] text-zinc-500">{tr('加载中…', 'Loading…')}</div>
            ) : err ? (
              <ErrorBanner>{err}</ErrorBanner>
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
        {err && <ErrorBanner>{err}</ErrorBanner>}
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
        {/* 副本是新组织文档，不带原文档来源地址，直接隐藏该字段 */}
        {!forked && (
          <Field
            htmlFor="knowledge-doc-url"
            label={
              mode === 'create'
                ? tr('来源 URL（可选）', 'Source URL (optional)')
                : tr('来源文件 / URL（不可修改）', 'Source file / URL (read-only)')
            }
          >
            <Input
              id="knowledge-doc-url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              readOnly={urlReadOnly}
              placeholder="https://wiki.internal/runbook/nginx-restart"
              className={cn('font-sans text-[11.5px]', urlReadOnly && 'cursor-not-allowed')}
            />
          </Field>
        )}
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
            className="h-80 w-full resize-y font-sans text-[11.5px] leading-5"
          />
        </Field>
      </div>
    </Modal>
  );
}
