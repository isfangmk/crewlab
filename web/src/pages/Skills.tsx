import { useAssistantPage } from '@/lib/assistantPage';
import { usePermissions } from '@/store/me';
import { EmptyStateCard,
  ErrorBanner, TableHead } from '@/components/ui';
import { Button } from '@/components/ui';
import { Hint } from '@/components/ui/Tooltip';
import { useTimeout } from '@/lib/useTimeout';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ChevronDown,
  ChevronRight,
  Download,
  FilePlus2,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
  Wrench,
} from 'lucide-react';
import { cn } from '@/lib/cn';
import {
  deleteSkill,
  exportSkillZip,
  listSkills,
  localizedSkill,
  reloadSkills,
  type SkillClass,
  type SkillSummary,
} from '@/api/skills';
import { mcpToolDisplayName } from '@/lib/mcpWire';
import { Modal } from '@/components/Modal';
import { useI18n } from '@/i18n/locale';
import { toolGroupKey, groupTag, groupTitle, orderedGroupKeys } from '@/lib/toolSkill';
import { ChoiceChip, Chip, CodeBlock, DetailSection, FilterBar, PageHeader, RowAction, SearchInput, StatusPill, TableCard } from '@/components/ui';
import type { StatusTone } from '@/components/ui/StatusPill';
import { SkillEditor, SkillImportDialog } from '@/components/SkillEditor';
import { errorText } from '@/lib/errors';

export default function SkillsPage() {
  // 本页展示内置（backend/skills）+ 用户自定义技能目录
  return <CatalogTab />;
}

type EditorState = { mode: 'create' | 'edit'; key?: string } | null;

function CatalogTab() {
  const { tr, locale } = useI18n();
  const { canMutate } = usePermissions();
  const [items, setItems] = useState<SkillSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<string>('');
  // 详情弹窗：只读查看技能包内容（MCP 工具在 /tools 与 /mcp，不混入本页）
  const [viewing, setViewing] = useState<SkillSummary | null>(null);
  const [editor, setEditor] = useState<EditorState>(null);
  const [importing, setImporting] = useState(false);
  const [deleting, setDeleting] = useState<SkillSummary | null>(null);

  const fetchSkills = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true);
    else setLoading(true);
    try {
      const sk = await listSkills();
      setItems((sk.items ?? []).map(localizedSkill));
      setErr(null);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void fetchSkills();
  }, [fetchSkills]);

  // flash 用自动清理的定时器：卸载后不再对已卸载组件 setState
  const flashTimer = useTimeout();
  const flash = useCallback((message: string) => {
    setNotice(message);
    flashTimer(() => setNotice(null), 4000);
  }, [flashTimer]);

  // 重载：让后端重新扫描本地 SKILL.md，成功后再刷新表格。
  const reloadCatalog = useCallback(async () => {
    setRefreshing(true);
    try {
      const sk = await reloadSkills();
      if (!Array.isArray(sk.items)) throw new Error('技能列表缺少 items');
      setItems(sk.items.map(localizedSkill));
      setErr(null);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setRefreshing(false);
    }
  }, []);

  const runExport = useCallback(
    async (skill: SkillSummary) => {
      try {
        const filename = await exportSkillZip(skill.key);
        flash(tr(`已导出 ${filename}`, `Exported ${filename}`));
      } catch (e) {
        setErr(errorText(e));
      }
    },
    [flash, tr],
  );

  const runDelete = useCallback(async () => {
    if (deleting === null) return;
    try {
      await deleteSkill(deleting.key);
      flash(tr(`已删除技能 ${deleting.key}`, `Deleted skill ${deleting.key}`));
      setDeleting(null);
      await fetchSkills(true);
    } catch (e) {
      setErr(errorText(e));
      setDeleting(null);
    }
  }, [deleting, fetchSkills, flash, tr]);

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

  const userSkillCount = items.filter((s) => s.source === 'user').length;

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tr('技能', 'Skills')}
        subtitle={tr(
          `可装配到助理的能力包（内置 ${items.length - userSkillCount} · 自定义 ${userSkillCount}）`,
          `Capability packs for assistants (built-in ${items.length - userSkillCount} · custom ${userSkillCount})`,
        )}
        actions={
          <>
            {canMutate && (
              <>
                <RowAction icon={FilePlus2} disabled={loading || refreshing} onClick={() => setImporting(true)}>
                  {tr('导入 ZIP', 'Import ZIP')}
                </RowAction>
                <RowAction icon={Plus} disabled={loading || refreshing} onClick={() => setEditor({ mode: 'create' })}>
                  {tr('新建技能', 'New skill')}
                </RowAction>
              </>
            )}
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
                  label={c === 'mcp' ? 'MCP' : c === 'ext' ? tr('自定义', 'Custom') : groupTitle(c, locale === 'zh-CN')}
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
            <ErrorBanner>
              {tr('操作失败：', 'Operation failed: ')}{err}
            </ErrorBanner>
          )}
          {notice && (
            <div className="mb-3 rounded-xl border border-emerald-500/40 bg-emerald-500/5 px-4 py-3 text-[12px] text-emerald-300">
              {notice}
            </div>
          )}
          {loading ? (
            <div className="flex h-40 items-center justify-center text-sm text-zinc-500">{tr('加载中…', 'Loading…')}</div>
          ) : err && items.length === 0 ? null : filtered.length === 0 ? (
            <EmptyState hasItems={!!(query.trim() || category)} onClear={() => { setQuery(''); setCategory(''); }} />
          ) : (
            <TableCard className="min-h-0 flex-1">
              <table className="og-action-table w-full text-sm">
                <TableHead>
                  <tr className="whitespace-nowrap">
                    <th className="px-3 py-2 text-left font-medium">{tr('名称', 'Name')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('类别', 'Class')}</th>
                    <th className="w-full max-w-0 px-3 py-2 text-left font-medium">{tr('描述', 'Description')}</th>
                    <th className="px-3 py-2 text-right font-medium">{tr('操作', 'Actions')}</th>
                  </tr>
                </TableHead>
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
                            {tag === 'ext' && <Chip tone="accent" dense>{tr('自定义', 'custom')}</Chip>}
                            <span className="ml-1 text-[11px] text-zinc-500">{rows.length} {tr('个', 'skills')}</span>
                          </div>
                        </td>
                      </tr>
                      {!isCollapsed &&
                        rows.map((skill) => (
                          <SkillRow
                            key={skill.key}
                            skill={skill}
                            displayName={skill.name}
                            canMutate={canMutate}
                            onView={() => setViewing(skill)}
                            onEdit={() => setEditor({ mode: 'edit', key: skill.key })}
                            onExport={() => void runExport(skill)}
                            onDelete={() => setDeleting(skill)}
                          />
                        ))}
                    </tbody>
                  );
                })}
              </table>
            </TableCard>
          )}
        </div>
        {viewing && (
          <SkillDetailModal
            skill={viewing}
            canMutate={canMutate}
            onClose={() => setViewing(null)}
            onEdit={() => {
              setEditor({ mode: 'edit', key: viewing.key });
              setViewing(null);
            }}
            onExport={() => void runExport(viewing)}
          />
        )}
        {editor && (
          <SkillEditor
            mode={editor.mode}
            existingKey={editor.key}
            onClose={() => setEditor(null)}
            onSaved={(skill, mode) => {
              setEditor(null);
              flash(
                mode === 'create'
                  ? tr(`已创建技能 ${skill.key}`, `Created skill ${skill.key}`)
                  : tr(`已更新技能 ${skill.key}`, `Updated skill ${skill.key}`),
              );
              setItems((cur) => {
                const rest = cur.filter((item) => item.key !== skill.key);
                return [...rest, localizedSkill(skill)].sort((a, b) => a.key.localeCompare(b.key));
              });
            }}
          />
        )}
        {importing && (
          <SkillImportDialog
            onClose={() => setImporting(false)}
            onImported={() => {
              setImporting(false);
              flash(tr('技能包已导入', 'Skill pack imported'));
              void fetchSkills(true);
            }}
          />
        )}
        {deleting && (
          <Modal
            open
            onClose={() => setDeleting(null)}
            title={tr('删除技能', 'Delete skill')}
            footer={
              <>
                <Button variant="ghost" onClick={() => setDeleting(null)}>
                  {tr('取消', 'Cancel')}
                </Button>
                <Button variant="danger" onClick={() => void runDelete()}>
                  {tr('删除', 'Delete')}
                </Button>
              </>
            }
          >
            <p className="text-[12px] leading-relaxed text-zinc-300">
              {tr(
                `确定删除技能 ${deleting.key}？已装配该技能的助理会退化为「不装配该技能」，需要重新保存助理配置。`,
                `Delete skill ${deleting.key}? Assistants that mounted it will read as missing until you re-save them.`,
              )}
            </p>
          </Modal>
        )}
      </div>
    </main>
  );
}

function SkillRow({
  skill,
  displayName,
  canMutate,
  onView,
  onEdit,
  onExport,
  onDelete,
}: {
  skill: SkillSummary;
  displayName?: string;
  canMutate: boolean;
  onView(): void;
  onEdit(): void;
  onExport(): void;
  onDelete(): void;
}) {
  const { tr } = useI18n();
  const editable = canMutate && skill.source === 'user';
  const resourceCount =
    (skill.references?.length ?? 0) + (skill.assets?.length ?? 0) + (skill.scripts?.length ?? 0);
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
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 font-mono text-[10px] text-zinc-600">
          {skill.category && <span>{skill.category}</span>}
          {Array.isArray(skill.tools) && skill.tools.length > 0 && (
            <span>tools: {skill.tools.join(', ')}</span>
          )}
          {resourceCount > 0 && (
            <span>
              refs {skill.references?.length ?? 0} · assets {skill.assets?.length ?? 0} · scripts {skill.scripts?.length ?? 0}
            </span>
          )}
          {skill.inventory_only ? <span>· {tr('不可装配', 'not assemblable')}</span> : null}
        </div>
      </td>
      <td className="whitespace-nowrap px-3 py-2 text-right">
        <div className="flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()}>
          <Hint content={tr('导出为 ZIP 技能包', 'Export as a ZIP skill pack')}>
            <Button
              variant="ghost"
              size="sm"
              type="button"
              className="px-1.5 py-0 text-[11px]"
              aria-label={tr(`导出技能 ${skill.key}`, `Export skill ${skill.key}`)}
              onClick={onExport}
            >
              <Download size={11} />
            </Button>
          </Hint>
          {editable ? (
            <>
              <Hint content={tr('编辑技能包', 'Edit skill pack')}>
                <Button
                  variant="ghost"
                  size="sm"
                  type="button"
                  className="px-1.5 py-0 text-[11px]"
                  aria-label={tr(`编辑技能 ${skill.key}`, `Edit skill ${skill.key}`)}
                  onClick={onEdit}
                >
                  <Pencil size={11} />
                </Button>
              </Hint>
              <Hint content={tr('删除技能', 'Delete skill')}>
                <Button
                  variant="dangerGhost"
                  size="sm"
                  type="button"
                  className="px-1.5 py-0 text-[11px]"
                  aria-label={tr(`删除技能 ${skill.key}`, `Delete skill ${skill.key}`)}
                  onClick={onDelete}
                >
                  <Trash2 size={11} />
                </Button>
              </Hint>
            </>
          ) : (
            <Hint
              content={
                skill.source === 'disk'
                  ? tr('内置技能随代码发布，不能在线编辑', 'Built-in skills ship with the code and cannot be edited here')
                  : tr('需要开发或管理员权限', 'Requires developer or admin role')
              }
            >
              <Chip tone="info">{tr('只读', 'read-only')}</Chip>
            </Hint>
          )}
        </div>
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

// SkillDetailModal — 技能包详情：frontmatter 字段 + 完整体检 + 三类资源清单。
function SkillDetailModal({
  skill,
  canMutate,
  onClose,
  onEdit,
  onExport,
}: {
  skill: SkillSummary;
  canMutate: boolean;
  onClose(): void;
  onEdit(): void;
  onExport(): void;
}) {
  const { tr } = useI18n();
  const editable = canMutate && skill.source === 'user';
  const fileGroups = [
    { kind: 'references', label: tr('参考资料 references/', 'References references/'), files: skill.references ?? [] },
    { kind: 'assets', label: tr('素材 assets/', 'Assets assets/'), files: skill.assets ?? [] },
    { kind: 'scripts', label: tr('脚本 scripts/', 'Scripts scripts/'), files: skill.scripts ?? [] },
  ];
  return (
    <Modal
      open
      onClose={onClose}
      size="lg"
      resizable
      title={skill.name}
      footer={
        <>
          <span className="mr-auto text-[11px] text-zinc-500">
            {skill.source === 'disk'
              ? tr('内置技能：随 backend/skills 发布', 'Built-in skill: shipped under backend/skills')
              : tr('自定义技能：存于数据库，可编辑', 'Custom skill: stored in the database, editable')}
          </span>
          <Button variant="ghost" onClick={onExport}>
            {tr('导出 ZIP', 'Export ZIP')}
          </Button>
          {editable && <Button onClick={onEdit}>{tr('编辑', 'Edit')}</Button>}
        </>
      }
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <ClassBadge value={skill.class} />
          {skill.category && <Chip>{skill.category}</Chip>}
          {skill.inject_state && <Chip tone="accent">adk_inject_state</Chip>}
          <span className="ml-auto font-mono text-[11px] text-zinc-500">{skill.key}</span>
        </div>

        <DetailSection title={tr('描述', 'Description')}>
          <div className="whitespace-pre-wrap rounded-lg border border-zinc-800/60 bg-zinc-900/30 p-3 text-[12px] leading-relaxed text-zinc-300">
            {skill.description || '—'}
          </div>
        </DetailSection>

        {(skill.license || skill.compatibility || skill.preapproved_tools) && (
          <DetailSection title={tr('frontmatter', 'Frontmatter')}>
            <dl className="grid grid-cols-1 gap-2 text-[12px] md:grid-cols-3">
              <div>
                <dt className="text-[11px] text-zinc-500">license</dt>
                <dd className="font-mono text-zinc-300">{skill.license || '—'}</dd>
              </div>
              <div>
                <dt className="text-[11px] text-zinc-500">compatibility</dt>
                <dd className="text-zinc-300">{skill.compatibility || '—'}</dd>
              </div>
              <div>
                <dt className="text-[11px] text-zinc-500">allowed-tools</dt>
                <dd className="font-mono text-zinc-300">{skill.preapproved_tools || '—'}</dd>
              </div>
            </dl>
          </DetailSection>
        )}

        <DetailSection title={tr('SKILL.md 正文', 'SKILL.md body')}>
          {skill.instructions ? (
            <CodeBlock>{skill.instructions}</CodeBlock>
          ) : (
            <div className="text-[12px] text-zinc-500">{tr('没有正文', 'No body')}</div>
          )}
        </DetailSection>

        <DetailSection title={tr('可调用工具', 'Declared tools')}>
          {Array.isArray(skill.tools) && skill.tools.length > 0 ? (
            <div className="flex flex-wrap gap-1">
              {(skill.tool_refs ?? skill.tools.map((name) => ({ name, kind: 'builtin' as const, available: true, description: '' }))).map(
                (ref) => (
                  <Hint
                    key={ref.name}
                    content={
                      ref.kind === 'mcp'
                        ? ref.available
                          ? tr(`MCP Server ${ref.server} 的工具`, `Tool of MCP server ${ref.server}`)
                          : tr(
                              `MCP Server ${ref.server} 未启用或探测失败，运行时不会挂载`,
                              `MCP server ${ref.server} is disabled or unverified; it will not be mounted`,
                            )
                        : ref.kind === 'unknown'
                          ? tr('目录里没有这个工具，运行时无法调用', 'This tool is not in the catalog and cannot be called')
                          : ref.description
                    }
                  >
                    <Chip
                      dense
                      tone={
                        !ref.available ? 'warning' : ref.kind === 'mcp' ? 'accent' : 'default'
                      }
                    >
                      {ref.kind === 'mcp' ? `mcp · ${ref.server} · ${mcpToolDisplayName(ref.name)}` : ref.name}
                    </Chip>
                  </Hint>
                ),
              )}
            </div>
          ) : (
            <div className="text-[12px] text-zinc-500">
              {tr('纯说明型技能：不挂任何工具', 'Instruction-only skill: no tools declared')}
            </div>
          )}
          {skill.params.length > 0 && (
            <div className="mt-2 overflow-hidden rounded-lg border border-zinc-800/60">
              <table className="og-action-table w-full text-[12px]">
                <thead className="border-b border-zinc-800/60 text-[11px] text-zinc-500">
                  <tr className="whitespace-nowrap">
                    <th className="px-3 py-2 text-left font-medium">{tr('参数', 'Param')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('类型', 'Type')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('必填', 'Required')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('默认值', 'Default')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('说明', 'Description')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/60">
                  {skill.params.map((p, index) => (
                    <tr key={`${p.name}-${index}`}>
                      <td className="whitespace-nowrap px-3 py-2 font-mono text-zinc-200">{p.name}</td>
                      <td className="whitespace-nowrap px-3 py-2 text-zinc-400">{p.type}</td>
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

        {fileGroups.map((group) => (
          <DetailSection key={group.kind} title={group.label}>
            {group.files.length === 0 ? (
              <div className="text-[12px] text-zinc-500">{tr('无', 'None')}</div>
            ) : (
              <ul className="divide-y divide-zinc-800/60 overflow-hidden rounded-lg border border-zinc-800/60">
                {group.files.map((file) => (
                  <li key={file.path} className="flex items-center gap-2 px-3 py-1.5 text-[12px]">
                    <span className="truncate font-mono text-zinc-300">{file.path}</span>
                    {file.binary && <Chip dense tone="warning">{tr('二进制', 'binary')}</Chip>}
                    <span className="ml-auto shrink-0 tabular-nums text-[11px] text-zinc-500">{file.size} B</span>
                  </li>
                ))}
              </ul>
            )}
          </DetailSection>
        ))}

        <div className="flex items-center justify-between gap-3 border-t border-zinc-800/60 pt-3 text-[11px] text-zinc-500">
          <span>
            {tr(
              '技能由 AI 在对话中按需加载，不提供手动执行表单；scripts 需要沙箱才能运行。',
              'Skills are loaded on demand in chat; scripts need a sandbox to run.',
            )}
          </span>
          <span>{tr('仅 AI 调用', 'AI only')}</span>
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
      title={hasItems ? tr('没有匹配的技能', 'No matching skills') : tr('还没有技能', 'No skills yet')}
      hint={hasItems ? tr('换个关键字或清除分类筛选', 'Try a different keyword or clear filters') : tr('点右上角「新建技能」或「导入 ZIP」开始', 'Use "New skill" or "Import ZIP" in the header to start')}
    />
  );
}
