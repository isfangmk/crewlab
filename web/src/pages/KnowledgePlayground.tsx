// 检索试跑页 — 知识检索调试台。
//
// 骨架（与 docs/UI设计规范.md 一致）：
//   PageHeader   标题 / 说明
//   FilterBar    试调区：返回条数 / RRF 阈值 / 来源组 / 来源类型 / 范围模式
//   语义检索面板 复用知识库页同款命中结果表（分数栏 + 命中块编号）
//
// 与知识库页的检索视图分工：这里放「怎么搜」的旋钮（阈值覆盖、助理范围模拟），
// 供配置助理知识范围前离线试调；点结果开只读预览并选中命中块。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAssistantPage } from '@/lib/assistantPage';
import { Modal } from '@/components/Modal';
import {
  Button,
  Chip,
  ErrorBanner,
  FilterBar,
  FilterPill,
  Input,
  PageHeader,
  Select,
  Textarea,
} from '@/components/ui';
import { LocateBanner, SemanticSearchPanel } from '@/components/knowledge/SearchPanel';
import {
  getDoc,
  localizedDocTitle,
  localizedPath,
  scopePreview,
  searchKnowledge,
  type DocSource,
  type KnowledgeDoc,
  type ScopePreview,
  type SearchHit,
} from '@/api/knowledge';
import { listAgents, type AgentSummary } from '@/api/agents';
import { useI18n } from '@/i18n/locale';
import { errorText } from '@/lib/errors';

type ScopeMode = 'all' | 'agent' | 'custom';

// 逗号 / 换行都算分隔符：范围输入是调试旋钮，不需要严谨的编辑器交互
const splitList = (text: string) =>
  text
    .split(/[\n,，]/)
    .map((s) => s.trim())
    .filter(Boolean);

// 只读预览弹窗：textarea 里选中命中块（与知识库编辑器同款定位手法，但不提供编辑）
function DocPreviewModal({
  doc,
  locate,
  onClose,
}: {
  doc: KnowledgeDoc;
  locate: { chunkIndex: number; excerpt: string };
  onClose(): void;
}) {
  const { tr } = useI18n();
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const locatedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);
    void getDoc(doc.id)
      .then((res) => {
        if (!cancelled) setContent(res.content ?? '');
      })
      .catch((e) => {
        if (!cancelled) setErr(errorText(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [doc.id]);

  // 命中块定位：正文加载后选中 excerpt 并把视口滚到对应行。
  // loading 也进依赖：加载态不渲染 textarea，必须等它挂载后再定位。
  useEffect(() => {
    if (loading || !content || locatedRef.current) return;
    const el = textareaRef.current;
    if (!el) return;
    const start = content.indexOf(locate.excerpt);
    if (start < 0) return;
    locatedRef.current = true;
    el.focus();
    el.setSelectionRange(start, start + locate.excerpt.length);
    const line = content.slice(0, start).split('\n').length;
    el.scrollTop = Math.max(0, (line - 3) * 19);
  }, [loading, content, locate.excerpt]);

  return (
    <Modal
      open
      onClose={onClose}
      size="xl"
      resizable
      title={localizedDocTitle(doc)}
      footer={
        <Button variant="outline" size="sm" onClick={onClose}>
          {tr('关闭', 'Close')}
        </Button>
      }
    >
      <div className="space-y-3">
        <LocateBanner locate={locate} readOnly={false} />
        <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
          {doc.path && <Chip>{localizedPath(doc.path)}</Chip>}
          {(doc.tags ?? []).map((tag) => (
            <Chip key={tag}>{tag}</Chip>
          ))}
        </div>
        {loading ? (
          <div className="text-[11.5px] text-zinc-500">{tr('加载中…', 'Loading…')}</div>
        ) : err ? (
          <ErrorBanner>{err}</ErrorBanner>
        ) : (
          <Textarea
            aria-label={tr('文档正文', 'Document content')}
            ref={textareaRef}
            value={content}
            readOnly
            className="h-96 w-full resize-y font-sans text-[11.5px] leading-5"
          />
        )}
      </div>
    </Modal>
  );
}

export default function KnowledgePlaygroundPage() {
  const { tr } = useI18n();
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [searchedQuery, setSearchedQuery] = useState<string | null>(null);
  // 试调旋钮：返回条数 / RRF 阈值覆盖（空串 = 用全局阈值）
  const [limit, setLimit] = useState(10);
  const [minScoreText, setMinScoreText] = useState('');
  const [group, setGroup] = useState<'all' | 'org' | 'builtin'>('all');
  const [sourceType, setSourceType] = useState<DocSource | ''>('');
  // 范围试跑：全库 / 按助理（取其真实知识范围）/ 自定义
  const [scopeMode, setScopeMode] = useState<ScopeMode>('all');
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [agentName, setAgentName] = useState('');
  const [customPathsText, setCustomPathsText] = useState('');
  const [customDocIdsText, setCustomDocIdsText] = useState('');
  const [preview, setPreview] = useState<ScopePreview | null>(null);
  const [openHit, setOpenHit] = useState<{
    doc: KnowledgeDoc;
    locate: { chunkIndex: number; excerpt: string };
  } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // 检索自己的请求序号：连续试调时只有最后一次结果落进界面
  const searchSeq = useRef(0);

  // 助理下拉：按助理范围试跑取的是它真实的 knowledge_scope
  useEffect(() => {
    let cancelled = false;
    void listAgents()
      .then((res) => {
        if (!cancelled) setAgents(res.items ?? []);
      })
      .catch((e) => {
        if (!cancelled) setErr(errorText(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 当前范围（wire 形态）：不传 = 全库；助理空范围同样不传，与后端「空 = 不限」口径一致
  const scopeWire = useMemo(() => {
    if (scopeMode === 'agent') {
      const scope = agents.find((a) => a.name === agentName)?.knowledge_scope;
      if (!scope || (scope.paths.length === 0 && scope.doc_ids.length === 0)) return undefined;
      return { paths: [...scope.paths], doc_ids: [...scope.doc_ids] };
    }
    if (scopeMode === 'custom') {
      const paths = splitList(customPathsText);
      const docIds = splitList(customDocIdsText);
      return paths.length > 0 || docIds.length > 0 ? { paths, doc_ids: docIds } : undefined;
    }
    return undefined;
  }, [scopeMode, agentName, agents, customPathsText, customDocIdsText]);

  // 范围预览：配置变化后防抖问一次，展示覆盖篇数与失效目录/文档
  useEffect(() => {
    if (scopeMode === 'all' || !scopeWire) {
      setPreview(null);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      void scopePreview(scopeWire)
        .then((res) => {
          if (!cancelled) setPreview(res);
        })
        .catch((e) => {
          if (!cancelled) setErr(errorText(e));
        });
    }, 400);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [scopeMode, scopeWire]);

  const runSearch = async () => {
    const current = ++searchSeq.current;
    const nextQuery = query.trim();
    if (nextQuery === '') {
      setHits([]);
      setSearchedQuery(null);
      return;
    }
    // 阈值非法显式报错，不悄悄退回全局——试跑页就是要看到真实生效的口径
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
    try {
      const res = await searchKnowledge(nextQuery, {
        limit,
        builtin: group === 'all' ? undefined : group === 'builtin',
        sourceType: sourceType !== '' ? sourceType : undefined,
        scope: scopeWire,
        minScore,
      });
      if (searchSeq.current !== current) return;
      setHits(res.items ?? []);
      setSearchedQuery(nextQuery);
    } catch (e) {
      if (searchSeq.current !== current) return;
      setErr(errorText(e));
    }
  };

  const resetSearch = useCallback(() => {
    searchSeq.current += 1;
    setQuery('');
    setHits([]);
    setSearchedQuery(null);
  }, []);

  useAssistantPage('检索试跑', 'Retrieval playground', {
    scope_mode: scopeMode,
    limit,
    has_min_score: minScoreText.trim() !== '',
    hits: hits.length,
  });

  const sourceOptions = [
    { value: '', label: tr('全部来源', 'All sources') },
    { value: 'manual', label: tr('手动', 'Manual') },
    { value: 'upload', label: tr('上传', 'Uploaded') },
    { value: 'url', label: 'URL' },
    { value: 'git', label: 'Git' },
    { value: 'repo', label: 'repo' },
  ];

  const scopeModeOptions = [
    { value: 'all', label: tr('全库', 'Entire base') },
    { value: 'agent', label: tr('按助理范围', 'By assistant') },
    { value: 'custom', label: tr('自定义范围', 'Custom') },
  ];

  // 检索面板占位符随范围模式变化，让「搜的是谁的知识」始终可见
  const scopeLabel =
    scopeMode === 'agent'
      ? (() => {
          const agent = agents.find((a) => a.name === agentName);
          const label = agent ? `${agent.display_name}（${agent.name}）` : tr('未选助理', 'none');
          return scopeWire
            ? tr(`助理 ${label} 的知识范围`, `assistant ${label} scope`)
            : tr(`助理 ${label} · 空范围 = 全库`, `assistant ${label} · empty scope = all`);
        })()
      : scopeMode === 'custom'
        ? scopeWire
          ? tr('自定义范围', 'custom scope')
          : tr('自定义范围 · 未配置 = 全库', 'custom scope · unset = all')
        : tr('全库', 'entire knowledge base');

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tr('检索试跑', 'Retrieval playground')}
        subtitle={tr(
          '按助理范围与 RRF 阈值试调检索；点结果可定位命中块',
          'Tune retrieval by assistant scope and RRF threshold; open a hit to locate its chunk',
        )}
      />
      <FilterBar
        className="mx-6 mt-3"
        footer={
          scopeMode !== 'all' ? (
            preview ? (
              <>
                <Chip dense tone={preview.doc_count > 0 ? 'success' : 'warning'}>
                  {tr(`范围覆盖 ${preview.doc_count} 篇`, `${preview.doc_count} docs in scope`)}
                </Chip>
                {preview.unknown_paths.length > 0 && (
                  <Chip dense tone="warning">
                    {tr(
                      `${preview.unknown_paths.length} 个目录已不存在`,
                      `${preview.unknown_paths.length} missing folder(s)`,
                    )}
                  </Chip>
                )}
                {preview.unknown_doc_ids.length > 0 && (
                  <Chip dense tone="warning">
                    {tr(
                      `${preview.unknown_doc_ids.length} 篇文档已不存在`,
                      `${preview.unknown_doc_ids.length} missing doc(s)`,
                    )}
                  </Chip>
                )}
              </>
            ) : (
              <span className="text-[11px] text-zinc-500">
                {scopeWire
                  ? tr('范围预览加载中…', 'Scope preview loading…')
                  : tr('范围为空，按全库试跑', 'Scope is empty; running on the entire base')}
              </span>
            )
          ) : null
        }
      >
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
          label={tr('来源组', 'Source group')}
          active={group !== 'all'}
          onClear={() => {
            setGroup('all');
            setSourceType('');
          }}
          width="w-[7.5rem]"
        >
          <Select
            aria-label={tr('来源组', 'Source group')}
            value={group}
            onValueChange={(v) => {
              setGroup(v as 'all' | 'org' | 'builtin');
              if (v === 'builtin') setSourceType('');
            }}
            options={[
              { value: 'all', label: tr('全部', 'All') },
              { value: 'org', label: tr('组织', 'Org') },
              { value: 'builtin', label: tr('内置', 'Built-in') },
            ]}
            variant="ghost"
            className="h-7 min-w-0 border-0 bg-transparent px-0 text-[12px] shadow-none"
          />
        </FilterPill>
        {group !== 'builtin' && (
          <FilterPill
            label={tr('来源', 'Source')}
            active={sourceType !== ''}
            onClear={() => setSourceType('')}
            width="w-[8.5rem]"
          >
            <Select
              aria-label={tr('来源', 'Source')}
              value={sourceType}
              onValueChange={(v) => setSourceType(v as DocSource | '')}
              options={sourceOptions}
              variant="ghost"
              className="h-7 min-w-0 border-0 bg-transparent px-0 text-[12px] shadow-none"
            />
          </FilterPill>
        )}
        <FilterPill
          label={tr('范围', 'Scope')}
          active={scopeMode !== 'all'}
          onClear={() => {
            setScopeMode('all');
            setPreview(null);
          }}
          width="w-[9.5rem]"
        >
          <Select
            aria-label={tr('范围模式', 'Scope mode')}
            value={scopeMode}
            onValueChange={(v) => {
              setScopeMode(v as ScopeMode);
              setPreview(null);
            }}
            options={scopeModeOptions}
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
        {scopeMode === 'custom' && (
          <>
            <Input
              value={customPathsText}
              onChange={(e) => setCustomPathsText(e.target.value)}
              placeholder={tr('目录范围，逗号/换行分隔（如 diagnostics,systems）', 'Folder paths, comma/newline separated')}
              aria-label={tr('自定义目录范围', 'Custom folder scope')}
              className="h-8 w-[18rem] text-[12px]"
            />
            <Input
              value={customDocIdsText}
              onChange={(e) => setCustomDocIdsText(e.target.value)}
              placeholder={tr('文档 id，逗号/换行分隔', 'Document ids, comma/newline separated')}
              aria-label={tr('自定义文档范围', 'Custom document scope')}
              className="h-8 w-[14rem] text-[12px]"
            />
          </>
        )}
      </FilterBar>

      {err && (
        <div className="mx-6 mt-3">
          <ErrorBanner>{err}</ErrorBanner>
        </div>
      )}

      <SemanticSearchPanel
        query={query}
        onQueryChange={setQuery}
        onRun={() => void runSearch()}
        onReset={resetSearch}
        searchedQuery={searchedQuery}
        hits={hits}
        scopeLabel={scopeLabel}
        onOpenHit={(hit) => {
          setOpenHit({
            doc: hit.doc,
            locate: { chunkIndex: hit.chunk_index, excerpt: hit.excerpt },
          });
        }}
        noHitsHint={tr(
          '换个说法，或在上方调低 RRF 阈值重试。',
          'Try another phrase, or lower the RRF threshold above and retry.',
        )}
      />

      {openHit && (
        <DocPreviewModal
          doc={openHit.doc}
          locate={openHit.locate}
          onClose={() => setOpenHit(null)}
        />
      )}
    </main>
  );
}
