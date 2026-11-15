import { useAssistantPage } from '@/lib/assistantPage';
import { flash, FilterBar, Label, Input, SearchInput, TableHead, Textarea } from '@/components/ui';
import { Hint } from '@/components/ui/Tooltip';
import { useTimeout } from '@/lib/useTimeout';
import { useRequestSeq } from '@/lib/requestSeq';
import { Switch } from '@/components/ui/Switch';
import { Select } from '@/components/ui/Select';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Copy,
  Eye,
  FileText,
  Globe,
  KeyRound,
  Loader2,
  Pencil,
  Play,
  PlugZap,
  Plus,
  RefreshCw,
  Server,
  ShieldCheck,
  Sparkles,
  Trash2,
  Wrench,
  XCircle,
} from 'lucide-react';
import { useAuth } from '@/store/auth';
import { ApiError } from '@/api/client';
import {
  createMcpServer,
  deleteMcpServer,
  getMcpPrompt,
  getMcpOauthStatus,
  listMcpPrompts,
  listMcpResources,
  listMcpServers,
  parseToolsCache,
  readMcpResource,
  revokeMcpOauth,
  runMcpTool,
  saveMcpOauthToken,
  searchMcpRegistry,
  startMcpOauth,
  testMcpServer,
  updateMcpServer,
  type McpJsonSchema,
  type McpOauthStatus,
  type McpPrompt,
  type McpRegistryItem,
  type McpResource,
  type McpServer,
  type McpServerInput,
  type McpTool,
} from '@/api/mcp';
import { listSecrets, type SecretView } from '@/api/secrets';
import { Modal } from '@/components/Modal';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { Button, Card, Chip, CodeBlock, DetailSection, EmptyStateCard, ErrorBanner, PageHeader, RowAction, RowActionIcon } from '@/components/ui';
import { cn } from '@/lib/cn';
import { useI18n } from '@/i18n/locale';
import { errorText } from '@/lib/errors';

// MCP 服务注册页：列表可展开看工具；工具可展开看参数表并试跑。

const inputClass =
  "w-full";

const NONE = '__none__';

function emptyInput(): McpServerInput {
  return {
    name: '',
    transport: 'http',
    endpoint: '',
    credential: '',
    header_template_json: '{}',
    trusted: false,
    approval_policy: 'always',
    enabled: true,
  };
}

export default function McpPage() {
  const { tr } = useI18n();
  const role = useAuth((s) => s.role);
  const isAdmin = role === 'admin';

  const [servers, setServers] = useState<McpServer[]>([]);
  const [secrets, setSecrets] = useState<SecretView[]>([]);
  const [loading, setLoading] = useState(true);
  const reqSeq = useRequestSeq();
  const [err, setErr] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const shownServers = servers.filter((s) => {
    const q = search.trim().toLowerCase();
    if (!q) return true;
    return s.name.toLowerCase().includes(q) || s.endpoint.toLowerCase().includes(q);
  });

  // editor modal: null = closed; { id: null } = create; { id } = edit
  const [editing, setEditing] = useState<{ id: number | null; input: McpServerInput } | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<McpServer | null>(null);
  const [testState, setTestState] = useState<Record<number, { loading: boolean; tools?: McpTool[]; error?: string }>>({});
  const [registryOpen, setRegistryOpen] = useState(false);

  const refresh = useCallback(async () => {
    const current = reqSeq.next();
    setLoading(true);
    setErr(null);
    try {
      const [list, secs] = await Promise.all([listMcpServers(), listSecrets().catch(() => ({ items: [] }))]);
      if (reqSeq.isStale(current)) return;
      setServers(list);
      setSecrets(secs.items ?? []);
    } catch (e) {
      if (reqSeq.isStale(current)) return;
      setErr(errorText(e));
    } finally {
      if (!reqSeq.isStale(current)) {
        setLoading(false);
      }
    }
  }, [reqSeq]);

  useEffect(() => {
    void refresh();
  }, [refresh]);



  const errMsg = (e: unknown) => (errorText(e));

  const handleSave = useCallback(async () => {
    if (!editing) return;
    const input = editing.input;
    if (!input.name.trim() || !input.endpoint.trim()) return;
    // wire 形如 mcp__{name}__{tool}，名称含 __ 会破坏分段解析
    if (input.name.includes('__')) {
      flash('err', tr("MCP Server 名称不能包含 '__'", "MCP server name cannot contain '__'"));
      return;
    }
    setSaving(true);
    try {
      if (editing.id == null) {
        await createMcpServer(input);
        flash('ok', tr(`已创建 ${input.name}`, `Created ${input.name}`));
      } else {
        await updateMcpServer(editing.id, input);
        flash('ok', tr(`已保存 ${input.name}`, `Saved ${input.name}`));
      }
      setEditing(null);
      await refresh();
    } catch (e) {
      flash('err', errMsg(e));
    } finally {
      setSaving(false);
    }
  }, [editing, refresh, tr]);

  const handleDelete = useCallback(
    async (s: McpServer) => {
      // 失败向上抛给 ConfirmDialog 行内展示，避免弹窗关闭后只剩一条 Toast
      await deleteMcpServer(s.id);
      flash('ok', tr(`已删除 ${s.name}`, `Deleted ${s.name}`));
      await refresh();
    },
    [refresh, tr],
  );

  const handleTest = useCallback(
    async (s: McpServer) => {
      setTestState((cur) => ({ ...cur, [s.id]: { loading: true } }));
      try {
        const tools = await testMcpServer(s.id);
        setTestState((cur) => ({ ...cur, [s.id]: { loading: false, tools } }));
        flash('ok', tr(`${s.name}：拉到 ${tools.length} 个工具`, `${s.name}: fetched ${tools.length} tool(s)`));
        // status / tools_cache_json 已在服务端更新，重新拉取。
        await refresh();
      } catch (e) {
        const msg = errMsg(e);
        setTestState((cur) => ({ ...cur, [s.id]: { loading: false, error: msg } }));
        flash('err', tr(`${s.name} 连接失败：${msg}`, `${s.name} connection failed: ${msg}`));
      }
    },
    [refresh, tr],
  );

  useAssistantPage('MCP 服务', 'MCP servers', {
    server_count: servers.length,
    visible_count: shownServers.length,
    sample_server_ids: shownServers.slice(0, 6).map(item => item.id).join(', '),
    loading,
  });

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tr('MCP 服务', 'MCP servers')}
        subtitle={
          <>
            {tr(
              '注册外部 MCP server 后，它暴露的工具会进入 Agent / 工作流 工具箱。工具执行默认需要人工确认；标记为 trusted 的服务免审直跑（仅建议给只读服务）。',
              'Registered external MCP servers expose tools into the Agent / Workflow toolbag. Tool execution requires human approval by default; servers marked trusted run without approval (recommended for read-only servers only).',
            )}
            {!isAdmin && (
              <span className="ml-1 text-warn">{tr('仅 admin 可增删改。', 'Only admins can add / edit / remove.')}</span>
            )}
          </>
        }
        actions={
          <>
            <RowAction
              icon={RefreshCw}
              iconClassName={loading ? 'animate-spin' : undefined}
              disabled={loading}
              onClick={() => void refresh()}
            >
              {tr('刷新', 'Refresh')}
            </RowAction>
            {isAdmin && (
              <>
                <RowAction icon={Globe} onClick={() => setRegistryOpen(true)}>
                  {tr('从 Registry 添加', 'Add from Registry')}
                </RowAction>
                <Button onClick={() => setEditing({ id: null, input: emptyInput() })} variant="primary" size="sm">
                  <Plus size={12} />
                  {tr('新建', 'New server')}
                </Button>
              </>
            )}
          </>
        }
      />
      {servers.length > 0 && (
        <FilterBar className="mx-6 mt-3">
          <SearchInput
            value={search}
            onChange={setSearch}
            placeholder={tr('搜索服务（名称 / 端点）…', 'Search servers (name / endpoint)…')}
            ariaLabel={tr('搜索服务', 'Search servers')}
          />
          <span className="ml-auto text-[11px] tabular-nums text-zinc-500">
            {tr(`共 ${shownServers.length} 个服务`, `${shownServers.length} services`)}
          </span>
        </FilterBar>
      )}

      <div className="flex-1 space-y-4 overflow-auto px-6 py-4">

      {err && <ErrorBanner>{err}</ErrorBanner>}

      {loading && servers.length === 0 ? (
        <Card className="flex h-24 items-center justify-center text-[11px] text-zinc-500">
          <Loader2 size={12} className="mr-2 animate-spin" /> {tr('加载中…', 'Loading…')}
        </Card>
      ) : err && servers.length === 0 ? null : servers.length === 0 ? (
        <EmptyStateCard
            icon={Server}
            title={tr('还没有注册任何 MCP 服务', 'No MCP servers registered yet')}
            action={isAdmin && <Button variant="primary" size="sm" onClick={() => setEditing({ id: null, input: emptyInput() })}><Plus size={12} />{tr('接入 MCP', 'Connect MCP')}</Button>}
            hint={
              isAdmin
                ? tr('接入外部 MCP 服务，让助理使用更多工具。', 'Connect an external MCP server to give assistants more tools.')
                : tr('请联系 admin 接入 MCP 服务', 'Ask an admin to register an MCP server')
            }
        />
      ) : (
        <div className="space-y-3">
          {shownServers.length === 0 ? (
            <EmptyStateCard icon={Server} title={tr('无匹配的服务', 'No matching servers')} action={<Button size="sm" onClick={() => setSearch('')}>{tr('清除筛选', 'Clear filters')}</Button>} />
          ) : (
            shownServers.map((s) => (
              <ServerRow
                key={s.id}
                server={s}
                test={testState[s.id]}
                isAdmin={isAdmin}
                onTest={() => void handleTest(s)}
                onEdit={() => setEditing({ id: s.id, input: toInput(s) })}
                onDelete={() => setConfirmDelete(s)}
                onRefresh={() => void refresh()}
              />
            ))
          )}
        </div>
      )}

      {editing && (
        <ServerEditor
          id={editing.id}
          input={editing.input}
          secrets={secrets}
          saving={saving}
          onChange={(patch) => setEditing((cur) => (cur ? { ...cur, input: { ...cur.input, ...patch } } : cur))}
          onClose={() => setEditing(null)}
          onSave={() => void handleSave()}
        />
      )}

      {registryOpen && (
        <RegistryDialog
          onClose={() => setRegistryOpen(false)}
          onPick={(item) => {
            // Registry 预填登记表单：endpoint 缺失（包形式）时留空，由用户补部署地址
            setRegistryOpen(false);
            setEditing({
              id: null,
              input: {
                ...emptyInput(),
                name: item.name,
                transport: item.transport,
                endpoint: item.endpoint,
              },
            });
          }}
        />
      )}

      <ConfirmDialog
        open={confirmDelete !== null}
        title={confirmDelete ? tr(`删除 ${confirmDelete.name}?`, `Delete ${confirmDelete.name}?`) : ''}
        description={tr('删除后该服务的工具会从 Agent / Workflow 工具箱移除。', "The server's tools will be removed from the Agent / Workflow toolbag.")}
        onConfirm={async () => {
          if (confirmDelete) await handleDelete(confirmDelete);
        }}
        onClose={() => setConfirmDelete(null)}
      />

      </div>
    </main>
  );
}

function toInput(s: McpServer): McpServerInput {
  return {
    name: s.name,
    transport: s.transport,
    endpoint: s.endpoint,
    credential: s.credential,
    header_template_json: s.header_template_json,
    trusted: s.trusted,
    approval_policy: s.approval_policy ?? 'always',
    enabled: s.enabled,
  };
}

// ---------- server / tool expand ----------------------------------------------

function loadServerTools(server: McpServer, probed?: McpTool[]): { tools: McpTool[]; error: string | null } {
  if (probed) return { tools: probed, error: null };
  try {
    return { tools: parseToolsCache(server.tools_cache_json), error: null };
  } catch (e) {
    return { tools: [], error: errorText(e) };
  }
}

function schemaTypeLabel(schema: McpJsonSchema): string {
  if (typeof schema.type === 'string') return schema.type;
  if (Array.isArray(schema.type)) return schema.type.join('|');
  if (Array.isArray(schema.anyOf)) {
    return schema.anyOf.map((item) => schemaTypeLabel(item)).join('|');
  }
  return 'unknown';
}

function schemaFields(schema: McpJsonSchema): Array<{
  name: string;
  type: string;
  required: boolean;
  description: string;
  defaultValue: string;
}> {
  const properties = schema.properties;
  if (!properties || typeof properties !== 'object') {
    return [];
  }
  const required = new Set(Array.isArray(schema.required) ? schema.required.filter((n) => typeof n === 'string') : []);
  return Object.entries(properties).map(([name, prop]) => ({
    name,
    type: schemaTypeLabel(prop),
    required: required.has(name),
    description: typeof prop.description === 'string' ? prop.description : '',
    defaultValue: prop.default === undefined ? '' : JSON.stringify(prop.default),
  }));
}

function ServerRow({
  server,
  test,
  isAdmin,
  onTest,
  onEdit,
  onDelete,
  onRefresh,
}: {
  server: McpServer;
  test?: { loading: boolean; tools?: McpTool[]; error?: string };
  isAdmin: boolean;
  onTest: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onRefresh: () => void;
}) {
  const { tr } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const [openTool, setOpenTool] = useState<string | null>(null);
  const [oauthOpen, setOauthOpen] = useState(false);
  const loaded = useMemo(() => loadServerTools(server, test?.tools), [server, test?.tools]);
  const toolCount = loaded.tools.length;
  // 测试失败优先展示最新一次结果；否则展示上次连接留下的错误。
  const errorMessage = test?.error || (server.status === 'error' ? server.last_error : '');
  const freePass = server.trusted || server.approval_policy === 'never';
  const approvalLabel =
    server.approval_policy === 'non_readonly'
      ? tr('写操作审批', 'Writes need approval')
      : tr('每次审批', 'Always asks');
  const embeddingLabel =
    server.embedding_sync_status === 'ok'
      ? tr(`索引 ${server.embedding_sync_count ?? 0}`, `Index ${server.embedding_sync_count ?? 0}`)
      : server.embedding_sync_status === 'syncing'
        ? tr('索引同步中', 'Indexing…')
        : server.embedding_sync_status === 'error'
          ? tr('索引失败', 'Index failed')
          : '';
  const iconClass = !server.enabled
    ? 'bg-zinc-800/40 text-zinc-500 ring-zinc-700/50'
    : server.status === 'error'
      ? 'bg-gradient-to-br from-rose-500/25 via-rose-500/10 to-transparent text-rose-300 ring-rose-500/20'
      : 'bg-gradient-to-br from-indigo-500/25 via-indigo-500/10 to-transparent text-indigo-300 ring-indigo-500/20';

  return (
    <Card className="overflow-hidden p-0 transition-colors hover:border-zinc-700/70">
      <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:justify-between">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-start gap-2.5 rounded-md text-left"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
        >
          <span className="flex h-9 w-3.5 shrink-0 items-center justify-center text-zinc-500">
            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </span>
          <span className={cn('grid h-9 w-9 shrink-0 place-items-center rounded-lg ring-1 ring-inset', iconClass)}>
            <Server size={16} />
          </span>
          <div className="min-w-0 flex-1">
            {/* 身份行：名称 + 连接状态 + 风险标记；传输方式与端点放到下一行。 */}
            <div className="flex min-w-0 flex-wrap items-center gap-1.5">
              <span className="min-w-0 truncate text-[13px] font-semibold leading-5 text-zinc-100">{server.name}</span>
              <StatusChip status={server.status} />
              {freePass && (
                <Chip dense tone="warning">
                  <ShieldCheck size={10} />
                  {tr('免审', 'trusted')}
                </Chip>
              )}
              {!server.enabled && <Chip dense>{tr('已停用', 'disabled')}</Chip>}
            </div>
            <div className="mt-1 flex min-w-0 items-center gap-1.5 text-[10px] text-zinc-500">
              <span className="shrink-0 font-medium uppercase tracking-wide text-zinc-400">{server.transport}</span>
              <span className="shrink-0 text-zinc-700">·</span>
              <span className="min-w-0 truncate" title={server.endpoint}>{server.endpoint || '—'}</span>
            </div>
            {/* 摘要行：工具/资源/提示词数 · 审批口径 · 工具索引，沿用分隔点串联。 */}
            <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-zinc-500">
              <span className={cn(loaded.error && 'text-warn')}>
                {loaded.error
                  ? tr('工具缓存异常', 'Tool cache error')
                  : tr(`${toolCount} 个工具`, `${toolCount} tool${toolCount === 1 ? '' : 's'}`)}
              </span>
              {!loaded.error && (
                <>
                  <span className="text-zinc-700">·</span>
                  <span>
                    {tr(
                      `资源 ${server.resources_count ?? 0}`,
                      `Resource${(server.resources_count ?? 0) === 1 ? '' : 's'} ${server.resources_count ?? 0}`,
                    )}
                  </span>
                  <span className="text-zinc-700">·</span>
                  <span>
                    {tr(
                      `提示词 ${server.prompts_count ?? 0}`,
                      `Prompt${(server.prompts_count ?? 0) === 1 ? '' : 's'} ${server.prompts_count ?? 0}`,
                    )}
                  </span>
                </>
              )}
              {/* OAuth/Token 授权态：只在有授权记录时展示，避免无 OAuth 服务的噪声 */}
              {server.oauth_status && server.oauth_status !== 'none' && (
                <>
                  <span className="text-zinc-700">·</span>
                  <span className={cn(server.oauth_status === 'expired' && 'text-warn')}>
                    {server.oauth_status === 'authorized'
                      ? tr('OAuth 已授权', 'OAuth authorized')
                      : tr('OAuth 已过期', 'OAuth expired')}
                  </span>
                </>
              )}
              {!freePass && (
                <>
                  <span className="text-zinc-700">·</span>
                  <span>{approvalLabel}</span>
                </>
              )}
              {embeddingLabel !== '' && (
                <>
                  <span className="text-zinc-700">·</span>
                  <span
                    className={cn(server.embedding_sync_status === 'error' && 'text-warn')}
                    title={server.embedding_sync_error || undefined}
                  >
                    {embeddingLabel}
                  </span>
                </>
              )}
            </div>
          </div>
        </button>
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5 sm:pl-2" onClick={(e) => e.stopPropagation()}>
          <RowAction
            icon={test?.loading ? Loader2 : PlugZap}
            iconClassName={test?.loading ? 'animate-spin' : undefined}
            onClick={onTest}
            disabled={test?.loading}
          >
            {tr('测试连接', 'Test')}
          </RowAction>
          <Hint content={isAdmin ? tr('OAuth 授权', 'OAuth') : tr('需要 admin 权限', 'Admin permission required')}>
            <RowActionIcon icon={KeyRound} label={tr('OAuth 授权', 'OAuth')} onClick={() => setOauthOpen(true)} disabled={!isAdmin} />
          </Hint>
          <Hint content={isAdmin ? tr('编辑', 'Edit') : tr('需要 admin 权限', 'Admin permission required')}>
            <RowActionIcon icon={Pencil} label={tr('编辑', 'Edit')} onClick={onEdit} disabled={!isAdmin} />
          </Hint>
          <Hint content={isAdmin ? tr('删除', 'Delete') : tr('需要 admin 权限', 'Admin permission required')}>
            <RowActionIcon icon={Trash2} label={tr('删除', 'Delete')} tone="danger" onClick={onDelete} disabled={!isAdmin} />
          </Hint>
        </div>
      </div>

      {errorMessage && <ErrorBanner className="mx-4 mb-3">{errorMessage}</ErrorBanner>}

      {expanded && (
        <div className="border-t border-zinc-800/60 bg-zinc-900/30 px-4 py-3">
          <div className="mb-2.5 flex items-center gap-2">
            <Wrench size={12} className="text-zinc-500" />
            <span className="text-[11px] font-medium text-zinc-300">{tr('工具清单', 'Tool catalog')}</span>
            <span className="text-[10px] text-zinc-500">{tr(`${toolCount} 个`, `${toolCount}`)}</span>
          </div>
          {loaded.error ? (
            <div className="flex items-start gap-2 rounded-lg border border-warn/30 bg-warn/10 px-3 py-2 text-[11.5px] leading-5 text-warn">
              <AlertTriangle size={12} className="mt-0.5 shrink-0" />
              <span>
                {tr(
                  `工具缓存缺少参数定义：${loaded.error}。请点击「测试连接」重新拉取。`,
                  `Tool cache missing schema: ${loaded.error}. Click Test to refresh.`,
                )}
              </span>
            </div>
          ) : toolCount === 0 ? (
            <div className="text-[12px] text-zinc-500">
              {tr('暂无工具，点击右上角「测试连接」重新拉取。', 'No tools yet. Click Test in the top-right corner to refresh.')}
            </div>
          ) : (
            <div className="space-y-2">
              {loaded.tools.map((tool) => (
                <ToolRow
                  key={tool.name}
                  serverId={server.id}
                  serverName={server.name}
                  tool={tool}
                  open={openTool === tool.name}
                  onToggle={() => setOpenTool((cur) => (cur === tool.name ? null : tool.name))}
                />
              ))}
            </div>
          )}
          {/* 资源 / 提示词分组：展开时实时拉取，失败给横幅与重试 */}
          <ServerExtras server={server} />
        </div>
      )}

      {oauthOpen && (
        <OAuthDialog
          server={server}
          onClose={() => setOauthOpen(false)}
          onChanged={onRefresh}
        />
      )}
    </Card>
  );
}

function ToolRow({
  serverId,
  serverName,
  tool,
  open,
  onToggle,
}: {
  serverId: number;
  serverName: string;
  tool: McpTool;
  open: boolean;
  onToggle: () => void;
}) {
  const { tr } = useI18n();
  const fields = useMemo(() => schemaFields(tool.input_schema), [tool.input_schema]);
  const [runOpen, setRunOpen] = useState(false);

  return (
    <div className="rounded-lg border border-zinc-800/60 bg-zinc-900/40">
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-2 rounded-md text-left hover:bg-zinc-900/60"
          onClick={onToggle}
          aria-expanded={open}
        >
          {open ? <ChevronDown size={13} className="shrink-0 text-zinc-400" /> : <ChevronRight size={13} className="shrink-0 text-zinc-400" />}
          <Wrench size={13} className="shrink-0 text-zinc-500" />
          <div className="min-w-0 flex-1">
            <div className="font-sans text-[12.5px] leading-5 text-zinc-100">
              mcp__{serverName}__{tool.name}
            </div>
            {!open && tool.description && (
              <div className="mt-0.5 line-clamp-1 text-[11px] leading-4 text-zinc-500">{tool.description}</div>
            )}
          </div>
        </button>
        <div className="flex h-7 shrink-0 items-center gap-1.5">
          <Chip tone="info" dense>
            {tr(`${fields.length} 个参数`, `${fields.length} parameter${fields.length === 1 ? '' : 's'}`)}
          </Chip>
          <RowAction
            icon={Play}
            onClick={(e) => {
              e.stopPropagation();
              setRunOpen(true);
            }}
          >
            {tr('运行', 'Run')}
          </RowAction>
        </div>
      </div>

      {open && (
        <div className="space-y-3 border-t border-zinc-800/70 px-3 py-3">
          <DetailSection title={tr('描述', 'Description')}>
            <p className="text-[12px] leading-relaxed text-zinc-300">{tool.description || '—'}</p>
          </DetailSection>

          <DetailSection title={tr('参数', 'Parameters')}>
            {fields.length === 0 ? (
              <div className="text-[12px] text-zinc-500">{tr('无参数', 'No parameters')}</div>
            ) : (
              <div className="overflow-hidden rounded-lg border border-zinc-800/60 bg-zinc-950/30">
                <table className="og-action-table w-full text-left text-[12px]">
                  <TableHead>
                    <tr>
                      <th scope="col" className="px-3 py-2 text-left font-medium">{tr('参数', 'Parameter')}</th>
                      <th scope="col" className="px-3 py-2 text-left font-medium">{tr('类型', 'Type')}</th>
                      <th scope="col" className="px-3 py-2 text-left font-medium">{tr('必填', 'Required')}</th>
                      <th scope="col" className="px-3 py-2 text-left font-medium">{tr('默认', 'Default')}</th>
                      <th scope="col" className="px-3 py-2 text-left font-medium">{tr('说明', 'Description')}</th>
                    </tr>
                  </TableHead>
                  <tbody className="divide-y divide-zinc-800/50">
                    {fields.map((field) => (
                      <tr key={field.name} className="transition-colors hover:bg-zinc-900/70">
                        <td className="px-3 py-2 align-top">
                          <Chip dense tone="info" className="font-sans">{field.name}</Chip>
                        </td>
                        <td className="px-3 py-2 align-top">
                          <Chip dense className="font-sans">{field.type}</Chip>
                        </td>
                        <td className="px-3 py-2 align-top">
                          {field.required ? (
                            <Chip tone="warning" dense>{tr('必填', 'required')}</Chip>
                          ) : (
                            <Chip tone="default" dense>{tr('可选', 'optional')}</Chip>
                          )}
                        </td>
                        <td className="px-3 py-2 align-top font-sans text-[11px] text-zinc-500">
                          {field.defaultValue || '—'}
                        </td>
                        <td className="max-w-md px-3 py-2 align-top text-[12px] leading-relaxed text-zinc-400">
                          {field.description || '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </DetailSection>
        </div>
      )}

      {runOpen && (
        <ToolRunModal
          serverId={serverId}
          serverName={serverName}
          tool={tool}
          fields={fields}
          onClose={() => setRunOpen(false)}
        />
      )}
    </div>
  );
}

type SchemaField = {
  name: string;
  type: string;
  required: boolean;
  description: string;
  defaultValue: string;
};

function ToolRunModal({
  serverId,
  serverName,
  tool,
  fields,
  onClose,
}: {
  serverId: number;
  serverName: string;
  tool: McpTool;
  fields: SchemaField[];
  onClose: () => void;
}) {
  const { tr } = useI18n();
  const [values, setValues] = useState<Record<string, string>>(() => {
    const next: Record<string, string> = {};
    for (const field of fields) {
      next[field.name] = field.defaultValue === '' ? '' : field.defaultValue;
    }
    return next;
  });
  const [running, setRunning] = useState(false);
  const [runErr, setRunErr] = useState<string | null>(null);
  const [resultText, setResultText] = useState<string | null>(null);
  const [resultIsError, setResultIsError] = useState(false);
  const [copied, setCopied] = useState(false);

  const buildArguments = (): Record<string, unknown> => {
    const args: Record<string, unknown> = {};
    for (const field of fields) {
      const raw = values[field.name] ?? '';
      if (raw.trim() === '') {
        if (field.required) {
          throw new Error(tr(`参数 ${field.name} 必填`, `Parameter ${field.name} is required`));
        }
        continue;
      }
      if (field.type === 'string' || field.type.startsWith('string')) {
        args[field.name] = raw;
        continue;
      }
      if (field.type === 'number' || field.type === 'integer') {
        const n = Number(raw);
        if (Number.isNaN(n)) {
          throw new Error(tr(`参数 ${field.name} 必须是数字`, `Parameter ${field.name} must be a number`));
        }
        args[field.name] = field.type === 'integer' ? Math.trunc(n) : n;
        continue;
      }
      if (field.type === 'boolean') {
        if (raw !== 'true' && raw !== 'false') {
          throw new Error(tr(`参数 ${field.name} 必须是 true/false`, `Parameter ${field.name} must be true/false`));
        }
        args[field.name] = raw === 'true';
        continue;
      }
      args[field.name] = JSON.parse(raw) as unknown;
    }
    return args;
  };

  const onRun = async () => {
    setRunning(true);
    setRunErr(null);
    setResultText(null);
    setCopied(false);
    try {
      const arguments_ = buildArguments();
      const resp = await runMcpTool(serverId, tool.name, arguments_);
      setResultText(resp.result_text);
      setResultIsError(resp.is_error);
    } catch (e) {
      setRunErr(e instanceof ApiError ? e.message : errorText(e));
    } finally {
      setRunning(false);
    }
  };

  const copyTimer = useTimeout();
  const onCopy = async () => {
    if (resultText == null) return;
    await navigator.clipboard.writeText(resultText);
    setCopied(true);
    copyTimer(() => setCopied(false), 2000);
  };

  return (
    <Modal
      open
      onClose={onClose}
      size="lg"
      title={tr(`运行 ${tool.name}`, `Run ${tool.name}`)}
      footer={
        <>
          <Button size="sm" onClick={onClose} variant="ghost">{tr('关闭', 'Close')}</Button>
          <Button size="sm" variant="primary" disabled={running} onClick={() => void onRun()}>
            {running ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
            {running ? tr('运行中…', 'Running…') : tr('运行', 'Run')}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="rounded-lg border border-zinc-800/60 bg-zinc-950/60 px-3 py-2">
          <div className="font-sans text-[11px] leading-4 text-zinc-100">
            mcp__{serverName}__{tool.name}
          </div>
          {tool.description ? (
            <p className="mt-1 line-clamp-2 text-[11px] leading-4 text-zinc-500">{tool.description}</p>
          ) : null}
        </div>

        {fields.length === 0 ? (
          <div className="text-[12px] text-zinc-500">{tr('无参数，可直接运行。', 'No parameters. You can run directly.')}</div>
        ) : (
          <div className="grid grid-cols-1 gap-x-3 gap-y-2.5 sm:grid-cols-2">
            {fields.map((field) => {
              const wide = field.type.includes('array') || field.type.includes('object');
              return (
                <Label
                  key={field.name}
                  className={cn('block min-w-0', wide && 'sm:col-span-2')}
                >
                  <span className="mb-0.5 flex h-5 items-center gap-1 overflow-hidden">
                    <span className="shrink-0 font-sans text-[11px] text-info">{field.name}</span>
                    {field.required ? <span className="shrink-0 text-[10px] text-warn">*</span> : null}
                    <span className="shrink-0 font-sans text-[10px] text-zinc-600">{field.type}</span>
                    {field.description ? (
                      <Hint content={field.description}>
                        <span className="min-w-0 flex-1 truncate text-[10px] text-zinc-500">{field.description}</span>
                      </Hint>
                    ) : null}
                  </span>
                  {field.type === 'boolean' ? (
                    <Select
                      label={field.name}
                      value={values[field.name] ?? ''}
                      onValueChange={(v) => setValues((cur) => ({ ...cur, [field.name]: v }))}
                      className="w-full"
                    >
                      <option value="">{tr('（空）', '(empty)')}</option>
                      <option value="true">true</option>
                      <option value="false">false</option>
                    </Select>
                  ) : wide ? (
                    <Textarea
                      value={values[field.name] ?? ''}
                      onChange={(e) => setValues((cur) => ({ ...cur, [field.name]: e.target.value }))}
                      rows={2}
                      placeholder={field.type.includes('array') ? '["a","b"]' : '{"k":"v"}'}
                      className="w-full font-sans text-[12px]"
                    />
                  ) : (
                    <Input
                      type={field.type === 'number' || field.type === 'integer' ? 'number' : 'text'}
                      value={values[field.name] ?? ''}
                      onChange={(e) => setValues((cur) => ({ ...cur, [field.name]: e.target.value }))}
                      className="w-full font-sans text-[12px]"
                    />
                  )}
                </Label>
              );
            })}
          </div>
        )}

        {runErr && <ErrorBanner>{runErr}</ErrorBanner>}
        {resultText != null && (
          <DetailSection
            title={tr('运行结果', 'Result')}
            actions={
              <RowAction icon={Copy} onClick={() => void onCopy()}>
                {copied ? tr('已复制', 'Copied') : tr('复制结果', 'Copy result')}
              </RowAction>
            }
          >
            <CodeBlock className={resultIsError ? 'border-danger/30 bg-danger/10 text-danger' : undefined}>
              {resultText}
            </CodeBlock>
          </DetailSection>
        )}
      </div>
    </Modal>
  );
}

// ---------- 资源 / 提示词 ------------------------------------------------------

function ServerExtras({ server }: { server: McpServer }) {
  const { tr } = useI18n();
  const [loading, setLoading] = useState(true);
  const [resources, setResources] = useState<McpResource[]>([]);
  const [prompts, setPrompts] = useState<McpPrompt[]>([]);
  const [extrasErr, setExtrasErr] = useState<string | null>(null);
  const [readUri, setReadUri] = useState<string | null>(null);
  const [usePrompt, setUsePrompt] = useState<McpPrompt | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setExtrasErr(null);
    try {
      const [res, prs] = await Promise.all([
        listMcpResources(server.id),
        listMcpPrompts(server.id),
      ]);
      setResources(res);
      setPrompts(prs);
    } catch (e) {
      setExtrasErr(errorText(e));
    } finally {
      setLoading(false);
    }
  }, [server.id]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="mt-4 space-y-4">
      {extrasErr && (
        <ErrorBanner className="flex items-center justify-between gap-2">
          <span>{tr(`资源 / 提示词拉取失败：${extrasErr}`, `Failed to load resources / prompts: ${extrasErr}`)}</span>
          <RowAction icon={RefreshCw} onClick={() => void load()}>
            {tr('重试', 'Retry')}
          </RowAction>
        </ErrorBanner>
      )}

      {/* 资源分组 */}
      <div>
        <div className="mb-2.5 flex items-center gap-2">
          <FileText size={12} className="text-zinc-500" />
          <span className="text-[11px] font-medium text-zinc-300">{tr('资源', 'Resources')}</span>
          <span className="text-[10px] text-zinc-500">{tr(`${resources.length} 个`, `${resources.length}`)}</span>
        </div>
        {loading ? (
          <div className="flex items-center gap-2 text-[11px] text-zinc-500">
            <Loader2 size={12} className="animate-spin" /> {tr('加载中…', 'Loading…')}
          </div>
        ) : resources.length === 0 && !extrasErr ? (
          <EmptyStateCard
            icon={FileText}
            title={tr('该服务未暴露资源', 'This server exposes no resources')}
            hint={tr('资源由 MCP Server 的 resources 能力提供。', 'Resources come from the MCP server resources capability.')}
          />
        ) : resources.length > 0 ? (
          <div className="overflow-hidden rounded-lg border border-zinc-800/60 bg-zinc-950/30">
            <table className="og-action-table w-full text-left text-[12px]">
              <TableHead>
                <tr>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('名称', 'Name')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">URI</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('类型', 'Type')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('操作', 'Actions')}</th>
                </tr>
              </TableHead>
              <tbody className="divide-y divide-zinc-800/50">
                {resources.map((res) => (
                  <tr key={res.uri} className="transition-colors hover:bg-zinc-900/70">
                    <td className="px-3 py-2 align-top">
                      <span className="font-sans text-[12px] text-zinc-100">{res.name || '—'}</span>
                      {res.description && (
                        <div className="mt-0.5 line-clamp-1 text-[11px] text-zinc-500">{res.description}</div>
                      )}
                    </td>
                    <td className="max-w-xs px-3 py-2 align-top font-sans text-[11px] text-zinc-400">
                      <span className="block truncate" title={res.uri}>{res.uri}</span>
                    </td>
                    <td className="px-3 py-2 align-top">
                      <Chip dense className="font-sans">{res.mime_type || '—'}</Chip>
                    </td>
                    <td className="px-3 py-2 align-top">
                      <RowAction icon={Eye} onClick={() => setReadUri(res.uri)}>
                        {tr('读取', 'Read')}
                      </RowAction>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>

      {/* 提示词分组 */}
      <div>
        <div className="mb-2.5 flex items-center gap-2">
          <Sparkles size={12} className="text-zinc-500" />
          <span className="text-[11px] font-medium text-zinc-300">{tr('提示词', 'Prompts')}</span>
          <span className="text-[10px] text-zinc-500">{tr(`${prompts.length} 个`, `${prompts.length}`)}</span>
        </div>
        {loading ? (
          <div className="flex items-center gap-2 text-[11px] text-zinc-500">
            <Loader2 size={12} className="animate-spin" /> {tr('加载中…', 'Loading…')}
          </div>
        ) : prompts.length === 0 && !extrasErr ? (
          <EmptyStateCard
            icon={Sparkles}
            title={tr('该服务未暴露提示词', 'This server exposes no prompts')}
            hint={tr('提示词由 MCP Server 的 prompts 能力提供。', 'Prompts come from the MCP server prompts capability.')}
          />
        ) : prompts.length > 0 ? (
          <div className="overflow-hidden rounded-lg border border-zinc-800/60 bg-zinc-950/30">
            <table className="og-action-table w-full text-left text-[12px]">
              <TableHead>
                <tr>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('名称', 'Name')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('参数', 'Arguments')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('说明', 'Description')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('操作', 'Actions')}</th>
                </tr>
              </TableHead>
              <tbody className="divide-y divide-zinc-800/50">
                {prompts.map((prompt) => (
                  <tr key={prompt.name} className="transition-colors hover:bg-zinc-900/70">
                    <td className="px-3 py-2 align-top font-sans text-[12px] text-zinc-100">{prompt.name}</td>
                    <td className="px-3 py-2 align-top">
                      <div className="flex flex-wrap gap-1">
                        {prompt.arguments.length === 0 ? (
                          <Chip dense>{tr('无参数', 'none')}</Chip>
                        ) : (
                          prompt.arguments.map((arg) => (
                            <Chip key={arg.name} dense tone={arg.required ? 'warning' : 'default'} className="font-sans">
                              {arg.name}
                            </Chip>
                          ))
                        )}
                      </div>
                    </td>
                    <td className="max-w-sm px-3 py-2 align-top text-[12px] leading-relaxed text-zinc-400">
                      {prompt.description || '—'}
                    </td>
                    <td className="px-3 py-2 align-top">
                      <RowAction icon={Play} onClick={() => setUsePrompt(prompt)}>
                        {tr('使用', 'Use')}
                      </RowAction>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>

      {readUri && (
        <ResourceReadDialog serverId={server.id} uri={readUri} onClose={() => setReadUri(null)} />
      )}
      {usePrompt && (
        <PromptUseDialog serverId={server.id} prompt={usePrompt} onClose={() => setUsePrompt(null)} />
      )}
    </div>
  );
}

function ResourceReadDialog({
  serverId,
  uri,
  onClose,
}: {
  serverId: number;
  uri: string;
  onClose: () => void;
}) {
  const { tr } = useI18n();
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [blocks, setBlocks] = useState<Array<{ uri: string; mime_type: string; text?: string; blob?: string }>>([]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setErr(null);
    readMcpResource(serverId, uri)
      .then((r) => {
        if (alive) setBlocks(r.contents);
      })
      .catch((e) => {
        if (alive) setErr(errorText(e));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [serverId, uri]);

  return (
    <Modal open onClose={onClose} size="lg" title={tr(`读取 ${uri}`, `Read ${uri}`)}>
      <div className="space-y-3">
        {loading ? (
          <div className="flex items-center gap-2 text-[12px] text-zinc-500">
            <Loader2 size={12} className="animate-spin" /> {tr('读取中…', 'Reading…')}
          </div>
        ) : err ? (
          <ErrorBanner>{err}</ErrorBanner>
        ) : (
          blocks.map((block, index) => (
            <DetailSection
              key={`${block.uri}-${index}`}
              title={block.mime_type || tr('内容', 'Content')}
            >
              {block.text != null ? (
                <CodeBlock>{block.text}</CodeBlock>
              ) : (
                <div className="rounded-lg border border-zinc-800/60 bg-zinc-950/30 px-3 py-2 text-[11px] text-zinc-500">
                  {tr(
                    `二进制内容（base64）：${(block.blob ?? '').length} 字符，暂不预览。`,
                    `Binary content (base64): ${(block.blob ?? '').length} chars, no preview.`,
                  )}
                </div>
              )}
            </DetailSection>
          ))
        )}
      </div>
    </Modal>
  );
}

function PromptUseDialog({
  serverId,
  prompt,
  onClose,
}: {
  serverId: number;
  prompt: McpPrompt;
  onClose: () => void;
}) {
  const { tr } = useI18n();
  const [values, setValues] = useState<Record<string, string>>({});
  const [running, setRunning] = useState(false);
  const [runErr, setRunErr] = useState<string | null>(null);
  const [messages, setMessages] = useState<Array<{ role: string; text: string }> | null>(null);

  const onUse = async () => {
    setRunning(true);
    setRunErr(null);
    setMessages(null);
    try {
      const args: Record<string, string> = {};
      for (const arg of prompt.arguments) {
        const raw = values[arg.name] ?? '';
        if (raw.trim() === '' && arg.required) {
          throw new Error(tr(`参数 ${arg.name} 必填`, `Argument ${arg.name} is required`));
        }
        if (raw !== '') args[arg.name] = raw;
      }
      const resp = await getMcpPrompt(serverId, prompt.name, args);
      setMessages(resp.messages);
    } catch (e) {
      setRunErr(e instanceof ApiError ? e.message : errorText(e));
    } finally {
      setRunning(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      size="lg"
      title={tr(`使用 ${prompt.name}`, `Use ${prompt.name}`)}
      footer={
        <>
          <Button size="sm" onClick={onClose} variant="ghost">{tr('关闭', 'Close')}</Button>
          <Button size="sm" variant="primary" disabled={running} onClick={() => void onUse()}>
            {running ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
            {running ? tr('取用中…', 'Fetching…') : tr('取用提示词', 'Fetch prompt')}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {prompt.description && (
          <p className="text-[12px] leading-relaxed text-zinc-400">{prompt.description}</p>
        )}
        {prompt.arguments.length > 0 && (
          <div className="grid grid-cols-1 gap-x-3 gap-y-2.5 sm:grid-cols-2">
            {prompt.arguments.map((arg) => (
              <Label key={arg.name} className="block min-w-0">
                <span className="mb-0.5 flex h-5 items-center gap-1 overflow-hidden">
                  <span className="shrink-0 font-sans text-[11px] text-info">{arg.name}</span>
                  {arg.required ? <span className="shrink-0 text-[10px] text-warn">*</span> : null}
                  {arg.description ? (
                    <span className="min-w-0 flex-1 truncate text-[10px] text-zinc-500">{arg.description}</span>
                  ) : null}
                </span>
                <Input
                  type="text"
                  value={values[arg.name] ?? ''}
                  onChange={(e) => setValues((cur) => ({ ...cur, [arg.name]: e.target.value }))}
                  className="w-full font-sans text-[12px]"
                />
              </Label>
            ))}
          </div>
        )}
        {runErr && <ErrorBanner>{runErr}</ErrorBanner>}
        {messages && (
          <DetailSection title={tr('提示词消息', 'Prompt messages')}>
            <div className="space-y-2">
              {messages.map((message, index) => (
                <div key={index} className="rounded-lg border border-zinc-800/60 bg-zinc-950/30 px-3 py-2">
                  <Chip dense tone="info" className="font-sans">{message.role}</Chip>
                  <CodeBlock className="mt-1.5">{message.text}</CodeBlock>
                </div>
              ))}
            </div>
          </DetailSection>
        )}
      </div>
    </Modal>
  );
}

// ---------- Registry 一键登记 --------------------------------------------------

function RegistryDialog({
  onClose,
  onPick,
}: {
  onClose: () => void;
  onPick: (item: McpRegistryItem) => void;
}) {
  const { tr } = useI18n();
  const reqSeq = useRequestSeq();
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<McpRegistryItem[]>([]);
  const [nextCursor, setNextCursor] = useState('');
  const [loading, setLoading] = useState(false);
  const [moreLoading, setMoreLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // 加载更多失败：保留失败 cursor，重试从断点续拉，不丢已加载项
  const [moreErr, setMoreErr] = useState<{ cursor: string; message: string } | null>(null);
  const [searched, setSearched] = useState(false);

  const runSearch = useCallback(
    async (cursor: string, append: boolean) => {
      const current = reqSeq.next();
      if (append) {
        setMoreLoading(true);
        setMoreErr(null);
      } else {
        // 新搜索立即清空旧结果，避免与上一次查询的残留混排
        setLoading(true);
        setErr(null);
        setItems([]);
        setNextCursor('');
      }
      try {
        const r = await searchMcpRegistry(query, 50, cursor);
        if (reqSeq.isStale(current)) return;
        // 游标深翻可能跨页重复：按 name|endpoint 去重后再追加
        setItems((cur) => {
          const base = append ? cur : [];
          const seen = new Set(base.map((it) => `${it.name}|${it.endpoint}`));
          const merged = [...base];
          for (const it of r.items) {
            const key = `${it.name}|${it.endpoint}`;
            if (seen.has(key)) continue;
            seen.add(key);
            merged.push(it);
          }
          return merged;
        });
        setNextCursor(r.next_cursor);
        setSearched(true);
      } catch (e) {
        if (reqSeq.isStale(current)) return;
        const message = errorText(e);
        if (append) {
          setMoreErr({ cursor, message });
        } else {
          setErr(message);
        }
      } finally {
        if (!reqSeq.isStale(current)) {
          if (append) {
            setMoreLoading(false);
          } else {
            setLoading(false);
          }
        }
      }
    },
    [query, reqSeq],
  );

  return (
    <Modal
      open
      onClose={onClose}
      size="lg"
      title={tr('从 Registry 添加', 'Add from Registry')}
      footer={
        <Button size="sm" onClick={onClose} variant="ghost">{tr('关闭', 'Close')}</Button>
      }
    >
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <SearchInput
            value={query}
            onChange={setQuery}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void runSearch('', false);
            }}
            placeholder={tr('搜索官方注册表…', 'Search the official registry…')}
            ariaLabel={tr('搜索注册表', 'Search registry')}
            className="min-w-[13.5rem] max-w-[14.5rem]"
            fluid
          />
          {/* 请求在途也允许发起新搜索：序号守卫丢弃旧响应，实现抢占式搜索 */}
          <RowAction
            icon={loading ? Loader2 : Globe}
            iconClassName={loading ? 'animate-spin' : undefined}
            onClick={() => void runSearch('', false)}
          >
            {tr('搜索', 'Search')}
          </RowAction>
        </div>

        {err && (
          <ErrorBanner className="flex items-center justify-between gap-2">
            <span>{err}</span>
            <RowAction icon={RefreshCw} onClick={() => void runSearch('', false)}>
              {tr('重试', 'Retry')}
            </RowAction>
          </ErrorBanner>
        )}

        {moreErr && (
          <ErrorBanner className="flex items-center justify-between gap-2">
            <span>{tr(`加载更多失败：${moreErr.message}`, `Failed to load more: ${moreErr.message}`)}</span>
            <RowAction icon={RefreshCw} onClick={() => void runSearch(moreErr.cursor, true)}>
              {tr('重试', 'Retry')}
            </RowAction>
          </ErrorBanner>
        )}

        {!err && searched && items.length > 0 && (
          <div className="text-[11px] text-zinc-500">
            {tr(`已加载 ${items.length} 个`, `${items.length} loaded`)}
          </div>
        )}

        {!err && !searched && items.length === 0 ? (
          <EmptyStateCard
            icon={Globe}
            title={tr('搜索官方 MCP 注册表', 'Search the official MCP registry')}
            hint={tr(
              '输入关键词搜索，选中后预填登记表单。',
              'Search by keyword; picking a result prefills the registration form.',
            )}
          />
        ) : !err && searched && items.length === 0 ? (
          <EmptyStateCard
            icon={Globe}
            title={tr('无匹配结果', 'No matching results')}
            hint={tr('换个关键词试试。', 'Try a different keyword.')}
          />
        ) : items.length > 0 ? (
          <div className="max-h-[22rem] space-y-2 overflow-y-auto">
            {items.map((item) => (
              <div key={`${item.name}-${item.endpoint}`} className="rounded-lg border border-zinc-800/60 bg-zinc-900/40 px-3 py-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                      <span className="min-w-0 truncate font-sans text-[12.5px] text-zinc-100">{item.name}</span>
                      <Chip dense>{item.transport}</Chip>
                    </div>
                    {item.description && (
                      <div className="mt-0.5 line-clamp-2 text-[11px] leading-4 text-zinc-500">{item.description}</div>
                    )}
                    {item.endpoint && (
                      <div className="mt-0.5 truncate font-sans text-[10px] text-zinc-500" title={item.endpoint}>
                        {item.endpoint}
                      </div>
                    )}
                    {item.hint && (
                      <div className="mt-1 flex items-start gap-1.5 text-[10.5px] leading-4 text-warn">
                        <AlertTriangle size={11} className="mt-0.5 shrink-0" />
                        <span>{item.hint}</span>
                      </div>
                    )}
                  </div>
                  <RowAction icon={Plus} onClick={() => onPick(item)}>
                    {tr('添加', 'Add')}
                  </RowAction>
                </div>
              </div>
            ))}
            {nextCursor !== '' && !moreErr && (
              <div className="flex justify-center">
                <RowAction icon={Loader2} iconClassName={moreLoading ? 'animate-spin' : undefined} disabled={moreLoading} onClick={() => void runSearch(nextCursor, true)}>
                  {tr('加载更多', 'Load more')}
                </RowAction>
              </div>
            )}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}

// ---------- OAuth 授权 --------------------------------------------------------

function OAuthDialog({
  server,
  onClose,
  onChanged,
}: {
  server: McpServer;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { tr } = useI18n();
  const [status, setStatus] = useState<McpOauthStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [manualOpen, setManualOpen] = useState(false);
  const [accessToken, setAccessToken] = useState('');
  const [refreshToken, setRefreshToken] = useState('');
  const [expiresIn, setExpiresIn] = useState('');
  const [confirmRevoke, setConfirmRevoke] = useState(false);
  const pollTimer = useRef<number | null>(null);

  const loadStatus = useCallback(async (): Promise<McpOauthStatus | null> => {
    try {
      const s = await getMcpOauthStatus(server.id);
      setStatus(s);
      return s;
    } catch (e) {
      setErr(errorText(e));
      return null;
    } finally {
      setLoading(false);
    }
  }, [server.id]);

  useEffect(() => {
    void loadStatus();
    return () => {
      if (pollTimer.current !== null) window.clearInterval(pollTimer.current);
    };
  }, [loadStatus]);

  // 浏览器授权回调后轮询状态（2s 一次，至多 5 分钟）
  const startPolling = useCallback(() => {
    if (pollTimer.current !== null) window.clearInterval(pollTimer.current);
    let ticks = 0;
    pollTimer.current = window.setInterval(() => {
      ticks += 1;
      if (ticks > 150) {
        if (pollTimer.current !== null) window.clearInterval(pollTimer.current);
        pollTimer.current = null;
        return;
      }
      void (async () => {
        const s = await loadStatus();
        if (s?.authorized && pollTimer.current !== null) {
          window.clearInterval(pollTimer.current);
          pollTimer.current = null;
          onChanged();
        }
      })();
    }, 2000);
  }, [loadStatus, onChanged]);

  const onAuthorize = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await startMcpOauth(server.id);
      window.open(r.authorize_url, '_blank');
      startPolling();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const onSaveManual = async () => {
    setBusy(true);
    setErr(null);
    try {
      const parsed = expiresIn.trim() === '' ? undefined : Number(expiresIn);
      if (parsed !== undefined && (!Number.isFinite(parsed) || parsed <= 0)) {
        throw new Error(tr('有效期必须是正数秒', 'Expiry must be a positive number of seconds'));
      }
      const s = await saveMcpOauthToken(server.id, {
        access_token: accessToken,
        refresh_token: refreshToken,
        expires_in: parsed,
      });
      setStatus(s);
      setManualOpen(false);
      setAccessToken('');
      setRefreshToken('');
      setExpiresIn('');
      onChanged();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const onRevoke = async () => {
    setBusy(true);
    setErr(null);
    try {
      await revokeMcpOauth(server.id);
      setStatus({ authorized: false, auth_source: null, expires_at: null, expired: false });
      onChanged();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
      setConfirmRevoke(false);
    }
  };

  const statusLabel =
    status === null
      ? tr('加载中…', 'Loading…')
      : status.authorized
        ? tr(
            `已授权（${status.auth_source === 'manual' ? '手动' : 'OAuth'}）`,
            `Authorized (${status.auth_source ?? 'oauth'})`,
          )
        : status.expired
          ? tr('已过期', 'Expired')
          : tr('未授权', 'Not authorized');

  return (
    <Modal
      open
      onClose={onClose}
      size="md"
      title={tr(`OAuth 授权 · ${server.name}`, `OAuth · ${server.name}`)}
      footer={
        <Button size="sm" onClick={onClose} variant="ghost">{tr('关闭', 'Close')}</Button>
      }
    >
      <div className="space-y-4">
        <div className="flex items-center gap-2 text-[12px] text-zinc-300">
          <span className="text-zinc-500">{tr('当前状态：', 'Status: ')}</span>
          {loading ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <span className={cn(status?.expired && 'text-warn')}>{statusLabel}</span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <RowAction icon={busy ? Loader2 : KeyRound} iconClassName={busy ? 'animate-spin' : undefined} disabled={busy} onClick={() => void onAuthorize()}>
            {tr('开始授权', 'Authorize')}
          </RowAction>
          <RowAction icon={Pencil} onClick={() => setManualOpen((v) => !v)}>
            {tr('手动粘贴 Token', 'Paste token manually')}
          </RowAction>
          {status?.authorized && (
            <RowAction icon={Trash2} tone="danger" onClick={() => setConfirmRevoke(true)}>
              {tr('解绑', 'Unlink')}
            </RowAction>
          )}
        </div>

        <p className="text-[11px] leading-5 text-zinc-500">
          {tr(
            '开始授权会在新窗口打开授权服务器页面（授权码 + PKCE）；完成后本弹窗自动刷新状态。不支持动态注册的服务请用「手动粘贴 Token」。',
            'Authorize opens the authorization server in a new tab (auth code + PKCE); this dialog refreshes automatically. For servers without dynamic registration, paste a token manually.',
          )}
        </p>

        {manualOpen && (
          <div className="space-y-3 rounded-lg border border-zinc-800/60 bg-zinc-950/40 p-3">
            <Field label={tr('Access Token（必填）', 'Access token (required)')}>
              <Input
                type="text"
                value={accessToken}
                onChange={(e) => setAccessToken(e.target.value)}
                className={cn(inputClass, 'font-sans')}
              />
            </Field>
            <Field label={tr('Refresh Token（可选，不自动刷新）', 'Refresh token (optional, no auto refresh)')}>
              <Input
                type="text"
                value={refreshToken}
                onChange={(e) => setRefreshToken(e.target.value)}
                className={cn(inputClass, 'font-sans')}
              />
            </Field>
            <Field label={tr('有效期（秒，可选）', 'Expires in (seconds, optional)')}>
              <Input
                type="number"
                value={expiresIn}
                onChange={(e) => setExpiresIn(e.target.value)}
                className="w-36 font-sans"
              />
            </Field>
            <Button size="sm" variant="primary" disabled={busy || accessToken.trim() === ''} onClick={() => void onSaveManual()}>
              {busy ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
              {tr('保存 Token', 'Save token')}
            </Button>
          </div>
        )}

        {err && <ErrorBanner>{err}</ErrorBanner>}

        <ConfirmDialog
          open={confirmRevoke}
          title={tr('解绑 Token？', 'Unlink token?')}
          description={tr(
            '解绑后该服务的请求不再携带授权头，需要重新授权或粘贴 Token。',
            'Requests will no longer carry the auth header; re-authorize or paste a token to restore access.',
          )}
          confirmLabel={tr('解绑', 'Unlink')}
          danger
          onConfirm={async () => {
            await onRevoke();
          }}
          onClose={() => setConfirmRevoke(false)}
        />
      </div>
    </Modal>
  );
}

function StatusChip({ status }: { status: string }) {
  const { tr } = useI18n();
  if (status === 'ok') {
    return (
      <Chip tone="success">
        <CheckCircle2 size={10} />
        {tr('正常', 'ok')}
      </Chip>
    );
  }
  if (status === 'error') {
    return (
      <Chip tone="danger">
        <XCircle size={10} />
        {tr('异常', 'error')}
      </Chip>
    );
  }
  return <Chip tone="default">{tr('未探测', 'untested')}</Chip>;
}

// ---------- editor modal ------------------------------------------------------

function ServerEditor({
  id,
  input,
  secrets,
  saving,
  onChange,
  onClose,
  onSave,
}: {
  id: number | null;
  input: McpServerInput;
  secrets: SecretView[];
  saving: boolean;
  onChange: (patch: Partial<McpServerInput>) => void;
  onClose: () => void;
  onSave: () => void;
}) {
  const { tr } = useI18n();
  const canSave = input.name.trim().length > 0 && input.endpoint.trim().length > 0 && !saving;

  return (
    <Modal
      open
      onClose={onClose}
      size="lg"
      title={id == null ? tr('新建 MCP 服务', 'New MCP server') : tr(`编辑 ${input.name}`, `Edit ${input.name}`)}
      footer={
        <>
          <Button size="sm" onClick={onClose} variant="ghost">
            {tr('取消', 'Cancel')}
          </Button>
          <Button size="sm" onClick={onSave} disabled={!canSave} variant="primary">
            {saving ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
            {saving ? tr('保存中…', 'Saving…') : tr('保存', 'Save')}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field
          label={tr('名称', 'Name')}
          hint={tr(
            '唯一标识，也是工具前缀：该服务的工具会暴露为 mcp__<name>__*',
            'Unique label, also the tool prefix: this server\'s tools are exposed as mcp__<name>__*',
          )}
        >
          <Input
            type="text"
            value={input.name}
            onChange={(e) => onChange({ name: e.target.value })}
            placeholder="github"
            className={cn(inputClass, "font-sans")}
          />
        </Field>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-[160px_1fr]">
          <Field label={tr('传输方式', 'Transport')} hint={tr('http 或 sse', 'http or sse')}>
            <Select
              label={tr('传输方式', 'Transport')}
              value={input.transport}
              onValueChange={(selectedValue) => {
                if (selectedValue !== 'http' && selectedValue !== 'sse') {
                  throw new Error(`不支持的 transport: ${selectedValue}`);
                }
                onChange({ transport: selectedValue });
              }}
              className="w-full"
            >
              <option value="http">http</option>
              <option value="sse">sse</option>
            </Select>
          </Field>
          <Field label={tr('端点 URL', 'Endpoint URL')} hint={tr('Streamable HTTP MCP 端点', 'Streamable HTTP MCP endpoint')}>
            <Input
              type="text"
              value={input.endpoint}
              onChange={(e) => onChange({ endpoint: e.target.value })}
              placeholder="https://mcp.example.com/sse"
              className={cn(inputClass, "font-sans")}
            />
          </Field>
        </div>

        <Field
          label={tr('凭证', 'Credential')}
          hint={tr('用于填充 header 模板里的 {{字段}}；选「（无）」表示不注入认证', 'Fills the {{field}} placeholders in the header template; pick "(none)" for no auth injection')}
        >
          <Select label={tr('凭证', 'Credential')}
            value={input.credential || NONE}
            onValueChange={(selectedValue) => onChange({ credential: selectedValue === NONE ? '' : selectedValue })}
            className="w-full"
          >
            <option value={NONE}>{tr('（无）', '(none)')}</option>
            {secrets.map((s) => (
              <option key={s.id} value={s.name}>
                {s.name}
              </option>
            ))}
          </Select>
        </Field>

        <Field
          label={tr('Header 模板', 'Header template')}
          hint={tr(
            'JSON map；{{字段}} 会用所选凭证的同名字段填充。',
            'JSON map; {{field}} placeholders are filled from the selected credential\'s fields.',
          )}
        >
          <Textarea
            value={input.header_template_json}
            onChange={(e) => onChange({ header_template_json: e.target.value })}
            placeholder={'{"Authorization":"Bearer {{token}}"}'}
            rows={3}
            className={cn(inputClass, "font-sans")}
          />
        </Field>

        <Toggle
          label={tr('Trusted（映射为 never）', 'Trusted (maps to never)')}
          checked={input.trusted}
          onChange={(v) =>
            onChange({
              trusted: v,
              approval_policy: v ? 'never' : input.approval_policy === 'never' ? 'always' : input.approval_policy,
            })
          }
        />
        {input.trusted && (
          <div className="flex items-start gap-2 rounded-lg border border-warn/30 bg-warn/10 px-3 py-2 text-[11.5px] leading-5 text-warn">
            <AlertTriangle size={13} className="mt-0.5 shrink-0" />
            <span>{tr('免审直跑：仅建议给只读服务，跳过人工确认。', 'Runs without approval. Recommended for read-only servers only.')}</span>
          </div>
        )}

        <Field label={tr('审批策略', 'Approval policy')}>
          <Select
            label={tr('审批策略', 'Approval policy')}
            value={input.approval_policy}
            onValueChange={(v) =>
              onChange({
                approval_policy: v as McpServerInput['approval_policy'],
                trusted: v === 'never',
              })
            }
            className="w-full"
          >
            <option value="always">{tr('始终审批', 'Always')}</option>
            <option value="non_readonly">{tr('非只读才审批', 'Non-readonly')}</option>
            <option value="never">{tr('永不审批', 'Never')}</option>
          </Select>
        </Field>

        <Toggle
          label={tr('启用', 'Enabled')}
          checked={input.enabled}
          onChange={(v) => onChange({ enabled: v })}
        />
      </div>
    </Modal>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <Label className="block">
      <span className="mb-1 block text-[11px] text-zinc-400">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-zinc-500">{hint}</span>}
    </Label>
  );
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <Label className="flex w-full items-center justify-between gap-3 rounded-lg border border-border px-3 py-2 text-[11px] text-text"><span>{label}</span><Switch checked={checked} onCheckedChange={onChange} aria-label={label} /></Label>
  );
}
