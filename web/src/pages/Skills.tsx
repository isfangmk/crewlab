import { useAssistantPage } from '@/lib/assistantPage';
import { EmptyStateCard } from '@/components/ui';
import { Button } from '@/components/ui';
import { Hint } from '@/components/ui/Tooltip';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { RefreshCw, ChevronDown, ChevronRight, Wrench } from 'lucide-react';
import { cn } from '@/lib/cn';
import { listSkills, localizedSkill, reloadSkills, type SkillClass, type SkillSummary } from '@/api/skills';
import { ApiError } from '@/api/client';
import { Modal } from '@/components/Modal';
import { useI18n } from '@/i18n/locale';
import { toolGroupKey, groupTag, groupTitle, orderedGroupKeys } from '@/lib/toolSkill';
import { ChoiceChip, Chip, CodeBlock, DetailSection, FilterBar, PageHeader, RowAction, SearchInput, StatusPill, TableCard } from '@/components/ui';
import type { StatusTone } from '@/components/ui/StatusPill';

export default function SkillsPage() {
  // 本页只展示本地 Skill 目录
  return <CatalogTab />;
}

function CatalogTab() {
  const { tr, locale } = useI18n();
  const [items, setItems] = useState<SkillSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<string>('');
  // 详情弹窗：只读查看本地 Skill 目录（MCP 工具在 /tools 与 /mcp，不混入本页）
  const [viewing, setViewing] = useState<SkillSummary | null>(null);

  const fetchSkills = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true);
    else setLoading(true);
    try {
      const sk = await listSkills();
      setItems((sk.items ?? []).map(localizedSkill));
      setErr(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchSkills();
  }, [fetchSkills]);

  // 重载：让后端重新扫描本地 SKILL.md / MCP 目录，成功后再刷新表格。
  const reloadCatalog = useCallback(async () => {
    setRefreshing(true);
    try {
      const sk = await reloadSkills();
      if (!Array.isArray(sk.items)) throw new Error('技能列表缺少 items');
      setItems(sk.items.map(localizedSkill));
      setErr(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setRefreshing(false);
    }
  }, []);

  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const toggleGroup = (k: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      next.has(k) ? next.delete(k) : next.add(k);
      return next;
    });

  // 与流程面板共用分组：内置工具 → 技能类目，MCP → 按 server，SKILL.md → 按包。
  // Keyed by the WIRE name (s.key), not the localized display name.
  const skillKey = useCallback((s: SkillSummary) => toolGroupKey(s.key, s.source, s.category), []);
  const skills = useMemo(() => orderedGroupKeys(items.map(skillKey)), [items, skillKey]);
  // Filter chips stay bounded: the 6 built-in skills + a single "MCP" and a
  // single "扩展" meta-chip, instead of one chip per server / pack (which would
  // grow without limit). The TABLE still sections per server / pack.
  const chips = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const k of skills) {
      const c = k.startsWith('mcp:') ? 'mcp' : k.startsWith('skill:') ? 'ext' : k;
      if (!seen.has(c)) {
        seen.add(c);
        out.push(c);
      }
    }
    return out;
  }, [skills]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter((s) => {
      if (category) {
        const gk = skillKey(s);
        const ok = category === 'mcp' ? gk.startsWith('mcp:') : category === 'ext' ? gk.startsWith('skill:') : gk === category;
        if (!ok) return false;
      }
      if (!q) return true;
      return (
        s.name.toLowerCase().includes(q) ||
        s.key.toLowerCase().includes(q) ||
        (s.description ?? '').toLowerCase().includes(q)
      );
    });
  }, [items, query, category, skillKey]);

  useAssistantPage('技能目录', 'Skill catalog', {
    category, visible_skills: filtered.length, selected_skill: viewing?.name, loading,
  });

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tr('技能', 'Skills')}
        subtitle={tr('可装配到助理的能力目录', 'Capability catalog for assistants')}
        actions={
          <>
            <RowAction
              icon={RefreshCw}
              iconClassName={cn(refreshing && 'animate-spin')}
              disabled={loading || refreshing}
              onClick={() => void reloadCatalog()}
            >
              {tr('重载', 'Reload')}
            </RowAction>
            <RowAction
              icon={RefreshCw}
              iconClassName={cn(refreshing && 'animate-spin')}
              disabled={loading || refreshing}
              onClick={() => void fetchSkills(true)}
            >
              {tr('刷新', 'Refresh')}
            </RowAction>
          </>
        }
      />
      <div className="flex flex-1 flex-col overflow-hidden">
        <FilterBar className="mx-6 mt-3">
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder={tr('搜索 name / key / description', 'Search name / key / description')}
            ariaLabel={tr('搜索', 'Search')}
            className="min-w-[16rem] flex-1"
          />
          {chips.length > 0 && (
            <>
              <span className="pl-1 text-[11px] text-zinc-500">{tr('技能', 'Skill')}</span>
              <ChoiceChip
                active={category === ''}
                label={tr('全部', 'All')}
                onClick={() => setCategory('')}
              />
              {chips.map((c) => (
                <ChoiceChip
                  key={c}
                  active={category === c}
                  label={c === 'mcp' ? 'MCP' : c === 'ext' ? tr('扩展', 'Extensions') : groupTitle(c, locale === 'zh-CN')}
                  onClick={() => setCategory(c)}
                />
              ))}
            </>
          )}
          <span className="ml-auto text-[11px] tabular-nums text-zinc-500">
            {tr(`${items.length} 个 · 匹配 ${filtered.length}`, `${items.length} total · ${filtered.length} matched`)}
          </span>
        </FilterBar>

        <div className="flex min-h-0 flex-1 flex-col px-6 py-4">
          {err && (
            <div className="mb-3 rounded-xl border border-red-500/40 bg-red-500/5 px-4 py-3 text-[12px] text-red-300">
              {tr('加载失败：', 'Load failed: ')}{err}
            </div>
          )}
          {loading ? (
            <div className="flex h-40 items-center justify-center text-sm text-zinc-500">{tr('加载中…', 'Loading…')}</div>
          ) : err && items.length === 0 ? null : filtered.length === 0 ? (
            <EmptyState hasItems={!!(query.trim() || category)} onClear={() => { setQuery(''); setCategory(''); }} />
          ) : (
            <TableCard className="min-h-0 flex-1">
              <table className="og-action-table w-full text-sm">
                <thead className="sticky top-0 z-10 border-b border-zinc-800/60 bg-zinc-900/95 text-[11px] text-zinc-500 backdrop-blur">
                  <tr className="whitespace-nowrap">
                    <th className="px-3 py-2 text-left font-medium">{tr('名称', 'Name')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('类别', 'Class')}</th>
                    <th className="w-full max-w-0 px-3 py-2 text-left font-medium">{tr('描述', 'Description')}</th>
                    <th className="px-3 py-2 text-right font-medium">{tr('操作', 'Actions')}</th>
                  </tr>
                </thead>
                {orderedGroupKeys(filtered.map(skillKey)).map((gk) => {
                  const rows = filtered.filter((s) => skillKey(s) === gk);
                  const tag = groupTag(gk);
                  const isCollapsed = collapsed.has(gk);
                  return (
                    <tbody key={gk} className="divide-y divide-zinc-800/40">
                      <tr
                        className="cursor-pointer select-none border-t border-zinc-800/60 bg-zinc-950/50 hover:bg-zinc-900/50"
                        onClick={() => toggleGroup(gk)}
                      >
                        <td colSpan={4} className="px-3 py-2">
                          <div className="flex items-center gap-1.5">
                            <button
                              type="button"
                              aria-label={isCollapsed ? tr('展开分组', 'Expand group') : tr('折叠分组', 'Collapse group')}
                              onClick={(e) => {
                                e.stopPropagation();
                                toggleGroup(gk);
                              }}
                              className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-zinc-500 transition-colors hover:bg-zinc-800/60 hover:text-zinc-300"
                            >
                              {isCollapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
                            </button>
                            <span className="text-[12.5px] font-medium text-zinc-200">
                              {groupTitle(gk, locale === 'zh-CN', rows[0]?.name)}
                            </span>
                            {tag === 'mcp' && <Chip tone="info" dense>mcp</Chip>}
                            {tag === 'ext' && <Chip tone="accent" dense>{tr('扩展', 'extension')}</Chip>}
                            <span className="ml-1 text-[11px] text-zinc-500">{rows.length} {tr('个工具', 'tools')}</span>
                          </div>
                        </td>
                      </tr>
                      {!isCollapsed &&
                        rows.map((skill) => (
                          <SkillRow
                            key={skill.key}
                            skill={skill}
                            displayName={skill.name}
                            onView={() => setViewing(skill)}
                          />
                        ))}
                    </tbody>
                  );
                })}
              </table>
            </TableCard>
          )}
        </div>
        {viewing && <SkillDetailModal skill={viewing} onClose={() => setViewing(null)} />}
      </div>
    </main>
  );
}

function SkillRow({ skill, displayName, onView }: { skill: SkillSummary; displayName?: string; onView(): void }) {
  const { tr } = useI18n();
  return (
    <tr className="cursor-pointer transition-colors hover:bg-zinc-900/40" onClick={onView}>
      <td className="whitespace-nowrap px-3 py-2 pl-8">
        <span className="text-[12.5px] text-zinc-200">{displayName ?? skill.name}</span>
        <Hint content={skill.key}><div className="font-mono text-[10px] text-zinc-600" >
          {skill.key}
        </div></Hint>
      </td>
      <td className="whitespace-nowrap px-3 py-2">
        <ClassBadge value={skill.class} />
      </td>
      {/* Description column is the truncate-absorber — w-full + max-w-0
          forces it to take all remaining horizontal space and clamp
          long copy, so identity columns (name / key / badges) never
          shrink below their natural width. */}
      <td className="w-full max-w-0 px-3 py-2 text-[11px] text-zinc-500">
        <div className="line-clamp-1">{skill.description || '—'}</div>
        {Array.isArray(skill.tools) && skill.tools.length > 0 && (
          <div className="mt-0.5 font-mono text-[10px] text-zinc-600">
            tools: {skill.tools.join(', ')}
            {skill.inventory_only ? ` · ${tr('不可装配', 'not assemblable')}` : ''}
          </div>
        )}
      </td>
      <td className="whitespace-nowrap px-3 py-2 text-right">
        <Hint
          content={tr(
            'Skill 由 AI 助手在对话中按需加载，不提供手动执行表单',
            'Skills are loaded on demand by the AI assistant in chat; no manual form',
          )}
        >
          <Chip tone="info">{tr('仅 AI 调用', 'AI only')}</Chip>
        </Hint>
      </td>
    </tr>
  );
}

export function ClassBadge({ value }: { value: SkillClass }) {
  const { tr } = useI18n();
  // safe = 只读无副作用；mutating = 改变状态（写/重启）；dangerous = 高风险破坏性操作。
  const tones: Record<SkillClass, { tone: StatusTone; zh: string; en: string }> = {
    safe: { tone: 'ok', zh: '安全', en: 'safe' },
    mutating: { tone: 'warn', zh: '变更', en: 'mutating' },
    dangerous: { tone: 'danger', zh: '危险', en: 'dangerous' },
  };
  const meta = tones[value];
  return <StatusPill tone={meta.tone} label={tr(meta.zh, meta.en)} />;
}

// SkillDetailModal — 只读详情弹窗：顶部元信息（类别 / 分类 / key），正文完整描述 + 参数表。
// 技能由代码或技能包定义、启动时注册进内存 registry，没有可编辑的存储。
function SkillDetailModal({ skill, onClose }: { skill: SkillSummary; onClose(): void }) {
  const { tr } = useI18n();
  return (
    <Modal open onClose={onClose} size="lg" resizable title={skill.name}>
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <ClassBadge value={skill.class} />
          {skill.category && <Chip>{skill.category}</Chip>}
          <span className="ml-auto font-mono text-[11px] text-zinc-500">{skill.key}</span>
        </div>

        <DetailSection title={tr('描述', 'Description')}>
          <div className="whitespace-pre-wrap rounded-lg border border-zinc-800/60 bg-zinc-900/30 p-3 text-[12px] leading-relaxed text-zinc-300">
            {skill.description || '—'}
          </div>
        </DetailSection>

        <DetailSection title={tr('参数', 'Parameters')}>
          {skill.params.length === 0 ? (
            <div className="text-[12px] text-zinc-500">
              {tr('无参数（由 AI 在对话中按工具签名调用）', 'No parameters (AI invokes tools by their signatures in chat)')}
            </div>
          ) : (
            <div className="overflow-hidden rounded-lg border border-zinc-800/60">
              <table className="og-action-table w-full text-[12px]">
                <thead className="border-b border-zinc-800/60 text-[11px] text-zinc-500">
                  <tr className="whitespace-nowrap">
                    <th className="px-3 py-2 text-left font-medium">{tr('名称', 'Name')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('类型', 'Type')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('必填', 'Required')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('默认值', 'Default')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('说明', 'Description')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/60">
                  {skill.params.map((p) => (
                    <tr key={p.name}>
                      <td className="whitespace-nowrap px-3 py-2 font-mono text-zinc-200">{p.name}</td>
                      <td className="whitespace-nowrap px-3 py-2 text-zinc-400">
                        {p.type}
                        {p.enum && p.enum.length > 0 && (
                          <span className="ml-1 text-zinc-500">({p.enum.join(' | ')})</span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-zinc-400">{p.required ? tr('是', 'Yes') : '—'}</td>
                      <td className="whitespace-nowrap px-3 py-2 font-mono text-zinc-400">
                        {p.default === undefined || p.default === null ? '—' : String(p.default)}
                      </td>
                      <td className="px-3 py-2 text-zinc-400">{p.desc || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </DetailSection>

        {skill.result_preview && (
          <DetailSection title={tr('返回示意', 'Result preview')}>
            <CodeBlock>{skill.result_preview}</CodeBlock>
          </DetailSection>
        )}

        <div className="flex items-center justify-between gap-3 border-t border-zinc-800/60 pt-3">
          <span className="text-[11px] text-zinc-500">
            {tr('技能由代码 / 技能包定义，不可在线编辑', 'Skills are defined in code / skill packs and cannot be edited online')}
          </span>
          <span className="text-[11px] text-zinc-500">{tr('仅 AI 调用', 'AI only')}</span>
        </div>
      </div>
    </Modal>
  );
}

function EmptyState({ hasItems, onClear }: { hasItems: boolean; onClear: () => void }) {
  const { tr } = useI18n();
  return (
    <EmptyStateCard icon={Wrench}
      action={hasItems && <Button onClick={onClear}>{tr('清除筛选', 'Clear filters')}</Button>}
      title={hasItems ? tr('没有匹配的技能', 'No matching skills') : tr('还没有 skill 注册', 'No skills registered yet')}
      hint={hasItems ? tr('换个关键字或清除分类筛选', 'Try a different keyword or clear filters') : tr('在 backend/skills 放入 SKILL.md 后会出现在这里', 'Skills appear here after you add SKILL.md packs under backend/skills')}
    />
  );
}
