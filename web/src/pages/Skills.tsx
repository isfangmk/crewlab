import { useAssistantPage } from '@/lib/assistantPage';
import { EmptyStateCard } from '@/components/ui';
import { Label, Input, Button } from '@/components/ui';
import { Hint } from '@/components/ui/Tooltip';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Wrench, RefreshCw, Search, ChevronDown, ChevronRight } from 'lucide-react';
import { cn } from '@/lib/cn';
import { listSkills, localizedSkill, type SkillClass, type SkillSummary } from '@/api/skills';
import { ApiError } from '@/api/client';
import { Modal } from '@/components/Modal';
import { useI18n } from '@/i18n/locale';
import { toolGroupKey, groupTag, groupTitle, orderedGroupKeys } from '@/lib/toolSkill';
import { PageHeader } from '@/components/ui';

export default function SkillsPage() {
  const { tr } = useI18n();
  // 本页只展示本地 Skill 目录
  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tr('技能', 'Skills')}
        subtitle={tr('LLM 当前可见的能力', 'Capabilities currently visible to the LLM')}
      />
      <CatalogTab />
    </main>
  );
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
    <div className="flex flex-1 flex-col overflow-hidden">
        <div className="border-b border-zinc-800 px-6 py-3">
          <div className="flex flex-wrap items-center gap-3">
            <Label className="relative block w-64">
              <span className="sr-only">{tr('搜索', 'Search')}</span>
              <Search size={12} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500" />
              <Input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={tr('搜索 name / key / description', 'Search name / key / description')}
                className="w-full pl-8 pr-2"
              />
            </Label>
            {chips.length > 0 && (
              <div className="inline-flex items-center gap-1.5 text-xs text-zinc-400">
                <span className="text-zinc-500">{tr('技能', 'Skill')}</span>
                <div className="flex flex-wrap gap-1">
                  <CategoryChip
                    active={category === ''}
                    label={tr('全部', 'All')}
                    onClick={() => setCategory('')}
                  />
                  {chips.map((c) => (
                    <CategoryChip
                      key={c}
                      active={category === c}
                      label={c === 'mcp' ? 'MCP' : c === 'ext' ? tr('扩展', 'Extensions') : groupTitle(c, locale === 'zh-CN')}
                      onClick={() => setCategory(c)}
                    />
                  ))}
                </div>
              </div>
            )}
            <div className="ml-auto flex items-center gap-2">
              <span className="text-xs text-zinc-500">
                {tr(`${items.length} 个 · 匹配 ${filtered.length}`, `${items.length} total · ${filtered.length} matched`)}
              </span>
              <Button variant="outline" size="sm"
                type="button"
                onClick={() => fetchSkills(true)}
                disabled={loading || refreshing}
                className="inline-flex items-center gap-1.5 px-2.5 py-1.5"
              >
                <RefreshCw size={12} className={cn(refreshing && 'animate-spin')} />
                {tr('刷新', 'Refresh')}
              </Button>
            </div>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-6">
          {err && (
            <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/5 px-4 py-3 text-sm text-red-300">
              {tr('加载失败：', 'Load failed: ')}{err}
            </div>
          )}
          {loading ? (
            <div className="flex h-40 items-center justify-center text-sm text-zinc-500">{tr('加载中…', 'Loading…')}</div>
          ) : err && items.length === 0 ? null : filtered.length === 0 ? (
            <EmptyState hasItems={!!(query.trim() || category)} onClear={() => { setQuery(''); setCategory(''); }} />
          ) : (
            <div className="overflow-hidden rounded-xl border border-zinc-800/60 bg-zinc-900/30">
              <table className="w-full text-sm">
                <thead className="border-b border-zinc-800/60 bg-zinc-950/40 text-[11px] uppercase tracking-wider text-zinc-500">
                  <tr className="whitespace-nowrap">
                    <th className="px-4 py-2.5 text-left">{tr('名称', 'Name')}</th>
                    <th className="px-4 py-2.5 text-left">{tr('类别', 'Class')}</th>
                    <th className="w-full px-4 py-2.5 text-left">{tr('描述', 'Description')}</th>
                    <th className="px-4 py-2.5 text-right">{tr('操作', 'Actions')}</th>
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
                            {isCollapsed ? <ChevronRight size={13} className="text-zinc-500" /> : <ChevronDown size={13} className="text-zinc-500" />}
                            <span className="text-sm font-medium text-zinc-200">
                              {groupTitle(gk, locale === 'zh-CN', rows[0]?.name)}
                            </span>
                            {tag === 'mcp' && <span className="rounded bg-sky-900/40 px-1 text-[9px] font-medium text-sky-300">mcp</span>}
                            {tag === 'ext' && <span className="rounded bg-violet-900/40 px-1 text-[9px] font-medium text-violet-300">{tr('扩展', 'extension')}</span>}
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
            </div>
          )}
        </div>
        {viewing && <SkillDetailModal skill={viewing} onClose={() => setViewing(null)} />}
      </div>
  );
}

function CategoryChip({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick(): void;
}) {
  return (
    <Button variant="outline" size="sm"
      type="button"
      onClick={onClick}
      className={cn(
        'rounded-md border px-2 py-0.5 text-[11px] transition-colors',
        active
          ? 'border-zinc-600 bg-zinc-800 text-zinc-100'
          : 'border-zinc-800 bg-zinc-900/50 text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-200'
      )}
    >
      {label}
    </Button>
  );
}

function SkillRow({ skill, displayName, onView }: { skill: SkillSummary; displayName?: string; onView(): void }) {
  const { tr } = useI18n();
  return (
    <tr className="cursor-pointer transition-colors hover:bg-zinc-900/40" onClick={onView}>
      <td className="whitespace-nowrap px-4 py-1.5 pl-8">
        <span className="text-xs text-zinc-200">{displayName ?? skill.name}</span>
        <Hint content={skill.key}><div className="font-mono text-[10px] text-zinc-600" >
          {skill.key}
        </div></Hint>
      </td>
      <td className="whitespace-nowrap px-4 py-1.5">
        <ClassBadge value={skill.class} />
      </td>
      {/* Description column is the truncate-absorber — w-full + max-w-0
          forces it to take all remaining horizontal space and clamp
          long copy, so identity columns (name / key / badges) never
          shrink below their natural width. */}
      <td className="w-full max-w-0 px-4 py-1.5 text-[11px] text-zinc-500">
        <div className="line-clamp-1">{skill.description || '—'}</div>
      </td>
      <td className="whitespace-nowrap px-4 py-1.5 text-right">
        <Hint
          content={tr(
            'Skill 由 AI 助手在对话中按需加载，不提供手动执行表单',
            'Skills are loaded on demand by the AI assistant in chat; no manual form',
          )}
        >
          <span className="text-[11px] text-zinc-500">{tr('仅 AI 调用', 'AI only')}</span>
        </Hint>
      </td>
    </tr>
  );
}

export function ClassBadge({ value }: { value: SkillClass }) {
  const { tr } = useI18n();
  const styles: Record<SkillClass, string> = {
    safe: 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30',
    mutating: 'bg-amber-500/10 text-amber-300 ring-amber-500/30',
    dangerous: 'bg-red-500/15 text-red-300 ring-red-500/40',
  };
  // safe = read-only, no side effects; mutating = changes state (write/
  // restart); dangerous = high-risk destructive. Drives the approval gate.
  const label: Record<SkillClass, string> = {
    safe: tr('安全', 'safe'),
    mutating: tr('变更', 'mutating'),
    dangerous: tr('危险', 'dangerous'),
  };
  return (
    <span
      className={cn(
        'rounded-md px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset',
        styles[value],
      )}
    >
      {label[value]}
    </span>
  );
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
          {skill.category && (
            <span className="rounded-md border border-zinc-800 bg-zinc-950/40 px-1.5 py-0.5 text-[10px] text-zinc-400">
              {skill.category}
            </span>
          )}
          <span className="ml-auto font-mono text-[11px] text-zinc-500">{skill.key}</span>
        </div>

        <section>
          <div className="mb-1.5 text-[11px] uppercase tracking-wider text-zinc-500">
            {tr('描述（LLM 工具描述原文）', 'Description (raw LLM tool description)')}
          </div>
          <div className="whitespace-pre-wrap rounded-md border border-zinc-800 bg-zinc-950/40 p-3 text-xs leading-relaxed text-zinc-300">
            {skill.description || '—'}
          </div>
        </section>

        <section>
          <div className="mb-1.5 text-[11px] uppercase tracking-wider text-zinc-500">
            {tr('参数', 'Parameters')}
          </div>
          {skill.params.length === 0 ? (
            <div className="text-xs text-zinc-500">
              {tr('无参数（由 AI 在对话中按工具签名调用）', 'No parameters (AI invokes tools by their signatures in chat)')}
            </div>
          ) : (
            <div className="overflow-hidden rounded-md border border-zinc-800">
              <table className="w-full text-xs">
                <thead className="border-b border-zinc-800 bg-zinc-950/40 text-[10px] uppercase tracking-wider text-zinc-500">
                  <tr>
                    <th className="px-3 py-1.5 text-left">{tr('名称', 'Name')}</th>
                    <th className="px-3 py-1.5 text-left">{tr('类型', 'Type')}</th>
                    <th className="px-3 py-1.5 text-left">{tr('必填', 'Required')}</th>
                    <th className="px-3 py-1.5 text-left">{tr('默认值', 'Default')}</th>
                    <th className="px-3 py-1.5 text-left">{tr('说明', 'Description')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/60">
                  {skill.params.map((p) => (
                    <tr key={p.name}>
                      <td className="whitespace-nowrap px-3 py-1.5 font-mono text-zinc-200">{p.name}</td>
                      <td className="whitespace-nowrap px-3 py-1.5 text-zinc-400">
                        {p.type}
                        {p.enum && p.enum.length > 0 && (
                          <span className="ml-1 text-zinc-500">({p.enum.join(' | ')})</span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-3 py-1.5 text-zinc-400">{p.required ? tr('是', 'Yes') : '—'}</td>
                      <td className="whitespace-nowrap px-3 py-1.5 font-mono text-zinc-400">
                        {p.default === undefined || p.default === null ? '—' : String(p.default)}
                      </td>
                      <td className="px-3 py-1.5 text-zinc-400">{p.desc || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {skill.result_preview && (
          <section>
            <div className="mb-1.5 text-[11px] uppercase tracking-wider text-zinc-500">
              {tr('返回示意', 'Result preview')}
            </div>
            <div className="rounded-md border border-zinc-800 bg-zinc-950/40 p-3 font-mono text-[11px] text-zinc-400">
              {skill.result_preview}
            </div>
          </section>
        )}

        <div className="flex items-center justify-between gap-3 border-t border-zinc-800 pt-3">
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
