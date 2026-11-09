import { useAssistantPage } from '@/lib/assistantPage';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Hammer, Loader2, Play, RefreshCw, Search } from 'lucide-react';
import { cn } from '@/lib/cn';
import {
  listBuiltinTools,
  runBuiltinTool,
  type BuiltinTool,
  type BuiltinToolParam,
} from '@/api/tools';
import { ApiError } from '@/api/client';
import { Modal } from '@/components/Modal';
import { Button, EmptyStateCard, Input, Label, PageHeader } from '@/components/ui';
import { Hint } from '@/components/ui/Tooltip';
import { useI18n } from '@/i18n/locale';

type RiskFilter = '' | 'safe' | 'approval_required';

/** 内置 Tool 目录页：版式对齐技能目录（筛选条 + 分组表 + 详情 / 运行弹窗）。 */
export default function ToolsPage() {
  const { tr } = useI18n();
  const [items, setItems] = useState<BuiltinTool[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [risk, setRisk] = useState<RiskFilter>('');
  const [collapsed, setCollapsed] = useState(false);
  const [viewing, setViewing] = useState<BuiltinTool | null>(null);
  const [running, setRunning] = useState<BuiltinTool | null>(null);

  const fetchTools = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true);
    else setLoading(true);
    try {
      const res = await listBuiltinTools();
      if (!Array.isArray(res.items)) {
        throw new Error('内置 Tool 列表缺少 items');
      }
      setItems(res.items);
      setErr(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void fetchTools();
  }, [fetchTools]);

  const counts = useMemo(() => {
    let safe = 0;
    let approval = 0;
    for (const t of items) {
      if (t.risk === 'safe') safe += 1;
      else if (t.risk === 'approval_required') approval += 1;
      else throw new Error(`未知 Tool risk: ${t.risk}`);
    }
    return { safe, approval, total: items.length };
  }, [items]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter((t) => {
      if (risk !== '' && t.risk !== risk) return false;
      if (!q) return true;
      return (
        t.name.toLowerCase().includes(q) ||
        t.description.toLowerCase().includes(q)
      );
    });
  }, [items, query, risk]);

  useAssistantPage('工具目录', 'Tool catalog', {
    risk,
    visible_tools: filtered.length,
    selected_tool: viewing?.name ?? running?.name,
    loading,
  });

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tr('工具', 'Tools')}
        subtitle={tr(
          '平台自带 Tool（非 Skill、非 MCP 全量列表）',
          'Built-in Tools (not Skills, not full MCP catalog)',
        )}
      />

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
                placeholder={tr('搜索 name / description', 'Search name / description')}
                className="w-full pl-8 pr-2"
              />
            </Label>
            <div className="inline-flex items-center gap-1.5 text-xs text-zinc-400">
              <span className="text-zinc-500">{tr('风险', 'Risk')}</span>
              <div className="flex flex-wrap gap-1">
                <FilterChip
                  active={risk === ''}
                  label={`${tr('全部', 'All')} ${counts.total}`}
                  onClick={() => setRisk('')}
                />
                <FilterChip
                  active={risk === 'safe'}
                  label={`${tr('安全', 'Safe')} ${counts.safe}`}
                  onClick={() => setRisk('safe')}
                />
                <FilterChip
                  active={risk === 'approval_required'}
                  label={`${tr('需审批', 'Approval')} ${counts.approval}`}
                  onClick={() => setRisk('approval_required')}
                />
              </div>
            </div>
            <div className="ml-auto flex items-center gap-2">
              <span className="text-xs text-zinc-500">
                {tr(`${items.length} 个 · 匹配 ${filtered.length}`, `${items.length} total · ${filtered.length} matched`)}
              </span>
              <Button
                variant="outline"
                size="sm"
                type="button"
                onClick={() => void fetchTools(true)}
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
              {tr('加载失败：', 'Load failed: ')}
              {err}
            </div>
          )}
          {loading ? (
            <div className="flex h-40 items-center justify-center text-sm text-zinc-500">
              {tr('加载中…', 'Loading…')}
            </div>
          ) : err && items.length === 0 ? null : filtered.length === 0 ? (
            <EmptyState
              hasFilter={query.trim() !== '' || risk !== ''}
              onClear={() => {
                setQuery('');
                setRisk('');
              }}
            />
          ) : (
            <div className="overflow-hidden rounded-xl border border-zinc-800/60 bg-zinc-900/30">
              <table className="w-full text-sm">
                <thead className="border-b border-zinc-800/60 bg-zinc-950/40 text-[11px] uppercase tracking-wider text-zinc-500">
                  <tr className="whitespace-nowrap">
                    <th className="px-4 py-2.5 text-left">{tr('名称', 'Name')}</th>
                    <th className="px-4 py-2.5 text-left">{tr('风险', 'Risk')}</th>
                    <th className="w-full px-4 py-2.5 text-left">{tr('描述', 'Description')}</th>
                    <th className="px-4 py-2.5 text-right">{tr('操作', 'Actions')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/40">
                  <tr
                    className="cursor-pointer select-none border-t border-zinc-800/60 bg-zinc-950/50 hover:bg-zinc-900/50"
                    onClick={() => setCollapsed((v) => !v)}
                  >
                    <td colSpan={4} className="px-3 py-2">
                      <div className="flex items-center gap-1.5">
                        {collapsed ? (
                          <ChevronRight size={13} className="text-zinc-500" />
                        ) : (
                          <ChevronDown size={13} className="text-zinc-500" />
                        )}
                        <span className="text-sm font-medium text-zinc-200">
                          {tr('内置工具', 'Built-in tools')}
                        </span>
                        <span className="rounded bg-emerald-900/40 px-1 text-[9px] font-medium text-emerald-300">
                          builtin
                        </span>
                        <span className="ml-1 text-[11px] text-zinc-500">
                          {filtered.length} {tr('个工具', 'tools')}
                        </span>
                      </div>
                    </td>
                  </tr>
                  {!collapsed &&
                    filtered.map((tool) => (
                      <ToolRow
                        key={tool.name}
                        tool={tool}
                        onView={() => setViewing(tool)}
                        onRun={() => setRunning(tool)}
                      />
                    ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {viewing && (
        <ToolDetailModal
          tool={viewing}
          onClose={() => setViewing(null)}
          onRun={() => {
            setRunning(viewing);
            setViewing(null);
          }}
        />
      )}
      {running && <ToolRunModal tool={running} onClose={() => setRunning(null)} />}
    </main>
  );
}

function FilterChip({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick(): void;
}) {
  return (
    <Button
      variant="outline"
      size="sm"
      type="button"
      onClick={onClick}
      className={cn(
        'rounded-md border px-2 py-0.5 text-[11px] transition-colors',
        active
          ? 'border-zinc-600 bg-zinc-800 text-zinc-100'
          : 'border-zinc-800 bg-zinc-900/50 text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-200',
      )}
    >
      {label}
    </Button>
  );
}

function RiskBadge({ value }: { value: string }) {
  const { tr } = useI18n();
  if (value === 'safe') {
    return (
      <span className="rounded-md bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300 ring-1 ring-inset ring-emerald-500/30">
        {tr('安全', 'safe')}
      </span>
    );
  }
  if (value === 'approval_required') {
    return (
      <span className="rounded-md bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-300 ring-1 ring-inset ring-amber-500/30">
        {tr('需审批', 'approval')}
      </span>
    );
  }
  throw new Error(`未知 Tool risk: ${value}`);
}

function ToolRow({
  tool,
  onView,
  onRun,
}: {
  tool: BuiltinTool;
  onView(): void;
  onRun(): void;
}) {
  const { tr } = useI18n();
  return (
    <tr className="cursor-pointer transition-colors hover:bg-zinc-900/40" onClick={onView}>
      <td className="whitespace-nowrap px-4 py-1.5 pl-8">
        <span className="text-xs text-zinc-200">{tool.name}</span>
        <Hint content={tool.name}>
          <div className="font-mono text-[10px] text-zinc-600">{tool.name}</div>
        </Hint>
      </td>
      <td className="whitespace-nowrap px-4 py-1.5">
        <RiskBadge value={tool.risk} />
      </td>
      <td className="w-full max-w-0 px-4 py-1.5 text-[11px] text-zinc-500">
        <div className="line-clamp-1">{tool.description}</div>
      </td>
      <td className="whitespace-nowrap px-4 py-1.5 text-right">
        <Button
          variant="outline"
          size="sm"
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onRun();
          }}
          className="inline-flex items-center gap-1.5 px-2.5 py-1 text-[11px]"
        >
          <Play size={11} />
          {tr('运行', 'Run')}
        </Button>
      </td>
    </tr>
  );
}

function ToolDetailModal({
  tool,
  onClose,
  onRun,
}: {
  tool: BuiltinTool;
  onClose(): void;
  onRun(): void;
}) {
  const { tr } = useI18n();
  const params = tool.params ?? [];
  return (
    <Modal open onClose={onClose} size="lg" resizable title={tool.name}>
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <RiskBadge value={tool.risk} />
          <span className="rounded-md border border-zinc-800 bg-zinc-950/40 px-1.5 py-0.5 text-[10px] text-zinc-400">
            builtin
          </span>
          <span className="ml-auto font-mono text-[11px] text-zinc-500">{tool.name}</span>
        </div>

        <section>
          <div className="mb-1.5 text-[11px] uppercase tracking-wider text-zinc-500">
            {tr('描述', 'Description')}
          </div>
          <div className="whitespace-pre-wrap rounded-md border border-zinc-800 bg-zinc-950/40 p-3 text-xs leading-relaxed text-zinc-300">
            {tool.description}
          </div>
        </section>

        <section>
          <div className="mb-1.5 text-[11px] uppercase tracking-wider text-zinc-500">
            {tr('参数', 'Parameters')}
          </div>
          {params.length === 0 ? (
            <div className="text-xs text-zinc-500">{tr('无参数', 'No parameters')}</div>
          ) : (
            <div className="overflow-hidden rounded-md border border-zinc-800">
              <table className="w-full text-xs">
                <thead className="border-b border-zinc-800 bg-zinc-950/40 text-[10px] uppercase tracking-wider text-zinc-500">
                  <tr>
                    <th className="px-3 py-1.5 text-left">{tr('名称', 'Name')}</th>
                    <th className="px-3 py-1.5 text-left">{tr('类型', 'Type')}</th>
                    <th className="px-3 py-1.5 text-left">{tr('必填', 'Required')}</th>
                    <th className="px-3 py-1.5 text-left">{tr('默认值', 'Default')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/60">
                  {params.map((p) => (
                    <tr key={p.name}>
                      <td className="whitespace-nowrap px-3 py-1.5 font-mono text-zinc-200">{p.name}</td>
                      <td className="whitespace-nowrap px-3 py-1.5 text-zinc-400">{p.type}</td>
                      <td className="whitespace-nowrap px-3 py-1.5 text-zinc-400">
                        {p.required ? tr('是', 'Yes') : '—'}
                      </td>
                      <td className="whitespace-nowrap px-3 py-1.5 font-mono text-zinc-400">
                        {p.default === undefined || p.default === null ? '—' : String(p.default)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <div className="flex items-center justify-between gap-3 border-t border-zinc-800 pt-3">
          <span className="text-[11px] text-zinc-500">
            {tr('内置 Tool 由平台代码定义，不可在线编辑', 'Built-in Tools are defined in platform code and cannot be edited online')}
          </span>
          <Button
            variant="primary"
            size="sm"
            type="button"
            onClick={onRun}
            className="inline-flex items-center gap-1.5"
          >
            <Play size={11} />
            {tr('运行', 'Run')}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function ToolRunModal({ tool, onClose }: { tool: BuiltinTool; onClose(): void }) {
  const { tr } = useI18n();
  const params = tool.params ?? [];
  const [values, setValues] = useState<Record<string, string>>(() => initialFormValues(params));
  const [busy, setBusy] = useState(false);
  const [runErr, setRunErr] = useState<string | null>(null);
  const [resultText, setResultText] = useState<string | null>(null);

  const onRun = async () => {
    setBusy(true);
    setRunErr(null);
    setResultText(null);
    try {
      const payload = buildParams(params, values, tr);
      const resp = await runBuiltinTool(tool.name, payload);
      setResultText(formatResult(resp.result));
    } catch (e) {
      setRunErr(e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      size="lg"
      title={tr(`运行 ${tool.name}`, `Run ${tool.name}`)}
      footer={
        <>
          <Button onClick={onClose} variant="ghost">
            {tr('关闭', 'Close')}
          </Button>
          <Button variant="primary" disabled={busy} onClick={() => void onRun()}>
            {busy ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
            {busy ? tr('运行中…', 'Running…') : tr('运行', 'Run')}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="rounded-md border border-zinc-800 bg-zinc-950/40 px-3 py-2">
          <div className="font-mono text-[11px] text-zinc-100">{tool.name}</div>
          {tool.description ? (
            <p className="mt-1 line-clamp-2 text-[11px] text-zinc-500">{tool.description}</p>
          ) : null}
        </div>

        {params.length === 0 ? (
          <div className="text-[12px] text-zinc-500">
            {tr('无参数，可直接运行。', 'No parameters. You can run directly.')}
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {params.map((p) => (
              <Label key={p.name} className="block min-w-0">
                <span className="mb-0.5 flex items-center gap-1 text-[11px]">
                  <span className="font-mono text-sky-300">{p.name}</span>
                  {p.required ? <span className="text-amber-400">*</span> : null}
                  <span className="font-mono text-zinc-600">{p.type}</span>
                </span>
                <Input
                  type={p.type === 'int' || p.type === 'float' ? 'number' : 'text'}
                  step={p.type === 'int' ? 1 : p.type === 'float' ? 'any' : undefined}
                  value={values[p.name] ?? ''}
                  onChange={(e) => setValues((cur) => ({ ...cur, [p.name]: e.target.value }))}
                  className="h-8 w-full font-mono text-[12px]"
                />
              </Label>
            ))}
          </div>
        )}

        {runErr && (
          <div className="rounded-md border border-red-900/50 bg-red-950/30 px-3 py-2 text-[12px] text-red-300">
            {runErr}
          </div>
        )}
        {resultText != null && (
          <div>
            <div className="mb-1.5 text-[11px] uppercase tracking-wide text-zinc-500">
              {tr('运行结果', 'Result')}
            </div>
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 font-mono text-[11px] text-zinc-200">
              {resultText}
            </pre>
          </div>
        )}
      </div>
    </Modal>
  );
}

function initialFormValues(params: BuiltinToolParam[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const p of params) {
    if (p.default === undefined || p.default === null) {
      out[p.name] = '';
    } else {
      out[p.name] = String(p.default);
    }
  }
  return out;
}

function buildParams(
  defs: BuiltinToolParam[],
  values: Record<string, string>,
  tr: (zh: string, en: string) => string,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const p of defs) {
    const raw = (values[p.name] ?? '').trim();
    if (raw === '') {
      if (p.required) {
        throw new Error(tr(`参数 ${p.name} 必填`, `Parameter ${p.name} is required`));
      }
      continue;
    }
    if (p.type === 'int') {
      const n = Number.parseInt(raw, 10);
      if (Number.isNaN(n)) {
        throw new Error(tr(`参数 ${p.name} 必须是整数`, `Parameter ${p.name} must be an integer`));
      }
      out[p.name] = n;
      continue;
    }
    if (p.type === 'float') {
      const n = Number.parseFloat(raw);
      if (Number.isNaN(n)) {
        throw new Error(tr(`参数 ${p.name} 必须是数字`, `Parameter ${p.name} must be a number`));
      }
      out[p.name] = n;
      continue;
    }
    if (p.type === 'bool') {
      if (raw !== 'true' && raw !== 'false') {
        throw new Error(tr(`参数 ${p.name} 必须是 true/false`, `Parameter ${p.name} must be true/false`));
      }
      out[p.name] = raw === 'true';
      continue;
    }
    out[p.name] = raw;
  }
  return out;
}

function formatResult(result: unknown): string {
  if (typeof result === 'string') return result;
  return JSON.stringify(result, null, 2);
}

function EmptyState({ hasFilter, onClear }: { hasFilter: boolean; onClear: () => void }) {
  const { tr } = useI18n();
  return (
    <EmptyStateCard
      icon={Hammer}
      action={hasFilter ? <Button onClick={onClear}>{tr('清除筛选', 'Clear filters')}</Button> : undefined}
      title={hasFilter ? tr('没有匹配的工具', 'No matching tools') : tr('还没有内置 Tool', 'No built-in tools yet')}
      hint={
        hasFilter
          ? tr('换个关键字或清除风险筛选', 'Try a different keyword or clear risk filters')
          : tr('后端注册自带 Tool 后会出现在这里', 'Built-in Tools appear here once the backend registers them')
      }
    />
  );
}
