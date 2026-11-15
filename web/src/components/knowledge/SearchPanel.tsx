// 知识检索共享组件：命中行 / 语义检索面板 / 命中块定位横幅。
// 知识库页与检索试跑页共用同一套结果呈现（分数栏、命中块编号、空态口径），
// 抽出来避免两处各维护一份后展示口径漂移。
import { Search, SearchX } from 'lucide-react';
import { Button, Chip, EmptyStateCard, FilterBar, SearchInput, TableCard, TableHead, COL, tableLayout } from '@/components/ui';
import { localizedDocTitle, localizedPath, type SearchHit } from '@/api/knowledge';
import { useI18n } from '@/i18n/locale';

export function SearchHitRow({ hit, rank, onOpen }: { hit: SearchHit; rank: number; onOpen(): void }) {
  const { tr } = useI18n();
  const title = localizedDocTitle(hit.doc);
  return (
    <tr>
      <td className="px-3 py-2 align-top font-sans text-[10.5px] tabular-nums text-zinc-600">
        {String(rank).padStart(2, '0')}
      </td>
      <td className="px-3 py-2">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <button
            type="button"
            onClick={onOpen}
            className="min-w-0 truncate text-left text-[12.5px] font-medium text-zinc-100 transition-colors hover:text-indigo-300"
            title={title}
          >
            {title}
          </button>
          {hit.doc.path && (
            <span className="max-w-[14rem] truncate text-[10px] text-zinc-500">
              {localizedPath(hit.doc.path)}
            </span>
          )}
          <Chip dense>{tr(`命中块 ${hit.chunk_index + 1}`, `chunk ${hit.chunk_index + 1}`)}</Chip>
        </div>
        <p className="mt-1 line-clamp-2 whitespace-pre-wrap break-words text-[11px] leading-5 text-zinc-400">
          {hit.excerpt}
        </p>
      </td>
      <td className="whitespace-nowrap px-3 py-2 text-right align-top text-[10px] leading-4 text-zinc-500">
        <span className="block">
          RRF <span className="font-sans text-zinc-300">{hit.score.toFixed(4)}</span>
        </span>
        {hit.vector_score !== null && (
          <span className="block">
            {tr('向量相似度', 'vector sim')} <span className="font-sans text-zinc-300">{hit.vector_score.toFixed(3)}</span>
          </span>
        )}
        {hit.keyword_rank !== null && (
          <span className="block">
            {tr('关键词排名', 'keyword rank')} <span className="text-zinc-300">#{hit.keyword_rank}</span>
          </span>
        )}
      </td>
    </tr>
  );
}

// 语义检索面板：语义检索是独立功能（页级视图），不是文档列表的筛选条件。
// 检索框 + 命中结果表都用共享组件（FilterBar / SearchInput / TableCard / TableHead）。
export function SemanticSearchPanel({
  query,
  onQueryChange,
  onRun,
  onReset,
  searchedQuery,
  hits,
  scopeLabel,
  onOpenHit,
  noHitsHint,
}: {
  query: string;
  onQueryChange(next: string): void;
  onRun(): void;
  onReset(): void;
  searchedQuery: string | null;
  hits: SearchHit[];
  scopeLabel: string;
  onOpenHit(hit: SearchHit): void;
  // 无命中提示可按页面语境覆盖（检索试跑页直接提示调阈值，不必绕去设置页）
  noHitsHint?: string;
}) {
  const { tr } = useI18n();
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden px-6 pb-5 pt-3">
      <FilterBar className="mb-3">
        <SearchInput
          value={query}
          onChange={onQueryChange}
          onKeyDown={(e) => {
            if (e.key === 'Enter') onRun();
          }}
          placeholder={tr(`语义检索：${scopeLabel}`, `Semantic search: ${scopeLabel}`)}
          ariaLabel={tr('语义检索', 'Semantic search')}
          className="min-w-[16rem] flex-1"
        />
        <Button
          variant="primary"
          size="sm"
          type="button"
          disabled={query.trim() === ''}
          onClick={onRun}
          className="inline-flex shrink-0 items-center gap-1.5"
        >
          <Search size={13} />
          {tr('检索', 'Search')}
        </Button>
        <span className="ml-auto shrink-0 pr-1 text-[11px] tabular-nums text-zinc-500">
          {searchedQuery !== null
            ? tr(`命中 ${hits.length} 条`, `${hits.length} hit(s)`)
            : tr('尚未检索', 'Not searched yet')}
        </span>
      </FilterBar>

      {searchedQuery === null ? (
        <EmptyStateCard
          icon={Search}
          title={tr('输入关键词做语义检索', 'Enter a keyword to search')}
          hint={tr(
            '按整句提问或描述检索；命中的是文档切块而不是标题。',
            'Ask with a phrase; matches are document chunks, not titles.',
          )}
        />
      ) : hits.length === 0 ? (
        <EmptyStateCard
          icon={SearchX}
          title={tr(`没有命中「${searchedQuery}」的切块`, `No chunks matched "${searchedQuery}"`)}
          hint={
            noHitsHint ??
            tr(
              '换个更具体的说法，或在「设置 → 知识库」里调低最低 RRF 分。',
              'Try a more specific phrase, or lower the minimum RRF score in Settings → Knowledge.',
            )
          }
          action={
            <Button variant="outline" size="sm" onClick={onReset}>
              {tr('重新检索', 'Search again')}
            </Button>
          }
        />
      ) : (
        <TableCard className="min-h-0 flex-1">
          <table {...tableLayout(32)}>
            <colgroup>
              <col className={COL.index} />
              <col className={COL.main} />
              <col className={COL.tag} />
            </colgroup>
            <TableHead>
              <tr className="whitespace-nowrap">
                <th scope="col" className="px-3 py-2 text-left font-medium">#</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">{tr('文档', 'Document')}</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">{tr('检索得分', 'Score')}</th>
              </tr>
            </TableHead>
            <tbody className="divide-y divide-zinc-800/40">
              {hits.map((hit, index) => (
                <SearchHitRow key={hit.doc.id} rank={index + 1} hit={hit} onOpen={() => onOpenHit(hit)} />
              ))}
            </tbody>
          </table>
        </TableCard>
      )}
    </div>
  );
}

export function LocateBanner({
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
        <div className="mt-1 line-clamp-4 whitespace-pre-wrap break-words font-sans text-[10.5px] text-zinc-400">
          {locate.excerpt}
        </div>
      )}
    </div>
  );
}
