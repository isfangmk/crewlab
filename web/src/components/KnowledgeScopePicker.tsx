// 助理知识范围选择：全部知识库 / 指定目录与文档。
//
// 范围在 SQL 层生效（knowledge_scope.scope_condition），不是检索后筛选；
// 因此这里必须把「命中 0 篇」「路径已不存在」当场拦住，而不是保存时才报错。
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ChevronRight, FileText, Folder, FolderOpen, Layers, Search, X } from 'lucide-react';
import { cn } from '@/lib/cn';
import { errorText } from '@/lib/errors';

import type { ScopePreview } from '@/api/knowledge';
import {
  listDocs,
  listPaths,
  localizedDocTitle,
  localizedPath,
  localizedPathSegment,
  scopePreview,
  searchKnowledge,
  type KnowledgeDoc,
  type PathRow,
  type SearchHit,
} from '@/api/knowledge';
import { Button, Checkbox, Chip, Input, Label, Radio, SearchInput } from '@/components/ui';
import { useI18n } from '@/i18n/locale';

export type ScopeMode = 'all' | 'custom';

export type ScopeSelection = {
  mode: ScopeMode;
  paths: string[];
  doc_ids: string[];
};

export const EMPTY_SCOPE_SELECTION: ScopeSelection = { mode: 'all', paths: [], doc_ids: [] };

/** 把 UI 选择转成后端 `knowledge_scope`；全部知识库 = 空范围。 */
export function scopeSelectionToWire(sel: ScopeSelection): { paths: string[]; doc_ids: string[] } {
  if (sel.mode === 'all') return { paths: [], doc_ids: [] };
  return { paths: sel.paths, doc_ids: sel.doc_ids };
}

/** 从后端助理配置还原 UI 选择。 */
export function scopeSelectionFromWire(scope: { paths: string[]; doc_ids: string[] }): ScopeSelection {
  const empty = scope.paths.length === 0 && scope.doc_ids.length === 0;
  return {
    mode: empty ? 'all' : 'custom',
    paths: [...scope.paths],
    doc_ids: [...scope.doc_ids],
  };
}

/** 指定范围但一项都没选：留空在后端等于继承全部，必须显式拦住。 */
export function scopeSelectionIsIncomplete(sel: ScopeSelection): boolean {
  return sel.mode === 'custom' && sel.paths.length === 0 && sel.doc_ids.length === 0;
}

const ROOT_PATH_LABEL = { zh: '（根目录）', en: '(root)' };
const DOC_FILTER_LIMIT = 50;
const DOC_SEARCH_LIMIT = 100;
// 选择器只加载可用的一页：不传 limit 会让后端拉全库并计算全库正文统计
const DOC_LOAD_LIMIT = 200;

type FolderNode = {
  name: string;
  path: string;
  /** 含子目录的文档数，与选中该目录后实际生效的范围一致 */
  subtreeCount: number;
  children: FolderNode[];
};

type MutableFolderNode = Omit<FolderNode, 'children'> & {
  children: Map<string, MutableFolderNode>;
};

/** 把后端按 path 聚合的扁平行还原成目录树；中间层级即使没有直属文档也要保留。 */
function buildFolderTree(rows: PathRow[]): FolderNode[] {
  const root: MutableFolderNode = {
    name: '',
    path: '',
    subtreeCount: 0,
    children: new Map(),
  };
  for (const row of rows) {
    if (!row.path) continue;
    const parts = row.path.split('/').filter(Boolean);
    let cur = root;
    let cumulative = '';
    for (let i = 0; i < parts.length; i += 1) {
      const segment = parts[i];
      cumulative = cumulative === '' ? segment : `${cumulative}/${segment}`;
      let child = cur.children.get(segment);
      if (!child) {
        child = { name: segment, path: cumulative, subtreeCount: 0, children: new Map() };
        cur.children.set(segment, child);
      }
      child.subtreeCount += row.count;
      cur = child;
    }
  }

  const freeze = (node: MutableFolderNode): FolderNode[] =>
    [...node.children.values()]
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((child) => ({
        name: child.name,
        path: child.path,
        subtreeCount: child.subtreeCount,
        children: freeze(child),
      }));
  return freeze(root);
}

/** 搜索时把树摊平成目录列表，避免用户先展开多层才能点到目标。 */
function flattenFolderTree(nodes: FolderNode[]): FolderNode[] {
  return nodes.flatMap((node) => [node, ...flattenFolderTree(node.children)]);
}

export function KnowledgeScopePicker({
  value,
  onChange,
  onPreviewChange,
}: {
  value: ScopeSelection;
  onChange: (next: ScopeSelection) => void;
  onPreviewChange?: (preview: ScopePreview | null) => void;
}) {
  const { tr } = useI18n();
  const [paths, setPaths] = useState<PathRow[]>([]);
  const [docs, setDocs] = useState<KnowledgeDoc[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [preview, setPreview] = useState<ScopePreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [scopeQuery, setScopeQuery] = useState('');
  const [trialQuery, setTrialQuery] = useState('');
  const [trialHits, setTrialHits] = useState<SearchHit[] | null>(null);
  const [trialError, setTrialError] = useState<string | null>(null);
  const [trialBusy, setTrialBusy] = useState(false);
  // 文档级选择是补充能力，默认收起；已有文档选择时自动展开。
  const [showDocuments, setShowDocuments] = useState(value.doc_ids.length > 0);
  // 树节点默认全部展开；用户逐个折叠后保持自己的选择。
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const expandedInitialized = useRef(false);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([listPaths(), listDocs({ limit: DOC_LOAD_LIMIT })])
      .then(([pathResp, docResp]) => {
        if (cancelled) return;
        setPaths(pathResp.items);
        setDocs(docResp.items);
        setLoadError(null);
      })
      .catch((e: Error) => {
        if (!cancelled) setLoadError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const folderTree = useMemo(() => buildFolderTree(paths), [paths]);

  useEffect(() => {
    if (expandedInitialized.current || folderTree.length === 0) return;
    expandedInitialized.current = true;
    const next = new Set<string>();
    const walk = (nodes: FolderNode[]) => {
      for (const node of nodes) {
        next.add(node.path);
        walk(node.children);
      }
    };
    walk(folderTree);
    setExpanded(next);
  }, [folderTree]);

  const wire = useMemo(() => scopeSelectionToWire(value), [value]);

  useEffect(() => {
    if (value.mode === 'all') {
      setPreview(null);
      setPreviewError(null);
      onPreviewChange?.(null);
      return;
    }
    let cancelled = false;
    // 输入框连续变化的场景不多，250ms 足够合并；范围本身是离散勾选
    const timer = setTimeout(() => {
      void scopePreview(wire)
        .then((resp) => {
          if (cancelled) return;
          setPreview(resp);
          setPreviewError(null);
          onPreviewChange?.(resp);
        })
        .catch((e: Error) => {
          if (cancelled) return;
          setPreview(null);
          setPreviewError(e.message);
          onPreviewChange?.(null);
        });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [value.mode, wire, onPreviewChange]);

  // 父目录已选中时，子目录必然被前缀匹配覆盖；此时子项只展示继承态，不再单独勾选。
  const isPathInherited = (path: string) =>
    value.paths.some((selected) => path.startsWith(`${selected}/`));

  // 父目录未选中、但子目录或文档已选时，父节点显示半选，避免「看起来什么都没选」。
  const isPathPartial = (path: string) => {
    if (value.paths.includes(path) || isPathInherited(path)) return false;
    const prefix = `${path}/`;
    if (value.paths.some((selected) => selected.startsWith(prefix))) return true;
    return value.doc_ids.some((id) => {
      const docPath = docs.find((doc) => doc.id === id)?.path ?? '';
      return docPath === path || docPath.startsWith(prefix);
    });
  };

  const togglePath = (path: string) => {
    if (isPathInherited(path)) return;
    const has = value.paths.includes(path);
    // 选中父目录后清掉被覆盖的子目录，保存的路径列表保持最小。
    const next = value.paths.filter((p) => p !== path && !p.startsWith(`${path}/`));
    if (!has) next.push(path);
    onChange({ ...value, paths: next });
  };

  const toggleExpanded = (path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const renderFolder = (node: FolderNode, depth: number): ReactNode => {
    const hasChildren = node.children.length > 0;
    const isExpanded = expanded.has(node.path);
    const selected = value.paths.includes(node.path);
    const inherited = isPathInherited(node.path);
    const partial = isPathPartial(node.path);
    const label = localizedPath(node.path);
    return (
      <div key={node.path}>
        <div
          className={cn(
            'flex items-center gap-1.5 rounded-md py-1 pr-1.5 text-[11px]',
            inherited ? 'text-zinc-500' : 'text-zinc-300 hover:bg-zinc-900/60',
            selected && 'bg-indigo-500/10',
          )}
          style={{ paddingLeft: `${depth * 14 + 2}px` }}
        >
          {hasChildren ? (
            <button
              type="button"
              onClick={() => toggleExpanded(node.path)}
              aria-label={tr(
                `${isExpanded ? '收起' : '展开'}目录 ${label}`,
                `${isExpanded ? 'Collapse' : 'Expand'} folder ${label}`,
              )}
              aria-expanded={isExpanded}
              className="flex h-5 w-4 shrink-0 items-center justify-center rounded text-zinc-500 hover:text-zinc-300"
            >
              <ChevronRight size={11} className={cn('transition-transform', isExpanded && 'rotate-90')} />
            </button>
          ) : (
            <span className="h-5 w-4 shrink-0" aria-hidden />
          )}
          <Checkbox
            checked={selected || inherited}
            indeterminate={partial}
            disabled={inherited}
            onCheckedChange={() => togglePath(node.path)}
            aria-label={tr(`范围目录 ${label}`, `Scope folder ${label}`)}
            className="h-3 w-3 shrink-0"
          />
          {isExpanded && hasChildren ? (
            <FolderOpen size={12} className="shrink-0 text-zinc-500" aria-hidden />
          ) : (
            <Folder size={12} className="shrink-0 text-zinc-500" aria-hidden />
          )}
          <button
            type="button"
            onClick={() => togglePath(node.path)}
            disabled={inherited}
            className="flex min-w-0 flex-1 items-center gap-1.5 text-left disabled:cursor-default"
          >
            <span className="truncate">{localizedPathSegment(node.name)}</span>
            {inherited && (
              <span className="shrink-0 text-[9.5px] text-zinc-600">{tr('随上级', 'inherited')}</span>
            )}
            {partial && (
              <span className="shrink-0 text-[9.5px] text-zinc-600">{tr('部分', 'partial')}</span>
            )}
            <span
              className="ml-auto shrink-0 text-[10px] tabular-nums text-zinc-500"
              title={tr(
                `含子目录共 ${node.subtreeCount} 篇`,
                `${node.subtreeCount} document(s) including subfolders`,
              )}
            >
              {node.subtreeCount}
            </span>
          </button>
        </div>
        {isExpanded && hasChildren && (
          <div>{node.children.map((child) => renderFolder(child, depth + 1))}</div>
        )}
      </div>
    );
  };

  const toggleDoc = (id: string) => {
    const has = value.doc_ids.includes(id);
    onChange({
      ...value,
      doc_ids: has ? value.doc_ids.filter((d) => d !== id) : [...value.doc_ids, id],
    });
  };

  // 目录树与搜索结果共用同一行结构，避免两处勾选语义分叉。
  const renderDocRow = (doc: KnowledgeDoc) => {
    const path = doc.path ?? '';
    const inherited = value.paths.some(
      (selected) => path === selected || path.startsWith(`${selected}/`),
    );
    return (
      <label
        key={`doc-${doc.id}`}
        className={cn(
          'flex items-center gap-2 rounded-md px-2 py-1.5 text-[11px] text-zinc-300 hover:bg-zinc-900/60',
          inherited && 'text-zinc-500',
        )}
      >
        <Checkbox
          checked={inherited || value.doc_ids.includes(doc.id)}
          disabled={inherited}
          onCheckedChange={() => toggleDoc(doc.id)}
          aria-label={tr(`范围文档 ${localizedDocTitle(doc)}`, `Scope document ${localizedDocTitle(doc)}`)}
          className="h-3 w-3 shrink-0"
        />
        <FileText size={12} className="shrink-0 text-zinc-500" aria-hidden />
        <span className="min-w-0 flex-1 truncate">{localizedDocTitle(doc)}</span>
        {inherited && (
          <span className="shrink-0 text-[9.5px] text-zinc-600">{tr('随目录', 'in folder')}</span>
        )}
        <span className="ml-auto max-w-[9rem] shrink-0 truncate text-[9.5px] text-zinc-500" title={localizedPath(path) || '—'}>
          {localizedPath(path) || '—'}
        </span>
      </label>
    );
  };

  const browseDocs = useMemo(() => docs.slice(0, DOC_FILTER_LIMIT), [docs]);

  const searchQuery = scopeQuery.trim().toLowerCase();
  const searchingScope = searchQuery !== '';
  const folderMatches = useMemo(() => {
    if (searchQuery === '') return [];
    return flattenFolderTree(folderTree).filter(
      (node) =>
        node.path.toLowerCase().includes(searchQuery) ||
        localizedPath(node.path).toLowerCase().includes(searchQuery),
    );
  }, [folderTree, searchQuery]);
  const docMatches = useMemo(() => {
    if (searchQuery === '') return [];
    return docs
      .filter((doc) => {
        const title = localizedDocTitle(doc).toLowerCase();
        return (
          title.includes(searchQuery) ||
          doc.id.toLowerCase().includes(searchQuery) ||
          (doc.path ?? '').toLowerCase().includes(searchQuery)
        );
      })
      .slice(0, DOC_SEARCH_LIMIT);
  }, [docs, searchQuery]);

  // 已选文档可能不在已加载的一页里：回退展示 id，避免「选了却不显示」
  const selectedDocs = useMemo(
    () =>
      value.doc_ids.map((id) => {
        const found = docs.find((doc) => doc.id === id);
        return {
          id,
          title: found ? localizedDocTitle(found) : id,
          path: found?.path ?? '',
        };
      }),
    [docs, value.doc_ids],
  );

  useEffect(() => {
    if (value.doc_ids.length > 0) setShowDocuments(true);
  }, [value.doc_ids.length]);

  const runTrial = useCallback(async () => {
    if (trialQuery.trim() === '') return;
    setTrialBusy(true);
    setTrialError(null);
    try {
      const resp = await searchKnowledge(trialQuery.trim(), {
        limit: 5,
        scope: value.mode === 'custom' ? wire : undefined,
      });
      setTrialHits(resp.items);
    } catch (e) {
      setTrialHits(null);
      setTrialError(errorText(e));
    } finally {
      setTrialBusy(false);
    }
  }, [trialQuery, value.mode, wire]);

  const unknown = preview !== null && (preview.unknown_paths.length > 0 || preview.unknown_doc_ids.length > 0);
  const emptyScope = preview !== null && preview.doc_count === 0;
  const incomplete = scopeSelectionIsIncomplete(value);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div
          className="inline-flex items-center rounded-lg border border-zinc-800/60 bg-zinc-950/40 p-0.5"
          role="group"
          aria-label={tr('知识范围模式', 'Knowledge scope mode')}
        >
          <Label
            className={cn(
              'flex cursor-pointer items-center rounded-md px-2.5 py-1 text-[10.5px] transition-colors focus-within:ring-1 focus-within:ring-indigo-500/40',
              value.mode === 'all' ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-500 hover:text-zinc-300',
            )}
          >
            <Radio
              className="sr-only"
              name="knowledge-scope-mode"
              checked={value.mode === 'all'}
              onChange={() => onChange({ mode: 'all', paths: [], doc_ids: [] })}
            />
            {tr('全部知识库', 'Whole knowledge base')}
          </Label>
          <Label
            className={cn(
              'flex cursor-pointer items-center rounded-md px-2.5 py-1 text-[10.5px] transition-colors focus-within:ring-1 focus-within:ring-indigo-500/40',
              value.mode === 'custom' ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-500 hover:text-zinc-300',
            )}
          >
            <Radio
              className="sr-only"
              name="knowledge-scope-mode"
              checked={value.mode === 'custom'}
              onChange={() => onChange({ ...value, mode: 'custom' })}
            />
            {tr('指定范围', 'Custom scope')}
          </Label>
        </div>
        <span className="text-[10px] text-zinc-500">
          {value.mode === 'all'
            ? tr('不限制目录与文档，检索全部知识库', 'Search every document without a folder scope')
            : tr('只在这些文档内检索；命中 0 篇时无法保存', 'Search only these documents; an empty scope cannot be saved')}
        </span>
        {loadError && <span className="text-[10px] text-danger">{loadError}</span>}
      </div>

      {value.mode === 'all' ? (
        <div className="flex h-[22rem] flex-col items-center justify-center rounded-xl border border-zinc-800/60 bg-zinc-900/30 px-6 text-center">
          <span className="grid h-11 w-11 place-items-center rounded-xl bg-indigo-500/10 text-indigo-300">
            <Layers size={20} />
          </span>
          <div className="mt-3 text-[14px] font-semibold text-zinc-100">
            {tr('检索全部知识库', 'Search the whole knowledge base')}
          </div>
          <p className="mt-1 max-w-[24rem] text-[11px] leading-5 text-zinc-500">
            {tr(
              '不限制目录与文档，问答时在所有已索引内容中检索。适合内容范围小、或希望助理自行判断的场景。',
              'No folder or document limit; every indexed document is searchable. Suited to small corpora or assistants that decide scope themselves.',
            )}
          </p>
          <Chip dense tone="success" className="mt-3">{tr('无范围限制', 'No scope limit')}</Chip>
        </div>
      ) : (
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_18rem]">
          <section className="flex h-[26rem] min-h-0 flex-col overflow-hidden rounded-xl border border-zinc-800/60 bg-zinc-900/30">
            <header className="flex flex-wrap items-center gap-2 border-b border-zinc-800/60 px-3.5 py-2.5">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-[12px] font-semibold text-zinc-100">
                    {tr('知识库目录', 'Knowledge folders')}
                  </span>
                  <span className="text-[10px] text-zinc-500">
                    {docs.length > 0 ? tr(`${docs.length} 篇文档`, `${docs.length} docs`) : tr('暂无文档', 'no docs')}
                  </span>
                </div>
                <p className="mt-0.5 text-[10px] text-zinc-500">
                  {tr('选择目录会包含全部子目录', 'Selecting a folder includes every subfolder')}
                </p>
              </div>
              <div className="w-full shrink-0 sm:w-[13.5rem]">
                <SearchInput
                  value={scopeQuery}
                  onChange={setScopeQuery}
                  placeholder={tr('搜索目录或文档', 'Search folders or documents')}
                  ariaLabel={tr('搜索知识范围', 'Search knowledge scope')}
                />
              </div>
            </header>

            {searchingScope ? (
              <div className="flex min-h-0 flex-1 flex-col">
                <div className="border-b border-zinc-800/50 px-3.5 py-1.5 text-[10px] text-zinc-500">
                  <span>
                    {tr(
                      `匹配 ${folderMatches.length} 个目录 · ${docMatches.length} 篇文档`,
                      `${folderMatches.length} folder(s) · ${docMatches.length} document(s)`,
                    )}
                  </span>
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
                  {folderMatches.length === 0 && docMatches.length === 0 ? (
                    <div className="rounded-lg border border-zinc-800/60 px-3 py-8 text-center text-[11px] text-zinc-500">
                      {tr('没有匹配的目录或文档。', 'No matching folders or documents.')}
                    </div>
                  ) : (
                    <>
                      {folderMatches.length > 0 && (
                        <div className="pb-1">
                          <div className="px-2 pb-1 text-[9px] text-zinc-600">{tr('目录', 'Folders')}</div>
                          {folderMatches.map((node) => {
                            const selected = value.paths.includes(node.path);
                            const inherited = isPathInherited(node.path);
                            const partial = isPathPartial(node.path);
                            const label = localizedPath(node.path);
                            return (
                              <label
                                key={`folder-${node.path}`}
                                className={cn(
                                  'flex items-center gap-2 rounded-md px-2 py-1.5 text-[11px] hover:bg-zinc-900/60',
                                  inherited ? 'text-zinc-500' : 'text-zinc-300',
                                  selected && 'bg-indigo-500/10',
                                )}
                              >
                                <Checkbox
                                  checked={selected || inherited}
                                  indeterminate={partial}
                                  disabled={inherited}
                                  onCheckedChange={() => togglePath(node.path)}
                                  aria-label={tr(`范围目录 ${label}`, `Scope folder ${label}`)}
                                  className="h-3 w-3 shrink-0"
                                />
                                <Folder size={12} className="shrink-0 text-zinc-500" aria-hidden />
                                <span className="min-w-0 flex-1 truncate">{label}</span>
                                {inherited && (
                                  <span className="shrink-0 text-[9.5px] text-zinc-600">
                                    {tr('随上级', 'inherited')}
                                  </span>
                                )}
                                {partial && (
                                  <span className="shrink-0 text-[9.5px] text-zinc-600">
                                    {tr('部分', 'partial')}
                                  </span>
                                )}
                                <span className="shrink-0 text-[9.5px] text-zinc-500">{node.subtreeCount}</span>
                              </label>
                            );
                          })}
                        </div>
                      )}
                      {docMatches.length > 0 && (
                        <div>
                          <div className="px-2 pb-1 text-[9px] text-zinc-600">{tr('文档', 'Documents')}</div>
                          {docMatches.map((doc) => renderDocRow(doc))}
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>
            ) : (
              <>
                <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
                  {folderTree.length === 0 ? (
                    <p className="px-2 py-2 text-[11px] text-zinc-500">
                      {tr('知识库暂无目录。', 'No folders yet.')}
                    </p>
                  ) : (
                    folderTree.map((node) => renderFolder(node, 0))
                  )}
                </div>

                <div className="border-t border-zinc-800/60">
                  <button
                    type="button"
                    onClick={() => setShowDocuments((v) => !v)}
                    aria-expanded={showDocuments}
                    className="flex w-full items-center gap-1.5 px-3 py-2 text-left"
                  >
                    <ChevronRight
                      size={11}
                      className={cn(
                        'shrink-0 text-zinc-500 transition-transform',
                        showDocuments && 'rotate-90',
                      )}
                    />
                    <span className="text-[10.5px] font-medium text-zinc-300">
                      {tr('按文档精确选择', 'Pick individual documents')}
                    </span>
                    <span className="text-[9.5px] text-zinc-500">
                      {selectedDocs.length > 0
                        ? tr(`已选 ${selectedDocs.length} 篇`, `${selectedDocs.length} selected`)
                        : tr('可选', 'optional')}
                    </span>
                  </button>
                  {showDocuments && (
                    <div className="border-t border-zinc-800/60 px-2 py-2">
                      <div className="max-h-40 overflow-y-auto">
                        {browseDocs.length === 0 ? (
                          <p className="px-2 py-1.5 text-[11px] text-zinc-500">
                            {tr('知识库暂无文档。', 'No documents yet.')}
                          </p>
                        ) : (
                          browseDocs.map((doc) => renderDocRow(doc))
                        )}
                      </div>
                      {docs.length > browseDocs.length && (
                        <p className="mt-1 px-1 text-[9.5px] text-zinc-500">
                          {tr(
                            `仅显示前 ${browseDocs.length} 篇，共 ${docs.length} 篇；用上方搜索查找其余文档。`,
                            `Showing first ${browseDocs.length} of ${docs.length}; use the search above for the rest.`,
                          )}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              </>
            )}
          </section>

          <aside className="flex h-[26rem] min-h-0 flex-col gap-3 overflow-y-auto rounded-xl border border-zinc-800/60 bg-zinc-900/30 p-3.5">
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="text-[12px] font-semibold text-zinc-100">
                  {tr('已选范围', 'Selected scope')}
                </div>
                <div className="mt-0.5 text-[9.5px] text-zinc-500">
                  {tr(
                    `${value.paths.length} 个目录 · ${selectedDocs.length} 篇文档`,
                    `${value.paths.length} folder(s) · ${selectedDocs.length} doc(s)`,
                  )}
                </div>
              </div>
              <Button
                variant="ghost"
                size="sm"
                type="button"
                disabled={value.paths.length === 0 && selectedDocs.length === 0}
                onClick={() => onChange({ ...value, paths: [], doc_ids: [] })}
                className="px-1.5 py-0.5 text-[9.5px] text-zinc-500"
              >
                {tr('清空', 'Clear')}
              </Button>
            </div>

            <div className="flex flex-wrap gap-1">
              {value.paths.length === 0 && selectedDocs.length === 0 ? (
                <span className="text-[10px] text-zinc-600">
                  {tr('尚未选择目录或文档', 'No folder or document selected')}
                </span>
              ) : (
                <>
                  {value.paths.map((path) => (
                    <span
                      key={`selected-${path}`}
                      className="inline-flex max-w-full items-center gap-1 rounded-md border border-zinc-800/60 bg-zinc-950/40 py-0.5 pl-1.5 pr-0.5 text-[9.5px] text-zinc-300"
                    >
                      <Folder size={10} className="shrink-0 text-zinc-500" aria-hidden />
                      <span className="truncate" title={localizedPath(path)}>{localizedPath(path)}</span>
                      <button
                        type="button"
                        aria-label={tr(`移除范围目录 ${localizedPath(path)}`, `Remove scope folder ${localizedPath(path)}`)}
                        onClick={() => onChange({ ...value, paths: value.paths.filter((p) => p !== path) })}
                        className="rounded p-0.5 text-zinc-500 hover:bg-zinc-800/60 hover:text-zinc-200"
                      >
                        <X size={9} />
                      </button>
                    </span>
                  ))}
                  {selectedDocs.map((doc) => (
                    <span
                      key={`selected-doc-${doc.id}`}
                      className="inline-flex max-w-full items-center gap-1 rounded-md border border-zinc-800/60 bg-zinc-950/40 py-0.5 pl-1.5 pr-0.5 text-[9.5px] text-zinc-300"
                    >
                      <FileText size={10} className="shrink-0 text-zinc-500" aria-hidden />
                      <span className="truncate" title={doc.title}>{doc.title}</span>
                      <button
                        type="button"
                        aria-label={tr(`移除范围文档 ${doc.title}`, `Remove scope document ${doc.title}`)}
                        onClick={() => toggleDoc(doc.id)}
                        className="rounded p-0.5 text-zinc-500 hover:bg-zinc-800/60 hover:text-zinc-200"
                      >
                        <X size={9} />
                      </button>
                    </span>
                  ))}
                </>
              )}
            </div>

            <div className="rounded-lg border border-zinc-800/60 bg-zinc-950/40 px-3 py-2.5">
              <div className="text-[10px] text-zinc-500">{tr('范围命中', 'In scope')}</div>
              {previewError !== null ? (
                <p className="mt-1 text-[10px] leading-4 text-danger">
                  {tr('范围预览失败：', 'Scope preview failed: ')}{previewError}
                </p>
              ) : incomplete ? (
                <p className="mt-1 text-[10px] leading-4 text-warn">
                  {tr(
                    '至少选择一个目录或文档；留空等于「全部知识库」。',
                    'Pick at least one folder or document; empty means the whole knowledge base.',
                  )}
                </p>
              ) : preview !== null ? (
                <>
                  <div className="mt-0.5 text-[10px] text-zinc-500">
                    {tr('命中 ', 'In scope: ')}
                    <span
                      className={cn(
                        'text-[22px] font-semibold leading-none text-zinc-100',
                        (unknown || emptyScope) && 'text-danger',
                      )}
                    >
                      {preview.doc_count}
                    </span>
                    {' '}{tr('篇文档', 'documents')}
                  </div>
                  {preview.matched_paths.length > 0 && (
                    <ul className="mt-2 space-y-1">
                      {preview.matched_paths.slice(0, 4).map((item) => (
                        <li key={item.path} className="flex items-center gap-1.5 text-[9.5px] text-zinc-400">
                          <span className="h-1 w-1 shrink-0 rounded-full bg-indigo-400" />
                          <span className="min-w-0 flex-1 truncate" title={localizedPath(item.path)}>
                            {localizedPath(item.path) || tr(ROOT_PATH_LABEL.zh, ROOT_PATH_LABEL.en)}
                          </span>
                          <span className="shrink-0 text-zinc-300">{item.count}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  {unknown && (
                    <p className="mt-1.5 text-[9.5px] leading-4 text-danger">
                      {tr(
                        '范围包含已不存在的项目，保存会被拒绝：',
                        'Scope contains missing items, saving will be rejected: ',
                      )}
                      {[...preview.unknown_paths, ...preview.unknown_doc_ids].join('、')}
                    </p>
                  )}
                  {emptyScope && !unknown && (
                    <p className="mt-1.5 text-[9.5px] leading-4 text-danger">
                      {tr('范围为空，保存会被拒绝。', 'Empty scope; saving will be rejected.')}
                    </p>
                  )}
                </>
              ) : (
                <p className="mt-1 text-[10px] text-zinc-500">{tr('正在计算范围…', 'Computing scope…')}</p>
              )}
            </div>

            <div className="mt-auto border-t border-zinc-800/60 pt-2.5">
              <div className="text-[9.5px] text-zinc-500">{tr('按当前范围试检索', 'Try a search in this scope')}</div>
              <div className="mt-1 flex items-center gap-1.5">
                <Input
                  type="text"
                  value={trialQuery}
                  onChange={(e) => setTrialQuery(e.target.value)}
                  placeholder={tr('输入检索词', 'Enter a query')}
                  className="h-7 w-full text-[10px]"
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="shrink-0 px-2 py-1 text-[10px]"
                  onClick={() => void runTrial()}
                  disabled={trialBusy || trialQuery.trim() === ''}
                >
                  <Search size={11} /> {trialBusy ? tr('检索中…', 'Searching…') : tr('试跑', 'Search')}
                </Button>
              </div>
              {trialError !== null && <div className="mt-1 text-[10px] text-danger">{trialError}</div>}
              {trialHits !== null && (
                <div className="mt-1.5 space-y-1 text-[10px] text-zinc-400">
                  {trialHits.length === 0 ? (
                    <p className="text-zinc-500">{tr('当前范围内没有命中。', 'No hits within this scope.')}</p>
                  ) : (
                    trialHits.map((hit) => (
                      <div key={hit.doc.id} className="flex items-center gap-2">
                        <span className="truncate text-zinc-300">{localizedDocTitle(hit.doc)}</span>
                        <span className="ml-auto max-w-[7rem] shrink-0 truncate text-[9px] text-zinc-500">
                          {localizedPath(hit.doc.path ?? '') || '—'}
                        </span>
                        <span className="shrink-0 text-[9px] text-zinc-500">{hit.score.toFixed(3)}</span>
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}
