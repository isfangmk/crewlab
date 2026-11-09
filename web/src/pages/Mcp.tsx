import { useAssistantPage } from '@/lib/assistantPage';
import { Label, Input, Textarea } from '@/components/ui';
import { Hint } from '@/components/ui/Tooltip';
import { Switch } from '@/components/ui/Switch';
import { Select } from '@/components/ui/Select';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Copy,
  Loader2,
  Pencil,
  Play,
  PlugZap,
  Plus,
  RefreshCw,
  Search,
  Server,
  ShieldCheck,
  Trash2,
  Wrench,
  XCircle,
} from 'lucide-react';
import { useAuth } from '@/store/auth';
import { ApiError } from '@/api/client';
import {
  createMcpServer,
  deleteMcpServer,
  listMcpServers,
  parseToolsCache,
  runMcpTool,
  testMcpServer,
  updateMcpServer,
  type McpJsonSchema,
  type McpServer,
  type McpServerInput,
  type McpTool,
} from '@/api/mcp';
import { listSecrets, type SecretView } from '@/api/secrets';
import { Modal } from '@/components/Modal';
import { Button, Card, Chip, EmptyStateCard, PageHeader } from '@/components/ui';
import { cn } from '@/lib/cn';
import { useI18n } from '@/i18n/locale';

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
  const [err, setErr] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const shownServers = servers.filter((s) => {
    const q = search.trim().toLowerCase();
    if (!q) return true;
    return s.name.toLowerCase().includes(q) || s.endpoint.toLowerCase().includes(q);
  });
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);

  // editor modal: null = closed; { id: null } = create; { id } = edit
  const [editing, setEditing] = useState<{ id: number | null; input: McpServerInput } | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<McpServer | null>(null);
  const [testState, setTestState] = useState<Record<number, { loading: boolean; tools?: McpTool[]; error?: string }>>({});

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const [list, secs] = await Promise.all([listMcpServers(), listSecrets().catch(() => ({ items: [] }))]);
      setServers(list);
      setSecrets(secs.items ?? []);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 5000);
    return () => window.clearTimeout(t);
  }, [toast]);

  const errMsg = (e: unknown) => (e instanceof ApiError ? e.message : (e as Error).message);

  const handleSave = useCallback(async () => {
    if (!editing) return;
    const input = editing.input;
    if (!input.name.trim() || !input.endpoint.trim()) return;
    // wire 形如 mcp__{name}__{tool}，名称含 __ 会破坏分段解析
    if (input.name.includes('__')) {
      setToast({
        kind: 'err',
        text: tr("MCP Server 名称不能包含 '__'", "MCP server name cannot contain '__'"),
      });
      return;
    }
    setSaving(true);
    try {
      if (editing.id == null) {
        await createMcpServer(input);
        setToast({ kind: 'ok', text: tr(`已创建 ${input.name}`, `Created ${input.name}`) });
      } else {
        await updateMcpServer(editing.id, input);
        setToast({ kind: 'ok', text: tr(`已保存 ${input.name}`, `Saved ${input.name}`) });
      }
      setEditing(null);
      await refresh();
    } catch (e) {
      setToast({ kind: 'err', text: errMsg(e) });
    } finally {
      setSaving(false);
    }
  }, [editing, refresh, tr]);

  const handleDelete = useCallback(
    async (s: McpServer) => {
      try {
        await deleteMcpServer(s.id);
        setToast({ kind: 'ok', text: tr(`已删除 ${s.name}`, `Deleted ${s.name}`) });
        await refresh();
      } catch (e) {
        setToast({ kind: 'err', text: errMsg(e) });
      }
    },
    [refresh, tr],
  );

  const handleTest = useCallback(
    async (s: McpServer) => {
      setTestState((cur) => ({ ...cur, [s.id]: { loading: true } }));
      try {
        const tools = await testMcpServer(s.id);
        setTestState((cur) => ({ ...cur, [s.id]: { loading: false, tools } }));
        setToast({
          kind: 'ok',
          text: tr(`${s.name}：拉到 ${tools.length} 个工具`, `${s.name}: fetched ${tools.length} tool(s)`),
        });
        // status / tools_cache_json 已在服务端更新，重新拉取。
        await refresh();
      } catch (e) {
        const msg = errMsg(e);
        setTestState((cur) => ({ ...cur, [s.id]: { loading: false, error: msg } }));
        setToast({ kind: 'err', text: tr(`${s.name} 连接失败：${msg}`, `${s.name} connection failed: ${msg}`) });
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
              <span className="ml-1 text-amber-400">{tr('仅 admin 可增删改。', 'Only admins can add / edit / remove.')}</span>
            )}
          </>
        }
        actions={
          <>
            <Button onClick={() => void refresh()} disabled={loading} variant="ghost">
              {loading ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
              {tr('刷新', 'Refresh')}
            </Button>
            {isAdmin && (
              <Button onClick={() => setEditing({ id: null, input: emptyInput() })} variant="primary">
                <Plus size={13} />
                {tr('新建', 'New server')}
              </Button>
            )}
          </>
        }
      />
      {servers.length > 0 && (
        <div className="border-b border-zinc-800 px-6 py-3">
          <div className="flex flex-wrap items-center gap-3">
            <Label className="relative block w-64">
              <span className="sr-only">{tr('搜索', 'Search')}</span>
              <Search size={12} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500" />
              <Input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={tr('搜索服务（名称 / 端点）…', 'Search servers (name / endpoint)…')}
                className="w-full pl-8 pr-2"
              />
            </Label>
            <span className="ml-auto text-xs text-zinc-500">
              {tr(`${servers.length} 个 · 匹配 ${shownServers.length}`, `${servers.length} total · ${shownServers.length} matched`)}
            </span>
          </div>
        </div>
      )}

      <div className="flex-1 space-y-4 overflow-auto px-6 py-4">

      {err && (
        <div className="rounded-md border border-red-900/50 bg-red-950/30 px-3 py-2 text-[12px] text-red-400">{err}</div>
      )}

      {loading && servers.length === 0 ? (
        <Card className="flex h-24 items-center justify-center text-sm text-zinc-500">
          <Loader2 size={14} className="mr-2 animate-spin" /> {tr('加载中…', 'Loading…')}
        </Card>
      ) : err && servers.length === 0 ? null : servers.length === 0 ? (
        <EmptyStateCard
            icon={Server}
            title={tr('还没有注册任何 MCP 服务', 'No MCP servers registered yet')}
            action={isAdmin && <Button variant="primary" onClick={() => setEditing({ id: null, input: emptyInput() })}><Plus size={12} />{tr('接入 MCP', 'Connect MCP')}</Button>}
            hint={
              isAdmin
                ? tr('接入外部 MCP 服务，让助理使用更多工具。', 'Connect an external MCP server to give assistants more tools.')
                : tr('请联系 admin 接入 MCP 服务', 'Ask an admin to register an MCP server')
            }
        />
      ) : (
        <div className="space-y-3">
          {shownServers.length === 0 ? (
            <EmptyStateCard icon={Server} title={tr('无匹配的服务', 'No matching servers')} action={<Button onClick={() => setSearch('')}>{tr('清除筛选', 'Clear filters')}</Button>} />
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

      <Modal
        open={confirmDelete !== null}
        onClose={() => setConfirmDelete(null)}
        size="sm"
        title={confirmDelete ? tr(`删除 ${confirmDelete.name}?`, `Delete ${confirmDelete.name}?`) : ''}
        footer={
          <>
            <Button onClick={() => setConfirmDelete(null)} variant="ghost">
              {tr('取消', 'Cancel')}
            </Button>
            <Button
              onClick={() => {
                const s = confirmDelete;
                setConfirmDelete(null);
                if (s) void handleDelete(s);
              }}
              variant="danger"
            >
              <Trash2 size={12} />
              {tr('确认删除', 'Delete')}
            </Button>
          </>
        }
      >
        <p className="text-sm text-zinc-300">
          {tr('删除后该服务的工具会从 Agent / Workflow 工具箱移除。', "The server's tools will be removed from the Agent / Workflow toolbag.")}
        </p>
      </Modal>

      {toast && (
        <div
          role="status"
          className={cn(
            'fixed bottom-6 right-6 z-50 max-w-sm rounded-lg px-4 py-2.5 text-sm shadow-2xl ring-1 ring-inset',
            toast.kind === 'ok'
              ? 'bg-emerald-500/15 text-emerald-200 ring-emerald-500/40'
              : 'bg-red-500/15 text-red-200 ring-red-500/40',
          )}
        >
          {toast.text}
        </div>
      )}
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
    enabled: s.enabled,
  };
}

// ---------- server / tool expand ----------------------------------------------

function loadServerTools(server: McpServer, probed?: McpTool[]): { tools: McpTool[]; error: string | null } {
  if (probed) return { tools: probed, error: null };
  try {
    return { tools: parseToolsCache(server.tools_cache_json), error: null };
  } catch (e) {
    return { tools: [], error: e instanceof Error ? e.message : String(e) };
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
}: {
  server: McpServer;
  test?: { loading: boolean; tools?: McpTool[]; error?: string };
  isAdmin: boolean;
  onTest: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const { tr } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const [openTool, setOpenTool] = useState<string | null>(null);
  const loaded = useMemo(() => loadServerTools(server, test?.tools), [server, test?.tools]);
  const toolCount = loaded.tools.length;

  return (
    <Card className="overflow-hidden p-0">
      <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
        <button
          type="button"
          className="min-w-0 flex-1 rounded-md text-left hover:bg-zinc-900/40"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
        >
          <div className="flex flex-wrap items-center gap-2">
            {expanded ? <ChevronDown size={14} className="text-zinc-400" /> : <ChevronRight size={14} className="text-zinc-400" />}
            <Server size={14} className="text-zinc-500" />
            <span className="font-mono text-sm text-zinc-100">{server.name}</span>
            <Chip className="font-mono">{server.transport}</Chip>
            <StatusChip status={server.status} />
            {server.trusted && (
              <Chip tone="warning">
                <ShieldCheck size={10} />
                {tr('免审', 'trusted')}
              </Chip>
            )}
            {!server.enabled && <Chip tone="default">{tr('已停用', 'disabled')}</Chip>}
            <Chip tone="info">{tr(`${toolCount} 个工具`, `${toolCount} tool${toolCount === 1 ? '' : 's'}`)}</Chip>
          </div>
          <div className="mt-2 break-all font-mono text-[11px] text-zinc-500">{server.endpoint || '—'}</div>
        </button>
        <div className="flex shrink-0 flex-wrap items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
          <Button onClick={onTest} disabled={test?.loading} variant="ghost">
            {test?.loading ? <Loader2 size={11} className="animate-spin" /> : <PlugZap size={11} />}
            {tr('测试连接', 'Test')}
          </Button>
          <Hint content={isAdmin ? undefined : tr('需要 admin 权限', 'Admin permission required')}>
            <Button onClick={onEdit} disabled={!isAdmin} variant="ghost">
              <Pencil size={11} />
              {tr('编辑', 'Edit')}
            </Button>
          </Hint>
          <Hint content={isAdmin ? undefined : tr('需要 admin 权限', 'Admin permission required')}>
            <Button onClick={onDelete} disabled={!isAdmin} variant="danger">
              <Trash2 size={11} />
              {tr('删除', 'Delete')}
            </Button>
          </Hint>
        </div>
      </div>

      {server.status === 'error' && server.last_error && (
        <div className="mx-4 mb-3 rounded-md border border-red-900/50 bg-red-950/30 px-3 py-2 text-[11px] text-red-400">
          {server.last_error}
        </div>
      )}

      {test?.error && (
        <div className="mx-4 mb-3 rounded-md border border-red-900/50 bg-red-950/30 px-3 py-2 text-[11px] text-red-400">
          {test.error}
        </div>
      )}

      {expanded && (
        <div className="border-t border-zinc-800/80 bg-zinc-950/40 px-4 py-3">
          {loaded.error ? (
            <div className="rounded-md border border-amber-900/40 bg-amber-950/20 px-3 py-2 text-[12px] text-amber-200">
              {tr(
                `工具缓存缺少参数定义：${loaded.error}。请点击「测试连接」重新拉取。`,
                `Tool cache missing schema: ${loaded.error}. Click Test to refresh.`,
              )}
            </div>
          ) : toolCount === 0 ? (
            <div className="text-[12px] text-zinc-500">
              {tr('暂无工具，请先「测试连接」。', 'No tools yet. Run Test first.')}
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
        </div>
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
    <div className="rounded-lg border border-zinc-800/80 bg-zinc-900/40">
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
            <div className="font-mono text-[12px] leading-5 text-zinc-100">
              mcp__{serverName}__{tool.name}
            </div>
            {!open && tool.description && (
              <div className="mt-0.5 line-clamp-1 text-[11px] leading-4 text-zinc-500">{tool.description}</div>
            )}
          </div>
        </button>
        <div className="flex h-7 shrink-0 items-center gap-1.5">
          <Chip tone="info" dense className="h-7 inline-flex items-center">
            {tr(`${fields.length} 个参数`, `${fields.length} parameter${fields.length === 1 ? '' : 's'}`)}
          </Chip>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-2"
            onClick={(e) => {
              e.stopPropagation();
              setRunOpen(true);
            }}
          >
            <Play size={11} />
            {tr('运行', 'Run')}
          </Button>
        </div>
      </div>

      {open && (
        <div className="space-y-3 border-t border-zinc-800/70 px-3 py-3">
          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-zinc-500">{tr('描述', 'Description')}</div>
            <p className="text-[12px] leading-relaxed text-zinc-300">{tool.description || '—'}</p>
          </div>

          <div>
            <div className="mb-1.5 text-[11px] uppercase tracking-wide text-zinc-500">{tr('参数', 'Parameters')}</div>
            {fields.length === 0 ? (
              <div className="text-[12px] text-zinc-500">{tr('无参数', 'No parameters')}</div>
            ) : (
              <div className="overflow-hidden rounded-lg border border-zinc-800/80 bg-zinc-950/50 shadow-sm shadow-black/20">
                <table className="w-full text-left text-[12px]">
                  <thead className="border-b border-zinc-800/60 bg-zinc-900/80 text-[11px] uppercase tracking-wider text-zinc-500">
                    <tr>
                      <th className="px-3 py-2.5 font-medium">{tr('参数', 'Parameter')}</th>
                      <th className="px-3 py-2.5 font-medium">{tr('类型', 'Type')}</th>
                      <th className="px-3 py-2.5 font-medium">{tr('必填', 'Required')}</th>
                      <th className="px-3 py-2.5 font-medium">{tr('默认', 'Default')}</th>
                      <th className="px-3 py-2.5 font-medium">{tr('说明', 'Description')}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/50">
                    {fields.map((field) => (
                      <tr key={field.name} className="bg-zinc-950/20 transition-colors hover:bg-zinc-900/70">
                        <td className="px-3 py-2.5 align-top">
                          <span className="rounded bg-zinc-800/80 px-1.5 py-0.5 font-mono text-[11px] text-sky-300">
                            {field.name}
                          </span>
                        </td>
                        <td className="px-3 py-2.5 align-top">
                          <span className="rounded border border-zinc-700/80 bg-zinc-900 px-1.5 py-0.5 font-mono text-[10px] text-violet-300">
                            {field.type}
                          </span>
                        </td>
                        <td className="px-3 py-2.5 align-top">
                          {field.required ? (
                            <Chip tone="warning" dense>{tr('必填', 'required')}</Chip>
                          ) : (
                            <Chip tone="default" dense>{tr('可选', 'optional')}</Chip>
                          )}
                        </td>
                        <td className="px-3 py-2.5 align-top font-mono text-[11px] text-zinc-500">
                          {field.defaultValue || '—'}
                        </td>
                        <td className="max-w-md px-3 py-2.5 align-top text-[12px] leading-relaxed text-zinc-400">
                          {field.description || '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
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
      setRunErr(e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  const onCopy = async () => {
    if (resultText == null) return;
    await navigator.clipboard.writeText(resultText);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };

  return (
    <Modal
      open
      onClose={onClose}
      size="lg"
      title={tr(`运行 ${tool.name}`, `Run ${tool.name}`)}
      footer={
        <>
          <Button onClick={onClose} variant="ghost">{tr('关闭', 'Close')}</Button>
          {resultText != null && (
            <Button variant="ghost" onClick={() => void onCopy()}>
              <Copy size={12} />
              {copied ? tr('已复制', 'Copied') : tr('复制结果', 'Copy result')}
            </Button>
          )}
          <Button variant="primary" disabled={running} onClick={() => void onRun()}>
            {running ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
            {running ? tr('运行中…', 'Running…') : tr('运行', 'Run')}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="rounded-md border border-zinc-800 bg-zinc-950/40 px-3 py-2">
          <div className="font-mono text-[11px] leading-4 text-zinc-100">
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
                    <span className="shrink-0 font-mono text-[11px] text-sky-300">{field.name}</span>
                    {field.required ? <span className="shrink-0 text-[10px] text-amber-400">*</span> : null}
                    <span className="shrink-0 font-mono text-[10px] text-zinc-600">{field.type}</span>
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
                      className="h-8 w-full"
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
                      className="w-full font-mono text-[12px]"
                    />
                  ) : (
                    <Input
                      type={field.type === 'number' || field.type === 'integer' ? 'number' : 'text'}
                      value={values[field.name] ?? ''}
                      onChange={(e) => setValues((cur) => ({ ...cur, [field.name]: e.target.value }))}
                      className="h-8 w-full font-mono text-[12px]"
                    />
                  )}
                </Label>
              );
            })}
          </div>
        )}

        {runErr && (
          <div className="rounded-md border border-red-900/50 bg-red-950/30 px-3 py-2 text-[12px] text-red-300">{runErr}</div>
        )}
        {resultText != null && (
          <div>
            <div className="mb-1.5 text-[11px] uppercase tracking-wide text-zinc-500">{tr('运行结果', 'Result')}</div>
            <pre
              className={cn(
                'max-h-72 overflow-auto rounded-md border px-3 py-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap',
                resultIsError
                  ? 'border-red-900/50 bg-red-950/20 text-red-200'
                  : 'border-zinc-800 bg-zinc-950 text-zinc-200',
              )}
            >
              {resultText}
            </pre>
          </div>
        )}
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
          <Button onClick={onClose} variant="ghost">
            {tr('取消', 'Cancel')}
          </Button>
          <Button onClick={onSave} disabled={!canSave} variant="primary">
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
            className={cn(inputClass, "font-mono")}
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
              className={cn(inputClass, "font-mono")}
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
            className={cn(inputClass, "font-mono")}
          />
        </Field>

        <Toggle
          label={tr('Trusted（免审）', 'Trusted')}
          checked={input.trusted}
          onChange={(v) => onChange({ trusted: v })}
        />
        {input.trusted && (
          <div className="flex items-start gap-2 rounded-md border border-amber-900/50 bg-amber-950/20 px-3 py-2 text-[11px] text-amber-400">
            <AlertTriangle size={13} className="mt-0.5 shrink-0" />
            <span>{tr('免审直跑：仅建议给只读服务，跳过人工确认。', 'Runs without approval. Recommended for read-only servers only.')}</span>
          </div>
        )}

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
    <Label className="flex w-full items-center justify-between gap-3 rounded-md border border-border px-3 py-2 text-sm text-text"><span>{label}</span><Switch checked={checked} onCheckedChange={onChange} aria-label={label} /></Label>
  );
}
