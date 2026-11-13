import { useAssistantPage } from '@/lib/assistantPage';
import { usePermissions } from '@/store/me';
import { COL, EmptyStateCard,
  ErrorBanner, TableHead, tableLayout } from '@/components/ui';
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
  listSkillGroups,
  listSkills,
  localizedSkill,
  reloadSkills,
  type SkillClass,
  type SkillGroup,
  type SkillGroupMeta,
  type SkillOrigin,
  type SkillSummary,
} from '@/api/skills';
import { mcpToolDisplayName } from '@/lib/mcpWire';
import { Modal } from '@/components/Modal';
import { useI18n } from '@/i18n/locale';
import { orderedGroupKeys } from '@/lib/toolSkill';
import { ChoiceChip, Chip, CodeBlock, DetailSection, FilterBar, PageHeader, RowAction, SearchInput, StatusPill, TableCard, Toast, type ToastTone } from '@/components/ui';
import type { StatusTone } from '@/components/ui/StatusPill';
import { SkillEditor, SkillImportDialog } from '@/components/SkillEditor';
import { errorText } from '@/lib/errors';

export default function SkillsPage() {
  // 本页展示内置（backend/skills）+ 用户自定义技能目录
  return <CatalogTab />;
}

type EditorState = { mode: 'create' | 'edit'; key?: string } | null;

/**
 * 技能来源类型：内置磁盘技能没有 origin 字段，按 builtin 处理；
 * 平台内新建与 zip 导入由后端记录区分。
 */
export function skillOrigin(s: SkillSummary): SkillOrigin {
  return s.origin ?? (s.source === 'disk' ? 'builtin' : 'created');
}

/** 来源类型的中文/英文名与胶囊色。 */
const ORIGIN_META: Record<SkillOrigin, { zh: string; en: string; tone: StatusTone }> = {
  builtin: { zh: '内置', en: 'Built-in', tone: 'info' },
  created: { zh: '新建', en: 'Created', tone: 'ok' },
  imported: { zh: 'ZIP 导入', en: 'Imported', tone: 'neutral' },
};

/** 来源筛选胶囊的展示顺序。 */
const ORIGIN_ORDER: SkillOrigin[] = ['builtin', 'created', 'imported'];

function CatalogTab() {
  const { tr } = useI18n();
  const { canMutate } = usePermissions();
  const [items, setItems] = useState<SkillSummary[]>([]);
  const [groups, setGroups] = useState<SkillGroupMeta[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [toast, setToast] = useState<{ tone: ToastTone; text: string } | null>(null);
  const [query, setQuery] = useState('');
  // 分组筛选：空值表示全部
  const [group, setGroup] = useState<SkillGroup | ''>('');
  // 来源筛选：内置 / 平台内新建 / zip 导入，空值表示全部
  const [origin, setOrigin] = useState<SkillOrigin | ''>('');
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
      const gr = await listSkillGroups();
      setGroups(gr.groups ?? []);
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
  const flash = useCallback((tone: ToastTone, message: string) => {
    setToast({ tone, text: message });
    flashTimer(() => setToast(null), 4000);
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
        flash('ok', tr(`已导出 ${filename}`, `Exported ${filename}`));
      } catch (e) {
        flash('err', errorText(e));
      }
    },
    [flash, tr],
  );

  const runDelete = useCallback(async () => {
    if (deleting === null) return;
    try {
      await deleteSkill(deleting.key);
      flash('ok', tr(`已删除技能 ${deleting.key}`, `Deleted skill ${deleting.key}`));
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

  // 分组直接用后端给的 group 字段（固定枚举），不再按名称正则猜。
  const skillKey = useCallback((s: SkillSummary) => s.group ?? 'other', []);
  // 筛选胶囊只列出目录里真实存在的分组，避免出现空分组的死胶囊。
  const chips = useMemo(() => {
    const present = new Set(items.map(skillKey));
    return groups.filter((g) => present.has(g.key));
  }, [groups, items, skillKey]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter((s) => {
      if (origin && skillOrigin(s) !== origin) return false;
      if (group && skillKey(s) !== group) return false;
      if (!q) return true;
      return (
        s.name.toLowerCase().includes(q) ||
        s.key.toLowerCase().includes(q) ||
        (s.description ?? '').toLowerCase().includes(q)
      );
    });
  }, [items, query, group, origin, skillKey]);

  useAssistantPage('技能目录', 'Skill catalog', {
    group, visible_skills: filtered.length, selected_skill: viewing?.name, loading,
  });

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tr('技能', 'Skills')}
        subtitle={tr(
          '可装配到助理与工作流的能力包，按分组管理',
          'Capability packs for assistants and workflows, managed by group',
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
              <span className="pl-1 text-[11px] text-zinc-500">{tr('分组', 'Group')}</span>
              <ChoiceChip
                active={group === ''}
                label={tr('全部', 'All')}
                onClick={() => setGroup('')}
              />
              {chips.map((g) => (
                <ChoiceChip
                  key={g.key}
                  active={group === g.key}
                  label={tr(g.title, g.title_en)}
                  onClick={() => setGroup(g.key)}
                />
              ))}
            </>
          )}
          <span className="pl-1 text-[11px] text-zinc-500">{tr('来源', 'Origin')}</span>
          <ChoiceChip
            active={origin === ''}
            label={tr('全部', 'All')}
            onClick={() => setOrigin('')}
          />
          {ORIGIN_ORDER.map((o) => (
            <ChoiceChip
              key={o}
              active={origin === o}
              label={tr(ORIGIN_META[o].zh, ORIGIN_META[o].en)}
              onClick={() => setOrigin(o)}
            />
          ))}
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
          {loading ? (
            <div className="flex h-40 items-center justify-center text-sm text-zinc-500">{tr('加载中…', 'Loading…')}</div>
          ) : err && items.length === 0 ? null : filtered.length === 0 ? (
            <EmptyState
              hasItems={!!(query.trim() || group || origin)}
              onClear={() => {
                setQuery('');
                setGroup('');
                setOrigin('');
              }}
            />
          ) : (
            <TableCard className="min-h-0 flex-1">
              <table className={tableLayout(64)}>
                <colgroup>
                  <col className={COL.id} />
                  <col className={COL.tag} />
                  <col className={COL.tag} />
                  <col className={COL.main} />
                  <col className={COL.actions} />
                </colgroup>
                <TableHead>
                  <tr className="whitespace-nowrap">
                    <th className="px-3 py-2 text-left font-medium">{tr('名称', 'Name')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('类别', 'Class')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('来源', 'Origin')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('描述', 'Description')}</th>
                    <th className="px-3 py-2 text-right font-medium">{tr('操作', 'Actions')}</th>
                  </tr>
                </TableHead>
                {orderedGroupKeys(filtered.map(skillKey)).map((gk) => {
                  const rows = filtered.filter((s) => skillKey(s) === gk);
                  const meta = groups.find((g) => g.key === gk);
                  const isCollapsed = collapsed.has(gk);
                  return (
                    <tbody key={gk} className="divide-y divide-zinc-800/40">
                      <tr
                        className="cursor-pointer select-none border-t border-zinc-800/60 bg-zinc-950/50 hover:bg-zinc-900/50"
                        onClick={() => toggleGroup(gk)}
                      >
                        <td colSpan={5} className="px-3 py-2">
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
                              {meta ? tr(meta.title, meta.title_en) : gk}
                            </span>
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
                'ok',
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
              flash('ok', tr('技能包已导入', 'Skill pack imported'));
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
      {toast !== null && <Toast tone={toast.tone} text={toast.text} />}
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
  // 内置技能随包发布，没有编辑入口
  const editable = canMutate && skillOrigin(skill) !== 'builtin';
  const resourceCount =
    (skill.references?.length ?? 0) + (skill.assets?.length ?? 0) + (skill.scripts?.length ?? 0);
  return (
    <tr className="transition-colors hover:bg-zinc-900/40">
      <td className="whitespace-nowrap px-3 py-2 pl-8">
        <button
          type="button"
          onClick={onView}
          className="text-[12.5px] text-zinc-200 transition-colors hover:text-indigo-300"
        >
          {displayName ?? skill.name}
        </button>
        <Hint content={skill.key}><div className="font-mono text-[10px] text-zinc-600" >
          {skill.key}
        </div></Hint>
      </td>
      <td className="whitespace-nowrap px-3 py-2">
        <ClassBadge value={skill.class} />
      </td>
      <td className="whitespace-nowrap px-3 py-2">
        <OriginBadge value={skillOrigin(skill)} />
      </td>
      {/* Description column is the truncate-absorber — w-full + max-w-0
          forces it to take all remaining horizontal space and clamp
          long copy, so identity columns (name / key / badges) never
          shrink below their natural width. */}
      <td className="w-full max-w-0 px-3 py-2 text-[11px] text-zinc-500">
        <div className="line-clamp-1">{skill.description || '—'}</div>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 font-mono text-[10px] text-zinc-600">
          {skill.group && <span>{skill.group}</span>}
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
          <RowAction icon={Download} onClick={onExport}>
            {tr('导出', 'Export')}
          </RowAction>
          {editable && (
            <>
              <RowAction icon={Pencil} onClick={onEdit}>
                {tr('编辑', 'Edit')}
              </RowAction>
              <RowAction icon={Trash2} tone="danger" onClick={onDelete}>
                {tr('删除', 'Delete')}
              </RowAction>
            </>
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

/** 来源胶囊：内置 / 新建 / ZIP 导入。 */
export function OriginBadge({ value }: { value: SkillOrigin }) {
  const { tr } = useI18n();
  const meta = ORIGIN_META[value];
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
  const origin = skillOrigin(skill);
  const editable = canMutate && origin !== 'builtin';
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
            {origin === 'builtin'
              ? tr('内置技能：随 backend/skills 发布，不可编辑', 'Built-in skill: shipped under backend/skills, not editable')
              : origin === 'imported'
                ? tr('ZIP 导入技能：存于数据库，可编辑', 'Imported skill: stored in the database, editable')
                : tr('新建技能：存于数据库，可编辑', 'Created skill: stored in the database, editable')}
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
          <OriginBadge value={origin} />
          {skill.group && <Chip>{skill.group}</Chip>}
          {skill.inject_state && <Chip tone="accent">adk_inject_state</Chip>}
          <span className="ml-auto font-mono text-[11px] text-zinc-500">{skill.key}</span>
        </div>

        <DetailSection title={tr('描述', 'Description')}>
          <div className="whitespace-pre-wrap rounded-lg border border-zinc-800/60 bg-zinc-900/30 p-3 text-[12px] leading-relaxed text-zinc-300">
            {skill.description || '—'}
          </div>
        </DetailSection>

        {skill.preapproved_tools && (
          <DetailSection title={tr('frontmatter', 'Frontmatter')}>
            <dl className="grid grid-cols-1 gap-2 text-[12px] md:grid-cols-3">
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
