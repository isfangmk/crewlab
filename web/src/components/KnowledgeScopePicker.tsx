// 助理知识范围选择：全部知识库 / 指定目录与文档。
//
// 范围在 SQL 层生效（knowledge_scope.scope_condition），不是检索后筛选；
// 因此这里必须把「命中 0 篇」「路径已不存在」当场拦住，而不是保存时才报错。
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ChevronRight, Folder, FolderOpen, Search } from 'lucide-react';
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
import { Button, Checkbox, Input, Label, Radio } from '@/components/ui';
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
  const [docQuery, setDocQuery] = useState('');
  const [trialQuery, setTrialQuery] = useState('');
  const [trialHits, setTrialHits] = useState<SearchHit[] | null>(null);
  const [trialError, setTrialError] = useState<string | null>(null);
  const [trialBusy, setTrialBusy] = useState(false);
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
    const label = localizedPath(node.path);
    return (
      <div key={node.path}>
        <div
          className={cn(
            'flex items-center gap-1 rounded py-0.5 pr-1 text-[11px]',
            inherited ? 'text-zinc-500' : 'text-zinc-300 hover:bg-zinc-900/60',
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
            disabled={inherited}
            onCheckedChange={() => togglePath(node.path)}
            aria-label={tr(`范围目录 ${label}`, `Scope folder ${label}`)}
            className="h-3 w-3"
          />
          {isExpanded && hasChildren ? (
            <FolderOpen size={11} className="shrink-0 text-zinc-500" aria-hidden />
          ) : (
            <Folder size={11} className="shrink-0 text-zinc-500" aria-hidden />
          )}
          <button
            type="button"
            onClick={() => togglePath(node.path)}
            disabled={inherited}
            className="flex min-w-0 flex-1 items-center gap-1.5 text-left disabled:cursor-default"
          >
            <span className="truncate">{localizedPathSegment(node.name)}</span>
            {inherited && (
              <span className="shrink-0 text-[10px] text-zinc-600">{tr('随上级', 'inherited')}</span>
            )}
            <span
              className="ml-auto shrink-0 font-mono text-[10px] tabular-nums text-zinc-500"
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

  const filteredDocs = useMemo(() => {
    const q = docQuery.trim().toLowerCase();
    const base = q === ''
      ? docs
      : docs.filter((doc) => {
          const title = localizedDocTitle(doc).toLowerCase();
          return title.includes(q) || doc.id.includes(q) || (doc.path ?? '').toLowerCase().includes(q);
        });
    return base.slice(0, DOC_FILTER_LIMIT);
  }, [docs, docQuery]);

  // 已选文档可能不在已加载的一页里：回退展示 id，避免「选了却不显示」
  const selectedDocs = useMemo(
    () =>
      value.doc_ids.map((id) => {
        const found = docs.find((doc) => doc.id === id);
        return { id, title: found ? localizedDocTitle(found) : id };
      }),
    [docs, value.doc_ids],
  );
  // 根目录不是可选范围：空范围已经代表「全部知识库」，后端也拒绝空路径
  // 目录树在 buildFolderTree 中已按 path 层级聚合，根节点本身不渲染。

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
      <div className="flex flex-wrap gap-4 text-[11px] text-zinc-300">
        <Label className="flex items-center gap-1.5">
          <Radio
            name="knowledge-scope-mode"
            checked={value.mode === 'all'}
            onChange={() => onChange({ mode: 'all', paths: [], doc_ids: [] })}
          />
          {tr('全部知识库', 'Whole knowledge base')}
        </Label>
        <Label className="flex items-center gap-1.5">
          <Radio
            name="knowledge-scope-mode"
            checked={value.mode === 'custom'}
            onChange={() => onChange({ ...value, mode: 'custom' })}
          />
          {tr('指定范围', 'Custom scope')}
        </Label>
      </div>

      {loadError && <div className="text-[11px] text-danger">{loadError}</div>}

      {value.mode === 'all' ? (
        <p className="text-[11px] text-zinc-500">
          {tr('助理可检索全部组织文档。', 'The assistant can search every organization document.')}
        </p>
      ) : (
        <div className="space-y-3">
          <div>
            <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-zinc-400">
              {tr('目录（含子目录）', 'Folders (with subfolders)')}
            </div>
            {folderTree.length === 0 ? (
              <p className="text-[11px] text-zinc-500">{tr('知识库暂无目录。', 'No folders yet.')}</p>
            ) : (
              <div className="max-h-56 overflow-y-auto rounded border border-zinc-800/60 p-1">
                {folderTree.map((node) => renderFolder(node, 0))}
              </div>
            )}
          </div>

          <div>
            <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-zinc-400">
              {tr('文档', 'Documents')}
            </div>
            <Input
              type="text"
              value={docQuery}
              onChange={(e) => setDocQuery(e.target.value)}
              placeholder={tr('按标题 / 路径 / ID 过滤', 'Filter by title / path / ID')}
              className="mb-1.5 w-full"
            />
            {selectedDocs.length > 0 && (
              <div className="mb-1.5 flex flex-wrap gap-1.5">
                {selectedDocs.map((doc) => (
                  <span
                    key={doc.id}
                    className="rounded border border-zinc-800/60 bg-zinc-900/30 px-1.5 py-0.5 text-[10px] text-zinc-400"
                  >
                    {doc.title}
                  </span>
                ))}
              </div>
            )}
            <div className="max-h-48 overflow-y-auto rounded border border-zinc-800/60">
              {filteredDocs.length === 0 ? (
                <p className="px-2 py-1.5 text-[11px] text-zinc-500">
                  {tr('没有匹配的文档。', 'No matching documents.')}
                </p>
              ) : (
                filteredDocs.map((doc) => (
                  <label
                    key={doc.id}
                    className="flex items-center gap-2 border-b border-zinc-800/40 px-2 py-1 text-[11px] text-zinc-300 last:border-b-0"
                  >
                    <Checkbox
                      checked={value.doc_ids.includes(doc.id)}
                      onCheckedChange={() => toggleDoc(doc.id)}
                      aria-label={tr(`范围文档 ${localizedDocTitle(doc)}`, `Scope document ${localizedDocTitle(doc)}`)}
                      className="h-3 w-3"
                    />
                    <span className="truncate">{localizedDocTitle(doc)}</span>
                    <span className="ml-auto shrink-0 font-mono text-[10px] text-zinc-500">
                      {localizedPath(doc.path ?? '') || '—'}
                    </span>
                  </label>
                ))
              )}
            </div>
          </div>

          <div className="text-[11px]">
            {previewError !== null ? (
              <span className="text-danger">{tr('范围预览失败：', 'Scope preview failed: ')}{previewError}</span>
            ) : incomplete ? (
              <span className="text-warn">
                {tr(
                  '至少选择一个目录或文档；留空等于「全部知识库」。',
                  'Pick at least one folder or document; empty means the whole knowledge base.',
                )}
              </span>
            ) : preview !== null ? (
              <span className={unknown || emptyScope ? 'text-danger' : 'text-zinc-400'}>
                {tr(`命中 ${preview.doc_count} 篇文档`, `${preview.doc_count} document(s) in scope`)}
                {preview.matched_paths.length > 0 &&
                  ' · ' +
                    preview.matched_paths
                      .map((p) => `${localizedPath(p.path) || tr(ROOT_PATH_LABEL.zh, ROOT_PATH_LABEL.en)}(${p.count})`)
                      .join('、')}
                {unknown && (
                  <>
                    {' · '}
                    {tr('范围包含已不存在的项目，保存会被拒绝：', 'Scope contains missing items, saving will be rejected: ')}
                    {[...preview.unknown_paths, ...preview.unknown_doc_ids].join('、')}
                  </>
                )}
                {emptyScope && tr(' · 范围为空，保存会被拒绝', ' · empty scope, saving will be rejected')}
              </span>
            ) : (
              <span className="text-zinc-500">{tr('正在计算范围…', 'Computing scope…')}</span>
            )}
          </div>

          <div>
            <div className="mb-1 flex items-center gap-1.5">
              <Input
                type="text"
                value={trialQuery}
                onChange={(e) => setTrialQuery(e.target.value)}
                placeholder={tr('按当前范围试检索', 'Try a search in this scope')}
                className="w-full"
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="shrink-0 px-2 py-1 text-[11px]"
                onClick={() => void runTrial()}
                disabled={trialBusy || trialQuery.trim() === ''}
              >
                <Search size={11} /> {trialBusy ? tr('检索中…', 'Searching…') : tr('试检索', 'Search')}
              </Button>
            </div>
            {trialError !== null && <div className="text-[11px] text-danger">{trialError}</div>}
            {trialHits !== null && (
              <div className="mt-1 space-y-1 text-[11px] text-zinc-400">
                {trialHits.length === 0 ? (
                  <p className="text-zinc-500">{tr('当前范围内没有命中。', 'No hits within this scope.')}</p>
                ) : (
                  trialHits.map((hit) => (
                    <div key={hit.doc.id} className="flex items-center gap-2">
                      <span className="truncate text-zinc-300">{localizedDocTitle(hit.doc)}</span>
                      <span className="ml-auto shrink-0 font-mono text-[10px] text-zinc-500">
                        {localizedPath(hit.doc.path ?? '') || '—'}
                      </span>
                      <span className="shrink-0 text-zinc-500">{hit.score.toFixed(3)}</span>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
