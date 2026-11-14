import { useAssistantPage } from '@/lib/assistantPage';
import { usePermissions } from '@/store/me';
import { Select } from '@/components/ui/Select';
import { useRequestSeq } from '@/lib/requestSeq';
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
  AlertTriangle,
  BookOpen,
  Bot,
  Check,
  ChevronRight,
  Copy,
  ListChecks,
  MessageSquarePlus,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
  Users,
} from 'lucide-react';
import { cn } from '@/lib/cn';
import { mcpToolWireName } from '@/lib/mcpWire';
import { Modal } from '@/components/Modal';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { Button, Card, Checkbox, Chip, CodeBlock, DetailSection, EmptyStateCard,
  ErrorBanner, FilterBar, Input, Label, PageHeader, RowAction, RowActionIcon, SearchInput, Textarea,
  Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui';
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
import { PersonaFields, OUTPUT_FORMAT_LABELS, examplesInvalid } from '@/components/PersonaFields';
import type { CapabilityCatalog } from '@/lib/capabilityPreview';
import type { ScopePreview } from '@/api/knowledge';
import type { AgentExample, OutputFormat } from '@/api/agents';
import { errorText } from '@/lib/errors';

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
  // 助理列表会随筛选与增删刷新：路数守卫保证只有最新一次请求的结果落进状态
  const reqSeq = useRequestSeq();
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
    const current = reqSeq.next();
    if (silent) setRefreshing(true);
    else setLoading(true);
    try {
      const r = await listAgents();
      if (reqSeq.isStale(current)) return;
      if (!Array.isArray(r.items)) {
        throw new Error('助理列表缺少 items');
      }
      setItems(r.items.map(localizedAgent));
      setErr(null);
    } catch (e) {
      if (reqSeq.isStale(current)) return;
      setErr(errorText(e));
    } finally {
      if (!reqSeq.isStale(current)) {
        setLoading(false);
              setRefreshing(false);
      }
    }
  }, [reqSeq]);

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
            (a.display_name ?? '').toLowerCase().includes(q) ||
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
        subtitle={tr(
          '预设的对话角色：绑定工具、技能与知识范围，可在对话中按需切换',
          'Preset personas: bound tools, skills and knowledge scope; switchable per conversation',
        )}
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
          placeholder={tr('搜索显示名称 / Agent 标识 / 描述', 'Search display name / Agent ID / description')}
          ariaLabel={tr('搜索', 'Search')}
        />
        <span className="ml-auto text-[11px] tabular-nums text-zinc-500">
          {tr(`共 ${filtered.length} 条`, `${filtered.length} total`)}
        </span>
      </FilterBar>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        {err && (
          <ErrorBanner>
            {tr('加载失败：', 'Load failed: ')}{err}
          </ErrorBanner>
        )}
        {loading ? (
          <div className="flex h-40 items-center justify-center text-[11px] text-zinc-500">{tr('加载中…', 'Loading…')}</div>
        ) : err && items.length === 0 ? null : filtered.length === 0 ? (
          <AgentsEmpty canCreate={canMutate} hasItems={!!query.trim()} onClear={() => setQuery('')} onCreate={() => setEditing({ mode: 'create' })} />
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
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
  // 显示名称优先取后端 display_name；旧数据或预置助理退回内置短名 / Agent 标识。
  const displayName = agent.display_name || SHORT_LABELS[agent.name] || agent.name;
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
  const formatLabel = tr(OUTPUT_FORMAT_LABELS[agent.output_format].zh, OUTPUT_FORMAT_LABELS[agent.output_format].en);
  const modelLabel = agent.model !== '' ? agent.model : tr('默认模型', 'Default model');
  const skillLabel = agent.skills == null ? tr('全部', 'All') : String(agent.skills.length);
  const delegationCount = agent.sub_agents?.length ?? 0;
  const delegationLabel = delegationCount > 0 ? String(delegationCount) : tr('无', 'None');
  const exampleCount = agent.examples?.length ?? 0;

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
      setErr(errorText(e));
      setBusy(false);
    }
  }, [agent, busy, navigate, tr]);

  return (
    <Card className="group flex flex-col transition-colors hover:border-zinc-700/70 hover:bg-zinc-900/50">
      {/* 头部：头像 + 名称/来源 + 行内操作。只有名称是进详情的入口。 */}
      <div className="flex items-start gap-3">
        <span className="mt-0.5 grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-indigo-500/25 via-indigo-500/10 to-transparent text-indigo-300 ring-1 ring-inset ring-indigo-500/20">
          <Bot size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-1.5">
            <Hint content={tr(`${displayName}（Agent 标识：${agent.name}）`, `${displayName} (Agent ID: ${agent.name})`)}>
              <button
                type="button"
                onClick={onView}
                className="min-w-0 truncate text-[13px] font-semibold leading-5 text-zinc-100 transition-colors hover:text-indigo-300"
              >
                {displayName}
              </button>
            </Hint>
            <SourceLabel source={agent.source} />
            {agent.permission_mode === 'read-only' && <Chip dense tone="success">{tr('只读', 'Read-only')}</Chip>}
          </div>
          <div className="mt-0.5 flex min-w-0 items-center gap-1.5 text-[10px] text-zinc-500">
            <span className="truncate" title={agent.name}>{agent.name}</span>
            <span className="shrink-0 text-zinc-700">·</span>
            <span className={cn('truncate', agent.model === '' && 'text-zinc-600')} title={agent.model || undefined}>
              {modelLabel}
            </span>
          </div>
        </div>
        <div className="-mr-1 -mt-0.5 flex shrink-0 items-center gap-0.5">
          {isUser && (
            <Hint content={tr('编辑', 'Edit')}>
              <RowActionIcon
                icon={Pencil}
                label={tr('编辑', 'Edit')}
                onClick={onEdit}
              />
            </Hint>
          )}
          {canDelete && (
            <Hint content={isUser ? tr('删除', 'Delete') : tr('从助理列表中移除（重启后内置 persona 会自动加载回来）', 'Remove from the list (built-in personas reload automatically on restart)')}>
              <RowActionIcon
                icon={Trash2}
                label={isUser ? tr('删除', 'Delete') : tr('从助理列表中移除', 'Remove from the list')}
                tone="danger"
                onClick={onDelete}
              />
            </Hint>
          )}
        </div>
      </div>

      {/* 描述固定两行高度，同一行的卡片底部元信息才能对齐。 */}
      <p className="mt-3 line-clamp-2 min-h-[2.6em] text-[12px] leading-[1.65] text-zinc-300">
        {agent.description}
      </p>

      {/* 关键提醒是运行时真正注入的约束，卡片上直接露出来，不用点进详情才知道。 */}
      {agent.critical_reminder && (
        <div className="mt-2.5 flex items-start gap-1.5 rounded-lg border border-warn/20 bg-warn/[0.05] px-2.5 py-1.5">
          <AlertTriangle size={11} className="mt-0.5 shrink-0 text-warn" />
          <p className="line-clamp-2 text-[10.5px] leading-4 text-zinc-400" title={agent.critical_reminder}>
            {agent.critical_reminder}
          </p>
        </div>
      )}

      {/* 三格配置摘要：工具 / 技能 / 委托，对应「这个助理能做什么」。 */}
      <div className="mt-3 grid grid-cols-3 divide-x divide-zinc-800/60 rounded-lg border border-zinc-800/60 bg-zinc-950/40 px-3 py-2">
        <Stat label={tr('工具', 'Tools')} value={toolCount > 0 ? String(toolCount) : tr('全部', 'All')} />
        <Stat label={tr('技能', 'Skills')} value={skillLabel} />
        <Stat label={tr('委托', 'Delegation')} value={delegationLabel} />
      </div>

      <div className="mt-2 flex min-w-0 items-center gap-1.5 text-[10px]">
        <BookOpen size={11} className="shrink-0 text-zinc-500" />
        <span className="shrink-0 text-zinc-500">{tr('知识范围', 'Scope')}</span>
        <span className="min-w-0 truncate text-zinc-300" title={scopeTitle}>{scopeLabel}</span>
      </div>

      <div className="mt-auto flex items-center justify-between gap-2 border-t border-zinc-800/60 pt-2.5">
        <span className="flex min-w-0 items-center gap-1.5 text-[9.5px] text-zinc-500">
          <ListChecks size={11} className="shrink-0" />
          <span className="truncate">{formatLabel}</span>
          {exampleCount > 0 && (
            <>
              <span className="shrink-0 text-zinc-700">·</span>
              <span className="shrink-0">{tr(`${exampleCount} 条示例`, `${exampleCount} example(s)`)}</span>
            </>
          )}
        </span>
        <Hint content={tr('用此助理开新会话', 'Start a new session with this assistant')}>
          <Button variant="plain" size="sm"
            type="button"
            onClick={() => {
              void onUse();
            }}
            disabled={busy}
            className="gap-1 border border-indigo-500/40 bg-indigo-500/10 px-2 text-[11px] font-medium text-indigo-300 hover:border-indigo-500/60 hover:bg-indigo-500/20 hover:text-indigo-200"
          >
            <MessageSquarePlus size={12} />
            {busy ? tr('创建中…', 'Creating…') : tr('使用此助理', 'Use this')}
          </Button>
        </Hint>
      </div>
      {err && <ErrorBanner className="mt-2">{err}</ErrorBanner>}
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 px-2 first:pl-0 last:pr-0">
      <div className="truncate text-[13px] font-semibold leading-none tabular-nums text-zinc-100" title={value}>
        {value}
      </div>
      <div className="mt-1 truncate text-[9px] text-zinc-500">{label}</div>
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
  const displayName = agent.display_name || SHORT_LABELS[agent.name] || agent.name;
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
      setErr(errorText(e));
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
          <span className="font-sans text-[11px] text-zinc-400">{agent.name}</span>
          <SourceLabel source={agent.source} />
          {agent.permission_mode === 'read-only' && <Chip dense tone="success">{tr('只读', 'Read-only')}</Chip>}
          <span className="ml-auto text-[11px] text-zinc-500">
            {toolCount > 0 ? tr(`${toolCount} 个工具`, `${toolCount} tool(s)`) : tr('继承全部工具', 'Inherits all tools')}
          </span>
        </div>

        {err && <ErrorBanner>{err}</ErrorBanner>}

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
            {/* 提醒条走 warn 语义色，深浅主题自动切换 */}
            <p className="whitespace-pre-wrap rounded-lg border border-warn/30 bg-warn/10 p-3 text-[12px] leading-relaxed text-warn">
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
                <Chip key={p} dense>
                  {localizedPath(p) || tr('（根目录）', '(root)')}
                </Chip>
              ))}
              {agent.knowledge_scope.doc_ids.length > 0 && (
                <Chip dense>
                  {tr(`${agent.knowledge_scope.doc_ids.length} 篇指定文档`, `${agent.knowledge_scope.doc_ids.length} pinned doc(s)`)}
                </Chip>
              )}
            </div>
          )}
        </DetailSection>

        {agent.sub_agents.length > 0 && (
          <DetailSection title={tr('子助理委托', 'Sub-assistant delegation')}>
            <div className="flex flex-wrap gap-1.5">
              {agent.sub_agents.map((name) => (
                <Chip key={name} dense className="font-sans">
                  {name}
                </Chip>
              ))}
            </div>
          </DetailSection>
        )}

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
                <Chip key={t} dense className="font-sans">
                  {t}
                </Chip>
              ))}
            </div>
          )}
        </DetailSection>

        {(agent.model || (agent.max_turns ?? 0) > 0) && (
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-[11px] text-zinc-500">
            {agent.model && (
              <span>{tr('模型', 'Model')}: <span className="font-sans text-zinc-300">{agent.model}</span></span>
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
        hasItems ? <Button size="sm" onClick={onClear}>{tr('清除筛选', 'Clear filters')}</Button> : canCreate && (
          <Button variant="primary" size="sm" onClick={onCreate}>
            <Plus size={12} /> {tr('新建助理', 'New assistant')}
          </Button>
        )
      }
    />
  );
}

// 新建走向导、编辑走 Tabs；两边共用同一组配置分区与同一份草稿状态。
type EditorSection = 'basic' | 'persona' | 'knowledge' | 'capabilities' | 'runtime';

const EDITOR_SECTIONS: {
  key: EditorSection;
  zh: string;
  en: string;
  hintZh: string;
  hintEn: string;
}[] = [
  {
    key: 'basic',
    zh: '基础',
    en: 'Basics',
    hintZh: '定义唯一标识、显示名称与适用场景',
    hintEn: 'Define the identifier, display name and when to use it',
  },
  {
    key: 'persona',
    zh: '人设',
    en: 'Persona',
    hintZh: '设定职责、回答方式与示例',
    hintEn: 'Set responsibilities, answer style and examples',
  },
  {
    key: 'knowledge',
    zh: '知识范围',
    en: 'Knowledge',
    hintZh: '限定可检索的目录与文档',
    hintEn: 'Limit the folders and documents it can search',
  },
  {
    key: 'capabilities',
    zh: '能力绑定',
    en: 'Capabilities',
    hintZh: '选择工具、技能与 MCP Server',
    hintEn: 'Pick tools, skills and MCP servers',
  },
  {
    key: 'runtime',
    zh: '委托与运行',
    en: 'Delegation & runtime',
    hintZh: '指定子助理、权限与模型参数',
    hintEn: 'Choose sub-assistants, permissions and model settings',
  },
];

// 与后端 agent_service._NAME_RE 保持一致：小写字母开头，仅限小写字母、数字、_、-，最长 64。
const AGENT_NAME_RE = /^[a-z][a-z0-9_-]{0,63}$/;
const RESERVED_AGENT_NAME = 'default';

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
  const [displayName, setDisplayName] = useState(
    existing?.display_name
      ?? (seed ? `${seed.display_name || SHORT_LABELS[seed.name] || seed.name}（副本）` : ''),
  );
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
  // 子助理候选：编辑时排除自身（后端同样拒绝自引用）
  const [subAgents, setSubAgents] = useState<string[]>(base?.sub_agents ?? []);
  const [subAgentCandidates, setSubAgentCandidates] = useState<AgentSummary[]>([]);
  const [permissionMode, setPermissionMode] = useState(base?.permission_mode ?? '');
  const [model, setModel] = useState(base?.model ?? '');
  const [maxTurns, setMaxTurns] = useState<number>(base?.max_turns ?? 0);
  const [catalog, setCatalog] = useState<CapabilityCatalog | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // 配置项按用途分区，避免单页纵向滚动过长；切换分区不丢草稿。
  const [section, setSection] = useState<EditorSection>('basic');

  useEffect(() => {
    let cancelled = false;
    void Promise.all([listMcpServers(), listBuiltinTools(), listSkills(), listAgents()])
      .then(([servers, builtins, skills, agents]) => {
        if (cancelled) return;
        setSubAgentCandidates(
          agents.items.filter((item) => item.name !== existing?.name),
        );
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
  }, [existing?.name]);

  const scopeBlocked =
    scope.mode === 'custom' &&
    (scopeSelectionIsIncomplete(scope) ||
      scopePreviewState === null ||
      scopePreviewState.doc_count === 0 ||
      scopePreviewState.unknown_paths.length > 0 ||
      scopePreviewState.unknown_doc_ids.length > 0);

  const isCreate = mode === 'create';
  const trimmedName = name.trim();
  // 标识格式错误与「未填写」分开提示：用户填了内容却不符合规则时，要直接说清原因。
  const nameIssue =
    isCreate && trimmedName !== ''
      ? trimmedName === RESERVED_AGENT_NAME
        ? 'reserved'
        : !AGENT_NAME_RE.test(trimmedName)
          ? 'invalid'
          : null
      : null;
  const nameError =
    nameIssue === 'invalid'
      ? tr(
          '格式不正确：仅限小写字母、数字、_、-，且需小写字母开头。',
          'Invalid format: lowercase letters, digits, _ and - only; must start with a lowercase letter.',
        )
      : nameIssue === 'reserved'
        ? tr('default 是系统保留名。', '"default" is reserved.')
        : null;

  // 保存按钮在底部，必填项却分散在多个分区：缺失时给出可定位的提示。
  const missingRequired: string[] = [];
  if (isCreate && trimmedName === '') missingRequired.push(tr('Agent 标识', 'Agent identifier'));
  if (displayName.trim() === '') missingRequired.push(tr('显示名称', 'Display name'));
  if (description.trim() === '') missingRequired.push(tr('描述', 'Description'));
  if (systemPrompt.trim() === '') missingRequired.push(tr('系统提示', 'System prompt'));
  const saveBlockedHint =
    missingRequired.length > 0
      ? tr(`还需填写：${missingRequired.join('、')}`, `Still required: ${missingRequired.join(', ')}`)
      : nameError !== null
        ? nameError
        : examplesInvalid(examples)
          ? tr('示例的输入与输出都不能为空。', 'Both sides of every example are required.')
          : scopeBlocked
            ? tr(
                '知识范围不可用，请到「知识范围」分区检查',
                'Knowledge scope is invalid; check the Knowledge section',
              )
            : '';
  const basicIncomplete =
    (isCreate && (trimmedName === '' || nameIssue !== null)) ||
    displayName.trim() === '' ||
    description.trim() === '';
  const personaIncomplete = systemPrompt.trim() === '';

  const sectionIndex = Math.max(
    0,
    EDITOR_SECTIONS.findIndex((item) => item.key === section),
  );
  const isLastSection = sectionIndex === EDITOR_SECTIONS.length - 1;
  const sectionIncomplete: Record<EditorSection, boolean> = {
    basic: basicIncomplete,
    persona: personaIncomplete || examplesInvalid(examples),
    knowledge: scopeBlocked,
    capabilities: false,
    runtime: false,
  };
  // 向导每一步只提示本步还缺什么，避免把后续分区的必填项提前抛给用户。
  const createStepHint = (() => {
    if (!isCreate) return saveBlockedHint;
    if (section === 'basic') {
      if (nameError !== null) return tr('请修正 Agent 标识', 'Fix the Agent identifier');
      const missing: string[] = [];
      if (trimmedName === '') missing.push(tr('Agent 标识', 'Agent identifier'));
      if (displayName.trim() === '') missing.push(tr('显示名称', 'Display name'));
      if (description.trim() === '') missing.push(tr('描述', 'Description'));
      return missing.length > 0
        ? tr(`还需填写：${missing.join('、')}`, `Still required: ${missing.join(', ')}`)
        : '';
    }
    if (section === 'persona') {
      if (systemPrompt.trim() === '') return tr('还需填写：系统提示', 'Still required: System prompt');
      if (examplesInvalid(examples)) {
        return tr('示例的输入与输出都不能为空。', 'Both sides of every example are required.');
      }
      return '';
    }
    if (section === 'knowledge' && scopeBlocked) {
      return tr(
        '知识范围不可用，请选择有效目录或文档',
        'Knowledge scope is invalid; pick a valid folder or document',
      );
    }
    return '';
  })();
  const saveDisabled =
    submitting ||
    (isCreate && (trimmedName === '' || nameIssue !== null)) ||
    displayName.trim() === '' ||
    description.trim() === '' ||
    systemPrompt.trim() === '' ||
    examplesInvalid(examples) ||
    scopeBlocked;

  const tabLabel = (label: string, incomplete: boolean) => (
    <>
      {label}
      {incomplete && (
        <span
          aria-hidden
          title={tr('有必填项未完成', 'Required fields incomplete')}
          className="h-1.5 w-1.5 rounded-full bg-warn"
        />
      )}
    </>
  );

  const submit = async () => {
    setErr(null);
    setSubmitting(true);
    try {
      const input: UserAgentInput = {
        name: mode === 'create' ? name.trim() : undefined,
        display_name: displayName.trim(),
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
        sub_agents: subAgents,
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
      setErr(errorText(e));
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
          ? tr(`编辑 ${existing?.display_name || existing?.name}`, `Edit ${existing?.display_name || existing?.name}`)
          : seed
            ? tr(`基于 ${seed.display_name || seed.name} 新建助理`, `New assistant from ${seed.display_name || seed.name}`)
            : tr('新建助理', 'New assistant')
      }
      footer={
        <>
          {createStepHint !== '' && (
            <span className="mr-auto text-[11px] text-zinc-500">{createStepHint}</span>
          )}
          <Button variant="outline" size="sm"
            type="button"
            onClick={onClose}
            className="px-3 py-1.5"
          >
            {tr('取消', 'Cancel')}
          </Button>
          {isCreate ? (
            <>
              {sectionIndex > 0 && (
                <Button
                  variant="outline"
                  size="sm"
                  type="button"
                  onClick={() => setSection(EDITOR_SECTIONS[sectionIndex - 1].key)}
                  className="px-3 py-1.5"
                >
                  {tr('上一步', 'Back')}
                </Button>
              )}
              {isLastSection ? (
                <Button
                  variant="primary"
                  size="sm"
                  type="button"
                  onClick={() => void submit()}
                  disabled={saveDisabled}
                  className="px-3 py-1.5 font-medium text-accent-fg"
                >
                  {submitting ? tr('创建中…', 'Creating…') : tr('创建', 'Create')}
                </Button>
              ) : (
                <Button
                  variant="primary"
                  size="sm"
                  type="button"
                  onClick={() => setSection(EDITOR_SECTIONS[sectionIndex + 1].key)}
                  disabled={submitting || sectionIncomplete[section]}
                  className="px-3 py-1.5 font-medium text-accent-fg"
                >
                  {tr('下一步', 'Next')}
                </Button>
              )}
            </>
          ) : (
            <Button variant="primary" size="sm"
              type="button"
              onClick={() => void submit()}
              disabled={saveDisabled}
              className="px-3 py-1.5 font-medium text-accent-fg"
            >
              {submitting ? tr('保存中…', 'Saving…') : tr('保存', 'Save')}
            </Button>
          )}
        </>
      }
    >
      <div className="space-y-4 text-xs text-zinc-300">
        {err && (
          <ErrorBanner>
            {err}
          </ErrorBanner>
        )}

        {isCreate && (
          <nav
            aria-label={tr('创建步骤', 'Creation steps')}
            className="rounded-lg border border-zinc-800/60 bg-zinc-950/40 px-3 py-2"
          >
            <ol className="flex flex-wrap items-center gap-x-2 gap-y-1">
              {EDITOR_SECTIONS.map((item, index) => {
                const active = item.key === section;
                const done = index < sectionIndex;
                return (
                  <li
                    key={item.key}
                    aria-current={active ? 'step' : undefined}
                    className="flex items-center gap-1.5"
                  >
                    {index > 0 && (
                      <ChevronRight size={11} aria-hidden className="text-zinc-600" />
                    )}
                    <span
                      className={cn(
                        'flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-medium',
                        active
                          ? 'bg-indigo-500 text-white'
                          : done
                            ? 'bg-indigo-500/15 text-indigo-300'
                            : 'bg-zinc-800 text-zinc-500',
                      )}
                    >
                      {done ? <Check size={9} aria-hidden /> : index + 1}
                    </span>
                    <span
                      className={cn('text-[11px]', active ? 'text-zinc-100' : 'text-zinc-500')}
                    >
                      {tr(item.zh, item.en)}
                    </span>
                  </li>
                );
              })}
            </ol>
            <div className="mt-1 text-[10px] text-zinc-500">
              {tr(
                `第 ${sectionIndex + 1} / ${EDITOR_SECTIONS.length} 步 · ${EDITOR_SECTIONS[sectionIndex].hintZh}`,
                `Step ${sectionIndex + 1} of ${EDITOR_SECTIONS.length} · ${EDITOR_SECTIONS[sectionIndex].hintEn}`,
              )}
            </div>
          </nav>
        )}

        <Tabs
          value={section}
          onValueChange={(value) => setSection(value as EditorSection)}
          className="space-y-4"
        >
          {!isCreate && (
            <TabsList aria-label={tr('助理配置分区', 'Assistant configuration sections')}>
              <TabsTrigger value="basic">{tabLabel(tr('基础', 'Basics'), basicIncomplete)}</TabsTrigger>
              <TabsTrigger value="persona">{tabLabel(tr('人设', 'Persona'), personaIncomplete)}</TabsTrigger>
              <TabsTrigger value="knowledge">
                {tabLabel(tr('知识范围', 'Knowledge'), scopeBlocked)}
              </TabsTrigger>
              <TabsTrigger value="capabilities">{tr('能力绑定', 'Capabilities')}</TabsTrigger>
              <TabsTrigger value="runtime">{tr('委托与运行', 'Delegation & runtime')}</TabsTrigger>
            </TabsList>
          )}

          <TabsContent value="basic" keepMounted className="min-h-[24rem] space-y-4">
            {/* 宽弹窗下基础信息并排，避免长表单变成一条竖直的窄列。 */}
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <Field label={tr('Agent 标识', 'Agent identifier')} required>
                <Input
                  type="text"
                  value={name}
                  disabled={mode === 'edit'}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={tr('例如 oncall-duty', 'e.g. oncall-duty')}
                  className="w-full font-sans"
                  maxLength={64}
                  aria-invalid={nameError !== null ? true : undefined}
                  aria-describedby="agent-name-help"
                />
                <div
                  id="agent-name-help"
                  className="mt-1 text-balance text-[11px] text-zinc-500"
                >
                  {tr(
                    '小写字母开头，仅限小写字母、数字、_、-，最长 64 位；default 不可用。',
                    'Lowercase start; only lowercase letters, digits, _ and -; max 64; "default" is reserved.',
                  )}
                  {nameError !== null && <div className="text-danger">{nameError}</div>}
                </div>
              </Field>

              <Field label={tr('显示名称', 'Display name')} required>
                <Input
                  type="text"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder={tr('例如：运维值班助理', 'e.g. 运维值班助理')}
                  className="w-full"
                  maxLength={64}
                />
                <div className="mt-1 text-balance text-[11px] text-zinc-500">
                  {tr(
                    '面向用户的展示名称，可使用中文；不参与运行时标识与调用。',
                    'User-facing label; may be localized. Not used as a runtime identifier.',
                  )}
                </div>
              </Field>
            </div>

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

            <Field label={tr('何时使用', 'When to use')}>
              <Textarea
                value={whenToUse}
                onChange={(e) => setWhenToUse(e.target.value)}
                placeholder={tr('给 coordinator 的判断线索：什么场景把任务派给这个助理', 'Hint for the coordinator: when to delegate to this assistant')}
                className="h-20 w-full resize-y"
              />
            </Field>
          </TabsContent>

          <TabsContent value="persona" keepMounted className="min-h-[24rem]">
            <PersonaFields
              value={{ systemPrompt, criticalReminder, outputFormat, examples }}
              onChange={(patch) => {
                if (patch.systemPrompt !== undefined) setSystemPrompt(patch.systemPrompt);
                if (patch.criticalReminder !== undefined) setCriticalReminder(patch.criticalReminder);
                if (patch.outputFormat !== undefined) setOutputFormat(patch.outputFormat);
                if (patch.examples !== undefined) setExamples(patch.examples);
              }}
            />
          </TabsContent>

          <TabsContent value="knowledge" keepMounted className="min-h-[24rem]">
            <KnowledgeScopePicker
              value={scope}
              onChange={setScope}
              onPreviewChange={setScopePreviewState}
            />
          </TabsContent>

          <TabsContent value="capabilities" keepMounted className="min-h-[24rem]">
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
          </TabsContent>

          <TabsContent value="runtime" keepMounted className="min-h-[24rem] space-y-4">
            <FieldGroup label={tr('子助理委托', 'Sub-assistant delegation')}>
              <p className="mb-2 text-[11px] leading-relaxed text-zinc-500">
                {tr(
                  '勾选的助理会成为父助理的可委托对象；由父助理按各自的「何时使用」判断何时转交，委托链最多 3 层。',
                  'Checked assistants become delegation targets; the parent decides when to hand off based on each one\'s "when to use", up to 3 levels deep.',
                )}
              </p>
              {subAgentCandidates.length === 0 ? (
                <div className="rounded border border-dashed border-zinc-800/60 px-2 py-1.5 text-[11px] text-zinc-500">
                  {tr('还没有其他助理可委托', 'No other assistants available yet')}
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-1 md:grid-cols-2">
                  {subAgentCandidates.map((item) => {
                    const checked = subAgents.includes(item.name);
                    return (
                      <div
                        key={item.name}
                        className="flex items-start gap-1.5 rounded px-1.5 py-1 hover:bg-zinc-900/60"
                      >
                        <Checkbox
                          checked={checked}
                          onCheckedChange={() =>
                            setSubAgents(
                              checked
                                ? subAgents.filter((name) => name !== item.name)
                                : [...subAgents, item.name],
                            )
                          }
                          className="mt-0.5 h-3 w-3"
                          aria-label={tr(`委托给 ${item.name}`, `Delegate to ${item.name}`)}
                        />
                        <div className="min-w-0">
                          <div className="truncate text-[11px] text-zinc-200">
                            {item.display_name || SHORT_LABELS[item.name] || item.name}
                            <span className="ml-1 font-sans text-[10px] text-zinc-500">({item.name})</span>
                          </div>
                          <div className="truncate text-[10px] text-zinc-500">
                            {item.when_to_use || item.description}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </FieldGroup>

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
          </TabsContent>
        </Tabs>
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
        {required && <span className="ml-0.5 text-danger">*</span>}
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
        {required && <span className="ml-0.5 text-danger">*</span>}
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
  const displayName = agent.display_name || SHORT_LABELS[agent.name] || agent.name;

  return (
    <ConfirmDialog
      open
      title={tr(`删除助理 ${displayName}`, `Delete assistant ${displayName}`)}
      description={
        <>
          <p>
            {tr('确定删除自定义助理 ', 'Delete custom assistant ')}
            <span className="text-zinc-100">{displayName}</span>
            <span className="ml-2 font-sans text-zinc-400">({agent.name})</span>?
          </p>
          <p className="mt-2 text-zinc-500">
            {tr(
              '已经用此助理建过的会话不会被删除——它们会回退到默认 coordinator 继续运行。',
              'Sessions already created with this assistant are kept — they fall back to the default coordinator.',
            )}
          </p>
        </>
      }
      onConfirm={async () => {
        await deleteAgent(agent.name);
        onDone();
      }}
      onClose={onClose}
    />
  );
}
