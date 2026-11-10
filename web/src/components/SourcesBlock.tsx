// SourcesBlock — 统一「来源」区：知识库命中与网页搜索的标题 / 链接 / 摘录。
//
// 工具结果里承载引用的字段来自两处契约：
//   - query_knowledge → knowledge_service.search_for_agent：items[doc_id,title,path,chunk_index,excerpt,score]
//   - web_search      → web_search_service.deepseek_web_search：results[title,url,snippet,summary,date,rerank_score]
// 其它名称里带 search / knowledge 的工具按 items|results 的通用形状读取。
import { useI18n } from '@/i18n/locale';
import { cn } from '@/lib/cn';

export type SourceKind = 'knowledge' | 'web' | 'other';

export type SourceItem = {
  /** 去重键：同一文档/网页在多次检索里只展示一次。 */
  key: string;
  kind: SourceKind;
  title: string;
  href?: string;
  path?: string;
  excerpt?: string;
  score?: number;
  chunkIndex?: number;
};

function pickList(result: Record<string, unknown>): unknown[] | null {
  if (Array.isArray(result.items)) return result.items;
  if (Array.isArray(result.results)) return result.results;
  return null;
}

/** 从工具结果里抽取可引用的来源；不是检索类工具或形状不符时返回空数组。 */
export function extractSources(toolName: string, result: unknown): SourceItem[] {
  if (result === null || typeof result !== 'object') return [];
  const isSearchTool =
    toolName === 'query_knowledge' ||
    toolName === 'web_search' ||
    toolName.includes('search') ||
    toolName.includes('knowledge');
  if (!isSearchTool) return [];
  const list = pickList(result as Record<string, unknown>);
  if (list === null) return [];

  const out: SourceItem[] = [];
  for (const raw of list) {
    if (raw === null || typeof raw !== 'object') continue;
    const row = raw as Record<string, unknown>;
    if (typeof row.title !== 'string' || row.title === '') continue;
    const title = row.title;

    const url = typeof row.url === 'string' && row.url !== '' ? row.url : '';
    const docId = typeof row.doc_id === 'string' && row.doc_id !== '' ? row.doc_id : '';
    const href = url !== '' ? url : docId !== '' ? `/knowledge?doc=${encodeURIComponent(docId)}` : undefined;
    const kind: SourceKind = url !== '' ? 'web' : docId !== '' ? 'knowledge' : 'other';

    const item: SourceItem = { key: '', kind, title };
    if (href !== undefined) item.href = href;
    if (typeof row.path === 'string' && row.path !== '') item.path = row.path;
    const excerpt =
      typeof row.excerpt === 'string'
        ? row.excerpt
        : typeof row.snippet === 'string'
          ? row.snippet
          : typeof row.summary === 'string'
            ? row.summary
            : '';
    if (excerpt !== '') item.excerpt = excerpt;
    const score = typeof row.score === 'number' ? row.score : row.rerank_score;
    if (typeof score === 'number') item.score = score;
    if (typeof row.chunk_index === 'number') item.chunkIndex = row.chunk_index;
    item.key = `${kind}|${href !== undefined ? href : title}|${item.chunkIndex !== undefined ? item.chunkIndex : ''}`;
    out.push(item);
  }
  return out;
}

/** 合并多条工具调用的来源，按 key 去重并保持首次出现顺序。 */
export function mergeSources(lists: SourceItem[][]): SourceItem[] {
  const seen = new Set<string>();
  const out: SourceItem[] = [];
  for (const list of lists) {
    for (const item of list) {
      if (seen.has(item.key)) continue;
      seen.add(item.key);
      out.push(item);
    }
  }
  return out;
}

/** 助手气泡内聚合本次回答用到的全部来源。 */
export function collectToolSources(
  calls: { name: string; result?: unknown }[] | undefined,
): SourceItem[] {
  if (calls === undefined) return [];
  return mergeSources(calls.map((call) => extractSources(call.name, call.result)));
}

export function SourcesSection({
  sources,
  className,
}: {
  sources: SourceItem[];
  className?: string;
}) {
  const { tr } = useI18n();
  if (sources.length === 0) return null;
  return (
    <section className={cn('space-y-1.5', className)} aria-label={tr('来源', 'Sources')}>
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-zinc-500">
        {tr('来源', 'Sources')}
        <span className="text-zinc-600">({sources.length})</span>
      </div>
      <ol className="space-y-1.5">
        {sources.map((s, i) => (
          <li
            key={s.key}
            className="rounded-lg border border-zinc-800/60 bg-zinc-900/40 px-2.5 py-1.5"
          >
            <div className="flex items-baseline gap-2">
              <span className="shrink-0 font-mono text-[10px] text-zinc-500">[{i + 1}]</span>
              {s.href !== undefined ? (
                <a
                  href={s.href}
                  className="truncate text-[12px] text-sky-300 hover:underline"
                  target={s.href.startsWith('http') ? '_blank' : undefined}
                  rel={s.href.startsWith('http') ? 'noreferrer' : undefined}
                >
                  {s.title}
                </a>
              ) : (
                <span className="truncate text-[12px] text-zinc-200">{s.title}</span>
              )}
              <span className="ml-auto flex shrink-0 items-center gap-1.5 font-mono text-[10px] text-zinc-500">
                {s.chunkIndex !== undefined && (
                  <span>{tr(`第 ${s.chunkIndex + 1} 块`, `chunk ${s.chunkIndex + 1}`)}</span>
                )}
                {s.score !== undefined && <span>{s.score.toFixed(3)}</span>}
              </span>
            </div>
            {s.path !== undefined && (
              <div className="mt-0.5 truncate pl-6 font-mono text-[10px] text-zinc-600">{s.path}</div>
            )}
            {s.excerpt !== undefined && (
              <p className="mt-1 line-clamp-3 pl-6 text-[11px] leading-relaxed text-zinc-500">
                {s.excerpt}
              </p>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
