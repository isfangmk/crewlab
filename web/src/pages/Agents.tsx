import { useAssistantPage } from '@/lib/assistantPage';
import { usePermissions } from '@/store/me';
import { Select } from '@/components/ui/Select';
import { Hint } from '@/components/ui/Tooltip';
// Agents page (Phase 1 inventory + Phase 3 user-defined CRUD).
//
// What's here:
//   - List every persona the chatruntime AgentRegistry has loaded —
//     `default` (virtual) + specialist personas (`incident-investigator`,
//     `specialist-sre`, `specialist-ops`, `specialist-network`,
//     `specialist-disk`) + `reviewer` from agents/*.md
//     (Source="disk", read-only), plus user-created ones
//     (Source="user", editable + deletable).
//   - Display order: default → specialists → reviewer → user-defined,
//     so the top of the page reads like a triage rail (start here →
//     specialists → SOP guard → custom).
//   - "新建助理" button → modal with form for name / description /
//     system_prompt / allowed_tools (multi-select from /v1/skills).
//   - Delete button on user-defined cards (confirm modal).
//   - "使用此助理" launches a new chat session pinned to this persona.
//
// The Side Panel will reuse the same /v1/agents data to populate its
// agent switcher dropdown so this stays the single source.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  BookOpen,
  Bot,
  Copy,
  ListChecks,
  MessageSquarePlus,
  Pencil,
  Plus,
  RefreshCw,
  Sparkles,
  Trash2,
  Users,
  Wrench,
  type LucideIcon,
} from 'lucide-react';
import { cn } from '@/lib/cn';
import { mcpToolWireName } from '@/lib/mcpWire';
import { Modal } from '@/components/Modal';
import { Button, Card, Chip, CodeBlock, DetailSection, EmptyStateCard, FilterBar, Input, Label, PageHeader, RowAction, RowActionIcon, SearchInput, Textarea } from '@/components/ui';
import {
  createUserAgent,
  deleteAgent,
  listAgents,
  localizedAgent,
  updateUserAgent,
  type AgentSource,
  type AgentSummary,
  type UserAgentInput,
} from '@/api/agents';
import { listMcpServers, parseToolsCache } from '@/api/mcp';
import { localizedPath } from '@/api/knowledge';
import { listBuiltinTools } from '@/api/tools';
import { listSkills } from '@/api/skills';
import { createSession, listModels } from '@/api/chat';
import { ApiError } from '@/api/client';
import { tr as trInline, useI18n } from '@/i18n/locale';
import { useModelSelection } from '@/store/modelSelection';
import { CapabilityBinding } from '@/components/CapabilityBinding';
import { KnowledgeScopePicker, scopeSelectionFromWire, scopeSelectionIsIncomplete, scopeSelectionToWire, type ScopeSelection } from '@/components/KnowledgeScopePicker';
import { PersonaFields, OUTPUT_FORMAT_LABELS } from '@/components/PersonaFields';
import type { CapabilityCatalog } from '@/lib/capabilityPreview';
import type { ScopePreview } from '@/api/knowledge';
import type { AgentExample, OutputFormat } from '@/api/agents';

/** 解析开会话要用的 provider/model：助理绑定模型优先，否则用全局选择或目录默认。 */
async function resolveSessionModel(agent: AgentSummary): Promise<{ provider: string; model: string }> {
  const cat = await listModels();
  if (!Array.isArray(cat.providers)) {
    throw new Error('模型目录缺少 providers');
  }
  if (!cat.default?.provider || !cat.default?.model) {
    throw new Error('模型目录缺少 default.provider/model');
  }
  if (agent.model !== '') {
    const hit = cat.providers.find((p) => Array.isArray(p.models) && p.models.includes(agent.model));
    if (!hit) {
      throw new Error(`助理绑定的模型不在目录中: ${agent.model}`);
    }
    return { provider: hit.id, model: agent.model };
  }
  const store = useModelSelection.getState().selected;
  if (store !== null && store.provider !== '' && store.model !== '') {
    return { provider: store.provider, model: store.model };
  }
  return { provider: cat.default.provider, model: cat.default.model };
}

export default function AgentsPage() {
  const { tr } = useI18n();
  const { canMutate } = usePermissions();
  const [items, setItems] = useState<AgentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  // editing.seed 预填一份「基于内置助理新建」的草稿（fork），对齐知识库
  // 「复制为组织文档」：内置 / 预置助理只读，想改就 fork 成自定义助理。
  const [editing, setEditing] = useState<
    | { mode: 'create'; seed?: AgentSummary }
    | { mode: 'edit'; agent: AgentSummary }
    | null
  >(null);
  const [deleting, setDeleting] = useState<AgentSummary | null>(null);
  // 详情查看弹窗：点击卡片主体打开（参照知识库文档 / 技能详情）。
  const [viewing, setViewing] = useState<AgentSummary | null>(null);

  const fetchAgents = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true);
    else setLoading(true);
    try {
      const r = await listAgents();
      if (!Array.isArray(r.items)) {
        throw new Error('助理列表缺少 items');
      }
      setItems(r.items.map(localizedAgent));
      setErr(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchAgents();
  }, [fetchAgents]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const matched = !q
      ? items
      : items.filter(
          (a) =>
            a.name.toLowerCase().includes(q) ||
            a.description.toLowerCase().includes(q) ||
            a.when_to_use.toLowerCase().includes(q),
        );
    // Built-ins in the curated order (default → specialists → reviewer),
    // then everything else alphabetically. Done client-side so the page
    // doesn't depend on the registry's iteration order.
    return [...matched].sort((a, b) => {
      const ra = builtinRank(a.name);
      const rb = builtinRank(b.name);
      if (ra !== rb) return ra - rb;
      return a.name.localeCompare(b.name);
    });
  }, [items, query]);

  useAssistantPage('助理列表', 'Agents', {
    agent_count: items.length,
    visible_count: filtered.length,
    visible_agents: filtered.slice(0, 6).map(agent => agent.name).join(', '),
    selected_agent: viewing?.name,
    loading,
  });

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tr('助理', 'Assistants')}
        subtitle={tr(`AI 助理库 · 共 ${items.length} 个，已匹配 ${filtered.length}`, `AI assistant library · ${items.length} total, ${filtered.length} matched`)}
        actions={
          <>
            <RowAction
              icon={RefreshCw}
              iconClassName={cn(refreshing && 'animate-spin')}
              disabled={loading || refreshing}
              onClick={() => fetchAgents(true)}
            >
              {tr('刷新', 'Refresh')}
            </RowAction>
            {canMutate && (
              <Button onClick={() => setEditing({ mode: 'create' })} variant="primary" size="sm">
                <Plus size={12} /> {tr('新建助理', 'New assistant')}
              </Button>
            )}
          </>
        }
      />

      <FilterBar className="mx-6 mt-3">
        <SearchInput
          value={query}
          onChange={setQuery}
          placeholder={tr('搜索 name / description', 'Search name / description')}
          ariaLabel={tr('搜索', 'Search')}
          className="min-w-[16rem] flex-1"
        />
        <span className="ml-auto text-[11px] tabular-nums text-zinc-500">
          {tr(`共 ${items.length} 个 · 匹配 ${filtered.length}`, `${items.length} total · ${filtered.length} matched`)}
        </span>
      </FilterBar>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        {err && (
          <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/5 px-4 py-3 text-sm text-red-300">
            {tr('加载失败：', 'Load failed: ')}{err}
          </div>
        )}
        {loading ? (
          <div className="flex h-40 items-center justify-center text-sm text-zinc-500">{tr('加载中…', 'Loading…')}</div>
        ) : err && items.length === 0 ? null : filtered.length === 0 ? (
          <AgentsEmpty canCreate={canMutate} hasItems={!!query.trim()} onClear={() => setQuery('')} onCreate={() => setEditing({ mode: 'create' })} />
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
            {filtered.map((a) => (
              <AgentCard
                key={a.name}
                agent={a}
                onView={() => setViewing(a)}
                onEdit={() => setEditing({ mode: 'edit', agent: a })}
                onDelete={() => setDeleting(a)}
              />
            ))}
          </div>
        )}
      </div>

      {viewing && (
        <AgentDetailModal
          agent={viewing}
          onClose={() => setViewing(null)}
          onEdit={() => {
            const a = viewing;
            setViewing(null);
            setEditing({ mode: 'edit', agent: a });
          }}
          onFork={() => {
            const a = viewing;
            setViewing(null);
            setEditing({ mode: 'create', seed: a });
          }}
        />
      )}
      {editing && (
        <AgentEditor
          mode={editing.mode}
          existing={editing.mode === 'edit' ? editing.agent : null}
          seed={editing.mode === 'create' ? editing.seed : undefined}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void fetchAgents(true);
          }}
        />
      )}
      {deleting && (
        <DeleteAgentDialog
          agent={deleting}
          onClose={() => setDeleting(null)}
          onDone={() => {
            setDeleting(null);
            void fetchAgents(true);
          }}
        />
      )}
    </main>
  );
}

// SHORT_LABELS — short Chinese display name per built-in persona id.
// Mirrors web/src/components/AgentBadge.tsx so card titles + sidebar
// chips stay aligned. Add new entries when shipping new personas.
const SHORT_LABELS_ZH: Record<string, string> = {
  default: '默认助理',
  'incident-investigator': '故障诊断',
  'specialist-sre': 'SRE 专家',
  'specialist-ops': '运维专家',
  'specialist-compute': '计算专家',
  'specialist-network': '网络专家',
  'specialist-disk': '磁盘专家',
  reviewer: '审核员',
};

const SHORT_LABELS_EN: Record<string, string> = {
  default: 'Default',
  'incident-investigator': 'Incident investigator',
  'specialist-sre': 'SRE specialist',
  'specialist-ops': 'Ops specialist',
  'specialist-compute': 'Compute specialist',
  'specialist-network': 'Network specialist',
  'specialist-disk': 'Disk specialist',
  reviewer: 'Reviewer',
};

// BUILTIN_ORDER drives the display order on /agents. The three
// resource-domain specialists (compute / network / disk) cluster
// together after the SRE / Ops "what's the situation" pair, so a
// scroll-pass reads like a triage rail: triage → ops decision →
// resource drilldown → SOP guard.
const BUILTIN_ORDER: string[] = [
  'default',
  'incident-investigator',
  'specialist-sre',
  'specialist-ops',
  'specialist-compute',
  'specialist-network',
  'specialist-disk',
  'reviewer',
];

function builtinRank(name: string): number {
  const idx = BUILTIN_ORDER.indexOf(name);
  return idx === -1 ? BUILTIN_ORDER.length : idx;
}

const SHORT_LABELS = new Proxy({} as Record<string, string>, {
  get: (_t, key: string) => {
    const zh = SHORT_LABELS_ZH[key];
    if (zh == null) return undefined;
    return trInline(zh, SHORT_LABELS_EN[key] ?? zh);
  },
});

function AgentCard({
  agent,
  onView,
  onEdit,
  onDelete,
}: {
  agent: AgentSummary;
  onView: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const { tr } = useI18n();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const toolCount = agent.tools.length;
  const isUser = agent.source === 'user';
  const canDelete = agent.source !== 'builtin' && agent.name !== 'default';
  // Short Chinese display name. Mirrors AgentBadge's mapping; falls
  // back to the ascii name for unknown personas (e.g. user-created).
  // Description was used as the title before but it's a full sentence
  // and made cards look noisy — we now show a short label and let the
  // description live in the card body as a 2-line teaser.
  const displayName = SHORT_LABELS[agent.name] ?? agent.name;
  // 知识范围：两个列表都空 = 继承全部知识库；否则优先列出目录名（最多两个），
  // 文档集只报篇数——路径比「N 个目录」更能说清这个助理到底管哪一摊。
  const scopePaths = agent.knowledge_scope.paths;
  const scopeDocs = agent.knowledge_scope.doc_ids;
  const scopeTitle =
    scopePaths.length === 0 && scopeDocs.length === 0
      ? tr('全部知识库', 'All knowledge')
      : tr(
          `目录：${scopePaths.join('、') || '（无）'} · 文档 ${scopeDocs.length} 篇`,
          `Folders: ${scopePaths.join(', ') || '(none)'} · ${scopeDocs.length} doc(s)`,
        );
  const scopeLabel =
    scopePaths.length === 0 && scopeDocs.length === 0
      ? tr('全部知识库', 'All knowledge')
      : scopePaths.length > 0
        ? scopePaths.slice(0, 2).join('、') + (scopePaths.length > 2 ? ` +${scopePaths.length - 2}` : '')
        : tr(`${scopeDocs.length} 篇文档`, `${scopeDocs.length} doc(s)`);

  const onUse = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      const title = tr(`使用 ${agent.name} - 新会话`, `Using ${agent.name} - new session`).slice(0, 60);
      const { provider, model } = await resolveSessionModel(agent);
      const session = await createSession({
        title,
        agent_id: agent.name,
        provider,
        model,
      });
      navigate(`/chat/${session.id}`);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
      setBusy(false);
    }
  }, [agent, busy, navigate, tr]);

  return (
    <Card interactive className="group flex cursor-pointer flex-col" onClick={onView}>
      {/* 头部：头像 + 名称/来源 + 行内操作。名称仍是点击进详情的入口。 */}
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500/25 to-sky-500/10 text-indigo-300 ring-1 ring-inset ring-indigo-500/30">
          <Bot size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <Hint content={agent.name}>
              <div className="truncate text-[13px] font-semibold leading-5 text-zinc-100">{displayName}</div>
            </Hint>
            <SourceLabel source={agent.source} />
            {agent.permission_mode === 'read-only' && <Chip dense tone="success">{tr('只读', 'Read-only')}</Chip>}
          </div>
          <div className="truncate font-mono text-[10.5px] leading-4 text-zinc-500">{agent.name}</div>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          {isUser && (
            <Hint content={tr('编辑', 'Edit')}>
              <RowActionIcon
                icon={Pencil}
                label={tr('编辑', 'Edit')}
                onClick={(e) => {
                  e.stopPropagation();
                  onEdit();
                }}
              />
            </Hint>
          )}
          {canDelete && (
            <Hint content={isUser ? tr('删除', 'Delete') : tr('从助理列表中移除（重启后内置 persona 会自动加载回来）', 'Remove from the list (built-in personas reload automatically on restart)')}>
              <RowActionIcon
                icon={Trash2}
                label={isUser ? tr('删除', 'Delete') : tr('从助理列表中移除', 'Remove from the list')}
                tone="danger"
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete();
                }}
              />
            </Hint>
          )}
        </div>
      </div>

      {/* 描述固定两行高度，同一行的卡片底部元信息才能对齐。 */}
      <p className="mt-2.5 line-clamp-2 min-h-[2.4em] text-[11.5px] leading-relaxed text-zinc-400">
        {agent.description}
      </p>

      {agent.when_to_use && (
        <div className="mt-1.5 flex items-baseline gap-1.5">
          <span className="shrink-0 text-[10px] uppercase tracking-wide text-zinc-400">
            {tr('何时使用', 'When')}
          </span>
          <span className="line-clamp-1 text-[11px] leading-snug text-zinc-500" title={agent.when_to_use}>
            {agent.when_to_use}
          </span>
        </div>
      )}

      {/* 关键提醒是运行时真正注入的约束，卡片上直接露出来，不用点进详情才知道。 */}
      {agent.critical_reminder && (
        <div className="mt-2.5 rounded-md border-l-2 border-amber-500/50 bg-amber-500/[0.06] py-1.5 pl-2 pr-2">
          <div className="text-[9.5px] font-medium uppercase tracking-wide text-amber-300/80">
            {tr('关键提醒', 'Reminder')}
          </div>
          <p className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-zinc-400" title={agent.critical_reminder}>
            {agent.critical_reminder}
          </p>
        </div>
      )}

      {/* 四格元信息：工具 / 知识范围 / 输出格式 / 示例，回答「这个助理到底怎么配的」。 */}
      <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 border-t border-zinc-800/60 pt-2.5">
        <Fact
          icon={Wrench}
          label={tr('工具', 'Tools')}
          value={toolCount > 0 ? tr(`${toolCount} 个`, `${toolCount}`) : tr('继承全部', 'Inherits all')}
        />
        <Fact icon={BookOpen} label={tr('知识范围', 'Scope')} value={scopeLabel} title={scopeTitle} />
        <Fact
          icon={ListChecks}
          label={tr('输出格式', 'Format')}
          value={tr(OUTPUT_FORMAT_LABELS[agent.output_format].zh, OUTPUT_FORMAT_LABELS[agent.output_format].en)}
        />
        <Fact
          icon={Sparkles}
          label={tr('示例', 'Examples')}
          value={agent.examples.length > 0 ? tr(`${agent.examples.length} 条`, `${agent.examples.length}`) : tr('无', 'None')}
        />
      </div>

      <div className="mt-2.5 flex items-center justify-between gap-2">
        <span className="min-w-0 truncate text-[10.5px] text-zinc-500" title={agent.model || undefined}>
          {agent.model !== '' ? agent.model : tr('模型继承默认', 'Model inherited')}
        </span>
        <Hint content={tr('用此助理开新会话', 'Start a new session with this assistant')}>
          <Button variant="plain" size="sm"
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              void onUse();
            }}
            disabled={busy}
            className="gap-1 border border-indigo-500/40 bg-indigo-500/10 px-2 text-[11px] text-indigo-200 hover:bg-indigo-500/20"
          >
            <MessageSquarePlus size={12} />
            {busy ? tr('创建中…', 'Creating…') : tr('使用此助理', 'Use this')}
          </Button>
        </Hint>
      </div>
      {err && <div className="mt-2 text-[11px] text-red-300">{err}</div>}
    </Card>
  );
}

/** 卡片元信息一格：图标 + 字段名 + 值（值过长单行省略，悬停看全文）。 */
function Fact({ icon: Icon, label, value, title }: { icon: LucideIcon; label: string; value: string; title?: string }) {
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <Icon size={11} className="shrink-0 text-zinc-500" />
      <span className="shrink-0 text-[10px] text-zinc-400">{label}</span>
      <span className="truncate text-[10.5px] font-medium text-zinc-300" title={title ?? value}>
        {value}
      </span>
    </div>
  );
}

function SourceLabel({ source }: { source: AgentSource }) {
  const { tr } = useI18n();
  if (source === 'user') return <Chip dense tone="accent">{tr('自定义', 'Custom')}</Chip>;
  if (source === 'builtin') return <Chip dense tone="success">{tr('系统内置', 'Built-in')}</Chip>;
  if (source === 'disk') return <Chip dense>{tr('预置', 'Preset')}</Chip>;
  throw new Error(`未知助理来源: ${String(source)}`);
}


// AgentDetailModal — 只读详情查看，参照知识库文档的阅读弹窗：完整展示
// description / when_to_use / system prompt（原文）/ 工具清单 / 模型等。
// 编辑路径按 source 区分（对齐知识库 vault 文档「只读 + 复制为组织文档」）：
//   - user 自定义助理 → 「编辑」直接改 user_agents 行
//   - builtin / disk 内置 / 预置 → 定义在代码 / agents/*.md，不可在线改，
//     提供「复制为自定义助理」fork 出一份可编辑的草稿
function AgentDetailModal({
  agent,
  onClose,
  onEdit,
  onFork,
}: {
  agent: AgentSummary;
  onClose: () => void;
  onEdit: () => void;
  onFork: () => void;
}) {
  const { tr } = useI18n();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const displayName = SHORT_LABELS[agent.name] ?? agent.name;
  const isUser = agent.source === 'user';
  const toolCount = agent.tools.length;

  const onUse = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      const title = tr(`使用 ${agent.name} - 新会话`, `Using ${agent.name} - new session`).slice(0, 60);
      const { provider, model } = await resolveSessionModel(agent);
      const session = await createSession({
        title,
        agent_id: agent.name,
        provider,
        model,
      });
      navigate(`/chat/${session.id}`);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
      setBusy(false);
    }
  }, [agent, busy, navigate, tr]);

  return (
    <Modal
      open
      onClose={onClose}
      size="lg"
      resizable
      title={displayName}
      footer={
        <>
          <Button variant="outline" size="sm"
            type="button"
            onClick={onClose}
          >
            {tr('关闭', 'Close')}
          </Button>
          {isUser ? (
            <Button variant="outline" size="sm"
              type="button"
              onClick={onEdit}
            >
              <Pencil size={11} /> {tr('编辑', 'Edit')}
            </Button>
          ) : (
            <Hint content={tr('内置 / 预置助理定义在代码 / agents/*.md，不可直接改；复制一份为自定义助理后即可编辑', 'Built-in / preset assistants are defined in code / agents/*.md and cannot be edited directly; copy into a custom assistant to edit')}><Button variant="outline" size="sm"
              type="button"
              onClick={onFork}
            >
              <Copy size={11} /> {tr('复制为自定义助理', 'Copy as custom')}
            </Button></Hint>
          )}
          <Button variant="primary" size="sm"
            type="button"
            onClick={() => void onUse()}
            disabled={busy}
          >
            <MessageSquarePlus size={11} /> {busy ? tr('创建中…', 'Creating…') : tr('使用此助理', 'Use this')}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[11px] text-zinc-400">{agent.name}</span>
          <SourceLabel source={agent.source} />
          {agent.permission_mode === 'read-only' && <Chip dense tone="success">{tr('只读', 'Read-only')}</Chip>}
          <span className="ml-auto text-[11px] text-zinc-500">
            {toolCount > 0 ? tr(`${toolCount} 个工具`, `${toolCount} tool(s)`) : tr('继承全部工具', 'Inherits all tools')}
          </span>
        </div>

        {err && <div className="text-[11px] text-red-300">{err}</div>}

        <DetailSection title={tr('描述', 'Description')}>
          <p className="text-[12px] leading-relaxed text-zinc-300">{agent.description || '—'}</p>
        </DetailSection>

        {agent.when_to_use && (
          <DetailSection title={tr('何时使用', 'When to use')}>
            <p className="whitespace-pre-wrap text-[12px] leading-relaxed text-zinc-300">{agent.when_to_use}</p>
          </DetailSection>
        )}

        <DetailSection title={tr('系统提示（原文）', 'System prompt (raw)')}>
          {agent.system_prompt ? (
            <CodeBlock maxHeightClassName="max-h-80">{agent.system_prompt}</CodeBlock>
          ) : (
            <p className="text-[12px] text-zinc-500">{tr('继承 coordinator 默认提示', 'Inherits the coordinator default prompt')}</p>
          )}
        </DetailSection>

        {agent.critical_reminder && (
          <DetailSection title={tr('关键提醒', 'Critical reminder')}>
            {/* 用不带透明度的 text-amber-300：light 主题有 amber-300→amber-700
                的覆盖（index.css），透明度变体 /80 不在覆盖内、浅色下会发灰看不清。
                配一层 amber 描边底色，强化「提醒」语义又保证两主题都够对比度。 */}
            <p className="whitespace-pre-wrap rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-[12px] leading-relaxed text-amber-300">
              {agent.critical_reminder}
            </p>
          </DetailSection>
        )}

        <DetailSection title={tr('知识范围', 'Knowledge scope')}>
          {agent.knowledge_scope.paths.length === 0 && agent.knowledge_scope.doc_ids.length === 0 ? (
            <p className="text-[12px] text-zinc-500">{tr('全部知识库', 'Whole knowledge base')}</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {agent.knowledge_scope.paths.map((p) => (
                <span key={p} className="rounded border border-zinc-800/60 bg-zinc-900/30 px-1.5 py-0.5 text-[10px] text-zinc-400">
                  {localizedPath(p) || tr('（根目录）', '(root)')}
                </span>
              ))}
              {agent.knowledge_scope.doc_ids.length > 0 && (
                <span className="rounded border border-zinc-800/60 bg-zinc-900/30 px-1.5 py-0.5 text-[10px] text-zinc-400">
                  {tr(`${agent.knowledge_scope.doc_ids.length} 篇指定文档`, `${agent.knowledge_scope.doc_ids.length} pinned doc(s)`)}
                </span>
              )}
            </div>
          )}
        </DetailSection>

        <DetailSection title={tr('输出格式与示例', 'Output format & examples')}>
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-[11px] text-zinc-500">
            <span>
              {tr('输出格式', 'Output format')}:{' '}
              <span className="text-zinc-300">
                {tr(
                  OUTPUT_FORMAT_LABELS[agent.output_format].zh,
                  OUTPUT_FORMAT_LABELS[agent.output_format].en,
                )}
              </span>
            </span>
            <span>
              {tr('示例', 'Examples')}: <span className="text-zinc-300">{agent.examples.length}</span>
            </span>
          </div>
        </DetailSection>

        <DetailSection title={tr('允许使用的工具', 'Allowed tools')}>
          {toolCount === 0 ? (
            <p className="text-[12px] text-zinc-500">{tr('继承 coordinator 的全部工具', 'Inherits all coordinator tools')}</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {agent.tools.map((t) => (
                <span key={t} className="rounded border border-zinc-800/60 bg-zinc-900/30 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">
                  {t}
                </span>
              ))}
            </div>
          )}
        </DetailSection>

        {(agent.model || (agent.max_turns ?? 0) > 0) && (
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-[11px] text-zinc-500">
            {agent.model && (
              <span>{tr('模型', 'Model')}: <span className="font-mono text-zinc-300">{agent.model}</span></span>
            )}
            {(agent.max_turns ?? 0) > 0 && (
              <span>{tr('最大轮数', 'Max turns')}: <span className="text-zinc-300">{agent.max_turns}</span></span>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}

function AgentsEmpty({ hasItems, onCreate, onClear, canCreate }: { canCreate: boolean; hasItems: boolean; onCreate: () => void; onClear: () => void }) {
  const { tr } = useI18n();
  return (
    <EmptyStateCard
      icon={Users}
      title={hasItems ? tr('没有匹配的助理', 'No matching assistants') : tr('还没有助理注册', 'No assistants registered yet')}
      hint={!hasItems && !canCreate ? tr('请联系管理员创建助理。', 'Ask an admin to create an assistant.') : undefined}
      action={
        hasItems ? <Button onClick={onClear}>{tr('清除筛选', 'Clear filters')}</Button> : canCreate && (
          <Button variant="primary" onClick={onCreate}>
            <Plus size={12} /> {tr('新建助理', 'New assistant')}
          </Button>
        )
      }
    />
  );
}

// AgentEditor is the create + edit form modal. Reused by three flows:
//   - create blank: existing=null, seed=undefined
//   - create from fork: existing=null, seed=<内置/预置助理> — 预填正文，
//     name 留空让用户取新名（对齐知识库「复制为组织文档」）
//   - edit: existing=<user agent> — Name 字段只读（name 是不可变标识）
function AgentEditor({
  mode,
  existing,
  seed,
  onClose,
  onSaved,
}: {
  mode: 'create' | 'edit';
  existing: AgentSummary | null;
  seed?: AgentSummary;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { tr } = useI18n();
  // base 是预填来源：edit 用 existing，fork 新建用 seed。name 只在 edit
  // 时承袭；fork 留空（不能与被 fork 的内置助理重名）。
  const base = existing ?? seed ?? null;
  const [name, setName] = useState(existing?.name ?? '');
  const [description, setDescription] = useState(base?.description ?? '');
  const [whenToUse, setWhenToUse] = useState(base?.when_to_use ?? '');
  const [systemPrompt, setSystemPrompt] = useState(base?.system_prompt ?? '');
  const [criticalReminder, setCriticalReminder] = useState(base?.critical_reminder ?? '');
  const [outputFormat, setOutputFormat] = useState<OutputFormat>(base?.output_format ?? 'free');
  const [examples, setExamples] = useState<AgentExample[]>(
    (base?.examples ?? []).map((item) => ({ ...item })),
  );
  const [scope, setScope] = useState<ScopeSelection>(
    scopeSelectionFromWire(base?.knowledge_scope ?? { paths: [], doc_ids: [] }),
  );
  const [scopePreviewState, setScopePreviewState] = useState<ScopePreview | null>(null);
  const [allowedTools, setAllowedTools] = useState<string[]>(base?.tools ?? []);
  const [disallowedTools, setDisallowedTools] = useState<string[]>(base?.disallowed_tools ?? []);
  const [selectedSkills, setSelectedSkills] = useState<string[] | null>(
    base?.skills === undefined ? null : base.skills,
  );
  const [permissionMode, setPermissionMode] = useState(base?.permission_mode ?? '');
  const [model, setModel] = useState(base?.model ?? '');
  const [maxTurns, setMaxTurns] = useState<number>(base?.max_turns ?? 0);
  const [catalog, setCatalog] = useState<CapabilityCatalog | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([listMcpServers(), listBuiltinTools(), listSkills()])
      .then(([servers, builtins, skills]) => {
        if (cancelled) return;
        setCatalog({
          builtin: builtins.items.map((tool) => ({
            name: tool.name,
            description: tool.description,
            mount: tool.mount,
          })),
          skills: skills.items.map((skill) => ({
            key: skill.key,
            description: skill.description,
            tools: skill.tools ?? [],
          })),
          mcpServers: servers.map((server) => ({
            name: server.name,
            enabled: server.enabled,
            status: server.status,
            // 未探测过的 Server 没有工具缓存（空串），按 0 个工具展示
            tools: server.tools_cache_json === ''
              ? []
              : parseToolsCache(server.tools_cache_json).map((tool) => ({
                  wire: mcpToolWireName(server.name, tool.name),
                  description: tool.description,
                })),
          })),
        });
        setCatalogError(null);
      })
      .catch((e: Error) => {
        if (!cancelled) setCatalogError(e instanceof ApiError ? e.message : e.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const scopeBlocked =
    scope.mode === 'custom' &&
    (scopeSelectionIsIncomplete(scope) ||
      scopePreviewState === null ||
      scopePreviewState.doc_count === 0 ||
      scopePreviewState.unknown_paths.length > 0 ||
      scopePreviewState.unknown_doc_ids.length > 0);

  const submit = async () => {
    setErr(null);
    setSubmitting(true);
    try {
      const input: UserAgentInput = {
        name: mode === 'create' ? name.trim() : undefined,
        description: description.trim(),
        when_to_use: whenToUse.trim(),
        system_prompt: systemPrompt.trim(),
        critical_reminder: criticalReminder.trim(),
        allowed_tools: allowedTools,
        disallowed_tools: disallowedTools,
        permission_mode: permissionMode,
        model: model.trim(),
        max_turns: maxTurns,
        output_format: outputFormat,
        examples: examples.map((item) => ({ input: item.input, output: item.output })),
        knowledge_scope: scopeSelectionToWire(scope),
        skills: selectedSkills,
      };
      if (mode === 'create') {
        await createUserAgent(input);
      } else {
        if (existing === null) {
          throw new Error('编辑模式缺少 existing 助理');
        }
        await updateUserAgent(existing.name, input);
      }
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      size="xl"
      resizable
      title={
        mode === 'edit'
          ? tr(`编辑 ${existing?.name}`, `Edit ${existing?.name}`)
          : seed
            ? tr(`基于 ${seed.name} 新建助理`, `New assistant from ${seed.name}`)
            : tr('新建助理', 'New assistant')
      }
      footer={
        <>
          <Button variant="outline" size="sm"
            type="button"
            onClick={onClose}
            className="px-3 py-1.5"
          >
            {tr('取消', 'Cancel')}
          </Button>
          <Button variant="primary" size="sm"
            type="button"
            onClick={() => void submit()}
            disabled={
              submitting ||
              (mode === 'create' && name.trim() === '') ||
              description.trim() === '' ||
              systemPrompt.trim() === '' ||
              scopeBlocked
            }
            className="px-3 py-1.5 font-medium text-accent-fg"
          >
            {submitting ? tr('保存中…', 'Saving…') : tr('保存', 'Save')}
          </Button>
        </>
      }
    >
      <div className="space-y-4 text-xs text-zinc-300">
        {err && (
          <div className="rounded-md border border-red-500/40 bg-red-500/5 px-3 py-2 text-red-300">
            {err}
          </div>
        )}

        {/* 宽弹窗下基础信息并排，避免长表单变成一条竖直的窄列。 */}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Field label={tr('名称', 'Name')} required>
            <Input
              type="text"
              value={name}
              disabled={mode === 'edit'}
              onChange={(e) => setName(e.target.value)}
              placeholder={tr('lower_snake 或 kebab-case，例如 my_db_assistant', 'lower_snake or kebab-case, e.g. my_db_assistant')}
              className="w-full font-mono"
              maxLength={64}
            />
            <div className="mt-1 text-[11px] text-zinc-500">
              {tr('创建后不能改名。', 'Cannot rename after creation.')}
            </div>
          </Field>

          <Field label={tr('描述', 'Description')} required>
            <Input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={tr('一句话说清这个助理擅长什么', 'One sentence on what this assistant is good at')}
              className="w-full"
              maxLength={512}
            />
            <div className="mt-1 text-[11px] text-zinc-500">
              {tr('显示在助理卡片与选择器里。', 'Shown on the assistant card and picker.')}
            </div>
          </Field>
        </div>

        <Field label={tr('何时使用', 'When to use')}>
          <Textarea
            value={whenToUse}
            onChange={(e) => setWhenToUse(e.target.value)}
            placeholder={tr('给 coordinator 的判断线索：什么场景把任务派给这个助理', 'Hint for the coordinator: when to delegate to this assistant')}
            className="h-20 w-full resize-y"
          />
        </Field>

        <FieldGroup label={tr('人设与示例', 'Persona & examples')} required>
          <PersonaFields
            value={{ systemPrompt, criticalReminder, outputFormat, examples }}
            onChange={(patch) => {
              if (patch.systemPrompt !== undefined) setSystemPrompt(patch.systemPrompt);
              if (patch.criticalReminder !== undefined) setCriticalReminder(patch.criticalReminder);
              if (patch.outputFormat !== undefined) setOutputFormat(patch.outputFormat);
              if (patch.examples !== undefined) setExamples(patch.examples);
            }}
          />
        </FieldGroup>

        <FieldGroup label={tr('知识范围', 'Knowledge scope')}>
          <KnowledgeScopePicker
            value={scope}
            onChange={setScope}
            onPreviewChange={setScopePreviewState}
          />
        </FieldGroup>

        <Field label={tr('能力绑定', 'Capability binding')}>
          <CapabilityBinding
            catalog={catalog}
            catalogError={catalogError}
            allowedTools={allowedTools}
            disallowedTools={disallowedTools}
            onAllowedToolsChange={setAllowedTools}
            onDisallowedToolsChange={setDisallowedTools}
            selectedSkills={selectedSkills}
            onSelectedSkillsChange={setSelectedSkills}
          />
        </Field>

        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <Field label={tr('权限模式', 'Permission mode')}>
            <Select
              label={tr('权限模式', 'Permission mode')}
              value={permissionMode}
              onValueChange={(v) => setPermissionMode(v)}
              className="w-full"
            >
              <option value="">{tr('（不限）', '(unrestricted)')}</option>
              <option value="read-only">{tr('只读 — 所有 MCP 工具都需人工确认', 'Read-only — every MCP tool needs confirmation')}</option>
            </Select>
          </Field>
          <Field label={tr('模型', 'Model')}>
            <Input
              type="text"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder={tr('留空 = 继承', 'Empty = inherit')}
              className="w-full"
            />
          </Field>
          <Field label={tr('最大轮数', 'Max turns')}>
            <Input
              type="number"
              value={maxTurns || ''}
              onChange={(e) => setMaxTurns(parseInt(e.target.value, 10) || 0)}
              placeholder={tr('留空 = 继承', 'Empty = inherit')}
              min={0}
              max={100}
              className="w-full"
            />
          </Field>
        </div>
      </div>
    </Modal>
  );
}

// 与后端 mcp_tool_wire_name 对齐：server 段保留原始名，tool 段规范化。
// 规则集中在 lib/mcpWire，技能编辑与能力绑定复用同一份实现。

function Field({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <Label className="block">
      <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-zinc-400">
        {label}
        {required && <span className="ml-0.5 text-red-400">*</span>}
      </div>
      {children}
    </Label>
  );
}

// 复合字段（多个控件）不能用 <label> 包裹：<label> 会把整段文字算成内部第一个
// 控件的可访问名，按钮就查不到了。这里只给分组标题。
function FieldGroup({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-2 text-[11px] font-medium uppercase tracking-wide text-zinc-400">
        {label}
        {required && <span className="ml-0.5 text-red-400">*</span>}
      </div>
      {children}
    </div>
  );
}

function DeleteAgentDialog({
  agent,
  onClose,
  onDone,
}: {
  agent: AgentSummary;
  onClose: () => void;
  onDone: () => void;
}) {
  const { tr } = useI18n();
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setSubmitting(true);
    setErr(null);
    try {
      await deleteAgent(agent.name);
      onDone();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={tr(`删除助理 ${agent.name}`, `Delete assistant ${agent.name}`)}
      footer={
        <>
          <Button variant="outline" size="sm"
            type="button"
            onClick={onClose}
            className="px-3 py-1.5"
          >
            {tr('取消', 'Cancel')}
          </Button>
          <Button variant="danger" size="sm"
            type="button"
            onClick={() => void submit()}
            disabled={submitting}
            className="px-3 py-1.5 font-medium"
          >
            {submitting ? tr('删除中…', 'Deleting…') : tr('删除', 'Delete')}
          </Button>
        </>
      }
    >
      <div className="text-xs text-zinc-300">
        {err && (
          <div className="mb-3 rounded-md border border-red-500/40 bg-red-500/5 px-3 py-2 text-red-300">
            {err}
          </div>
        )}
        <p>
          {tr('确定删除自定义助理 ', 'Delete custom assistant ')}<span className="font-mono text-zinc-100">{agent.name}</span>?
        </p>
        <p className="mt-2 text-zinc-500">
          {tr(
            '已经用此助理建过的会话不会被删除——它们会回退到默认 coordinator 继续运行。',
            "Sessions already created with this assistant are kept — they fall back to the default coordinator.",
          )}
        </p>
      </div>
    </Modal>
  );
}
