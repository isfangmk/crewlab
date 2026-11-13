import { useAssistantPage } from '@/lib/assistantPage';
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Copy, Hammer, Loader2, Play, RefreshCw, ShieldAlert, ShieldCheck, Wrench } from 'lucide-react';
import { Select } from '@/components/ui/Select';
import { useRequestSeq } from '@/lib/requestSeq';
import { cn } from '@/lib/cn';
import {
  listBuiltinTools,
  runBuiltinTool,
  type BuiltinTool,
  type BuiltinToolGroup,
  type BuiltinToolParam,
} from '@/api/tools';
import { ApiError } from '@/api/client';
import { Modal } from '@/components/Modal';
import {
  Button,
  ChoiceChip,
  CodeBlock,
  DetailSection,
  EmptyStateCard,
  FilterBar,
  Input,
  Label,
  MetricCard,
  PageHeader,
  RowAction,
  SearchInput,
  StatusPill,
  ErrorBanner,
  TableCard,
  TableHead,
} from '@/components/ui';
import { useI18n } from '@/i18n/locale';
import { errorText } from '@/lib/errors';

type RiskFilter = '' | 'safe' | 'approval_required';

/** 内置 Tool 目录页：版式对齐技能目录（筛选条 + 分组表 + 详情 / 运行弹窗）。 */
export default function ToolsPage() {
  const { tr } = useI18n();
  const [items, setItems] = useState<BuiltinTool[]>([]);
  const [groups, setGroups] = useState<BuiltinToolGroup[]>([]);
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(true);
  // 目录只在挂载与手动刷新时拉取：路数守卫挡住重挂载前的旧请求
  const reqSeq = useRequestSeq();
  const [refreshing, setRefreshing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [risk, setRisk] = useState<RiskFilter>('');
  const [viewing, setViewing] = useState<BuiltinTool | null>(null);
  const [running, setRunning] = useState<BuiltinTool | null>(null);

  const fetchTools = useCallback(async (silent = false) => {
    const current = reqSeq.next();
    if (silent) setRefreshing(true);
    else setLoading(true);
    try {
      const res = await listBuiltinTools();
      if (!Array.isArray(res.items)) {
        throw new Error('内置 Tool 列表缺少 items');
      }
      if (!Array.isArray(res.groups)) {
        throw new Error('内置 Tool 列表缺少 groups');
      }
      const groupKeys = new Set<string>();
      for (const group of res.groups) {
        if (!group.key || !group.title || !group.title_en) {
          throw new Error('内置 Tool 分组缺少 key / title / title_en');
        }
        if (groupKeys.has(group.key)) {
          throw new Error(`内置 Tool 分组 key 重复: ${group.key}`);
        }
        groupKeys.add(group.key);
      }
      for (const tool of res.items) {
        if (!groupKeys.has(tool.group)) {
          throw new Error(`内置 Tool 分组不存在: ${tool.group}`);
        }
      }
      if (reqSeq.isStale(current)) return;
      setItems(res.items);
      setGroups(res.groups);
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

  const filteredGroups = useMemo(
    () =>
      groups
        .map((group) => ({
          ...group,
          items: filtered.filter((tool) => tool.group === group.key),
        }))
        .filter((group) => group.items.length > 0),
    [groups, filtered],
  );

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
        actions={
          <RowAction
            icon={RefreshCw}
            iconClassName={cn(refreshing && 'animate-spin')}
            disabled={loading || refreshing}
            onClick={() => void fetchTools(true)}
          >
            {tr('刷新', 'Refresh')}
          </RowAction>
        }
      />

      <div className="flex flex-1 flex-col overflow-hidden">
        <FilterBar className="mx-6 mt-3">
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder={tr('搜索 name / description', 'Search name / description')}
            ariaLabel={tr('搜索', 'Search')}
            className="min-w-[16rem] flex-1"
          />
          <span className="pl-1 text-[11px] text-zinc-500">{tr('风险', 'Risk')}</span>
          <ChoiceChip
            active={risk === ''}
            label={tr('全部', 'All')}
            count={counts.total}
            onClick={() => setRisk('')}
          />
          <ChoiceChip
            active={risk === 'safe'}
            label={tr('安全', 'Safe')}
            count={counts.safe}
            onClick={() => setRisk('safe')}
          />
          <ChoiceChip
            active={risk === 'approval_required'}
            label={tr('需审批', 'Approval')}
            count={counts.approval}
            onClick={() => setRisk('approval_required')}
          />
          <div className="ml-auto flex items-center gap-2">
            <span className="text-[11px] tabular-nums text-zinc-500">
              {tr(`${items.length} 个 · 匹配 ${filtered.length}`, `${items.length} total · ${filtered.length} matched`)}
            </span>
          </div>
        </FilterBar>

        {/* 概览：把「多少工具需要审批」变成一屏可见，而不是靠筛选项去数 */}
        <div className="mx-6 mt-3 grid shrink-0 grid-cols-3 gap-2">
          <MetricCard
            icon={Wrench}
            label={tr('工具总数', 'Tools')}
            value={String(counts.total)}
            hint={tr('平台内置能力', 'Built-in capabilities')}
          />
          <MetricCard
            icon={ShieldCheck}
            tone="ok"
            label={tr('安全', 'Safe')}
            value={String(counts.safe)}
            hint={tr('无需审批，直接执行', 'Run without approval')}
          />
          <MetricCard
            icon={ShieldAlert}
            tone={counts.approval > 0 ? 'warn' : 'default'}
            label={tr('需审批', 'Needs approval')}
            value={String(counts.approval)}
            hint={
              counts.total === 0
                ? undefined
                : tr(`占比 ${Math.round((counts.approval / counts.total) * 100)}%`, `${Math.round((counts.approval / counts.total) * 100)}% of tools`)
            }
            bar={counts.total === 0 ? undefined : (counts.approval / counts.total) * 100}
          />
        </div>

        <div className="flex min-h-0 flex-1 flex-col px-6 py-4">
          {err && (
            <ErrorBanner>
              {tr('加载失败：', 'Load failed: ')}
              {err}
            </ErrorBanner>
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
            <TableCard className="min-h-0 flex-1">
              <table className="og-action-table w-full table-fixed text-sm">
                <colgroup>
                  <col />
                  <col className="w-[7rem]" />
                  <col className="w-[8.5rem]" />
                  <col className="w-[7.5rem]" />
                </colgroup>
                <TableHead>
                  <tr className="whitespace-nowrap">
                    <th className="w-full px-3 py-2 text-left font-medium">{tr('工具 / 说明', 'Tool / description')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('风险', 'Risk')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('参数', 'Params')}</th>
                    <th className="px-3 py-2 text-right font-medium">{tr('操作', 'Actions')}</th>
                  </tr>
                </TableHead>
                <tbody className="divide-y divide-zinc-800/40">
                  {filteredGroups.map((group) => {
                    const isCollapsed = collapsedGroups[group.key] === true;
                    return (
                      <Fragment key={group.key}>
                        <tr
                          className="cursor-pointer select-none border-t border-zinc-800/60 bg-zinc-950/50 hover:bg-zinc-900/50"
                          onClick={() =>
                            setCollapsedGroups((prev) => ({
                              ...prev,
                              [group.key]: !isCollapsed,
                            }))
                          }
                        >
                          <td colSpan={4} className="px-3 py-2">
                            <div className="flex items-center gap-1.5">
                              {isCollapsed ? (
                                <ChevronRight size={13} className="text-zinc-500" />
                              ) : (
                                <ChevronDown size={13} className="text-zinc-500" />
                              )}
                              <span className="text-[12.5px] font-medium text-zinc-200">
                                {tr(group.title, group.title_en)}
                              </span>
                              <span className="ml-1 text-[11px] text-zinc-500">
                                {group.items.length} {tr('个工具', 'tools')}
                              </span>
                            </div>
                          </td>
                        </tr>
                        {!isCollapsed &&
                          group.items.map((tool) => (
                            <ToolRow
                              key={tool.name}
                              tool={tool}
                              onView={() => setViewing(tool)}
                              onRun={() => setRunning(tool)}
                            />
                          ))}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </TableCard>
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

function RiskBadge({ value }: { value: string }) {
  const { tr } = useI18n();
  if (value === 'safe') {
    return <StatusPill tone="ok" label={tr('安全', 'safe')} />;
  }
  if (value === 'approval_required') {
    return <StatusPill tone="warn" label={tr('需审批', 'approval')} />;
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
  const required = tool.params.filter((p) => p.required).length;
  return (
    <tr className="cursor-pointer transition-colors hover:bg-zinc-900/40" onClick={onView}>
      <td className="min-w-0 px-3 py-2 pl-8">
        <div className="flex min-w-0 items-baseline gap-2">
          <span className="shrink-0 font-mono text-[12.5px] font-medium text-zinc-100">{tool.name}</span>
        </div>
        <div className="mt-0.5 line-clamp-1 text-[11px] text-zinc-500" title={tool.description}>
          {tool.description}
        </div>
      </td>
      <td className="whitespace-nowrap px-3 py-2">
        <RiskBadge value={tool.risk} />
      </td>
      <td className="whitespace-nowrap px-3 py-2 text-[11px] text-zinc-400">
        {tool.params.length === 0 ? (
          <span className="text-zinc-600">{tr('无参数', 'No params')}</span>
        ) : (
          <span className="tabular-nums">
            {tr(`${tool.params.length} 个参数`, `${tool.params.length} params`)}
            {required > 0 ? <span className="text-zinc-600"> · {tr(`${required} 必填`, `${required} required`)}</span> : null}
          </span>
        )}
      </td>
      <td className="whitespace-nowrap px-3 py-2 text-right">
        <RowAction
          icon={Play}
          onClick={(e) => {
            e.stopPropagation();
            onRun();
          }}
        >
          {tr('运行', 'Run')}
        </RowAction>
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

        <DetailSection title={tr('描述', 'Description')}>
          <div className="whitespace-pre-wrap rounded-lg border border-zinc-800/60 bg-zinc-900/30 p-3 text-[12px] leading-relaxed text-zinc-300">
            {tool.description}
          </div>
        </DetailSection>

        <DetailSection title={tr('参数', 'Parameters')}>
          {params.length === 0 ? (
            <div className="text-[12px] text-zinc-500">{tr('无参数', 'No parameters')}</div>
          ) : (
            <div className="overflow-hidden rounded-lg border border-zinc-800/60">
              <table className="og-action-table w-full text-[12px]">
                <thead className="border-b border-zinc-800/60 text-[11px] text-zinc-500">
                  <tr className="whitespace-nowrap">
                    <th className="px-3 py-2 text-left font-medium">{tr('名称', 'Name')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('类型', 'Type')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('必填', 'Required')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('默认值', 'Default')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/60">
                  {params.map((p) => (
                    <tr key={p.name}>
                      <td className="whitespace-nowrap px-3 py-2 font-mono text-zinc-200">{p.name}</td>
                      <td className="whitespace-nowrap px-3 py-2 text-zinc-400">{p.type}</td>
                      <td className="whitespace-nowrap px-3 py-2 text-zinc-400">
                        {p.required ? tr('是', 'Yes') : '—'}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 font-mono text-zinc-400">
                        {p.default === undefined || p.default === null ? '—' : String(p.default)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </DetailSection>

        <div className="flex items-center justify-between gap-3 border-t border-zinc-800/60 pt-3">
          <span className="text-[11px] text-zinc-500">
            {tr('内置 Tool 由平台代码定义，不可在线编辑', 'Built-in Tools are defined in platform code and cannot be edited online')}
          </span>
          <Button
            variant="primary"
            size="sm"
            type="button"
            onClick={onRun}
          >
            <Play size={12} />
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
      setRunErr(e instanceof ApiError ? e.message : errorText(e));
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
        <div className="rounded-lg border border-zinc-800/60 bg-zinc-900/30 px-3 py-2">
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
                  {p.desc ? <span className="truncate text-zinc-500">{p.desc}</span> : null}
                </span>
                {p.type === 'bool' ? (
                  <Select
                    label={p.name}
                    value={values[p.name] ?? ''}
                    onValueChange={(v) => setValues((cur) => ({ ...cur, [p.name]: v }))}
                    className="h-8 w-full font-mono text-[12px]"
                  >
                    <option value="">{tr('（选择）', '(select)')}</option>
                    <option value="true">true</option>
                    <option value="false">false</option>
                  </Select>
                ) : p.type === 'enum' && Array.isArray(p.enum) ? (
                  <Select
                    label={p.name}
                    value={values[p.name] ?? ''}
                    onValueChange={(v) => setValues((cur) => ({ ...cur, [p.name]: v }))}
                    className="h-8 w-full font-mono text-[12px]"
                  >
                    <option value="">{tr('（选择）', '(select)')}</option>
                    {p.enum.map((opt) => (
                      <option key={opt} value={opt}>{opt}</option>
                    ))}
                  </Select>
                ) : (
                  <Input
                    type={p.type === 'int' || p.type === 'float' ? 'number' : 'text'}
                    step={p.type === 'int' ? 1 : p.type === 'float' ? 'any' : undefined}
                    value={values[p.name] ?? ''}
                    onChange={(e) => setValues((cur) => ({ ...cur, [p.name]: e.target.value }))}
                    className="h-8 w-full font-mono text-[12px]"
                  />
                )}
              </Label>
            ))}
          </div>
        )}

        {runErr && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-[12px] text-red-300">
            {runErr}
          </div>
        )}
        {resultText != null && (
          <div>
            <div className="mb-1.5 flex items-center justify-between text-[11px] uppercase tracking-wide text-zinc-500">
              <span>{tr('运行结果', 'Result')}</span>
              <Button
                variant="ghost"
                size="sm"
                type="button"
                className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[11px] normal-case"
                onClick={() => void navigator.clipboard.writeText(resultText)}
              >
                <Copy size={11} />
                {tr('复制', 'Copy')}
              </Button>
            </div>
            <CodeBlock maxHeightClassName="max-h-72">{resultText}</CodeBlock>
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
