// 自动化触发器：Cron / Webhook 驱动工作流或助理会话。
import { useCallback, useEffect, useState } from 'react';
import { CalendarClock, Loader2, Plus, RefreshCw, Trash2, Webhook } from 'lucide-react';
import {
  Button,
  EmptyStateCard,
  FilterBar,
  Input,
  PageHeader,
  RowAction,
  RowActionIcon,
  SearchInput,
  Select,
  StatusPill,
  TableCard,
  TableHead,
  TablePager,
  tableLayout,
  COL,
} from '@/components/ui';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { Modal } from '@/components/Modal';
import { Hint } from '@/components/ui/Tooltip';
import { errorText } from '@/lib/errors';
import { useI18n } from '@/i18n/locale';
import { usePermissions } from '@/store/me';
import { cn } from '@/lib/cn';
import { listWorkflows } from '@/api/workflows';
import {
  createTrigger,
  deleteTrigger,
  fireTrigger,
  listTriggerRuns,
  listTriggers,
  updateTrigger,
  type TriggerRuleItem,
  type TriggerRunItem,
} from '@/api/triggers';
import { listAgents, type AgentSummary } from '@/api/agents';

const PAGE_SIZE = 20;

export default function TriggersPage() {
  const { tr } = useI18n();
  const { canMutate, isAdmin } = usePermissions();
  const [items, setItems] = useState<TriggerRuleItem[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(0);
  const [editor, setEditor] = useState<null | { mode: 'create' }>(null);
  const [deleting, setDeleting] = useState<TriggerRuleItem | null>(null);
  const [runsFor, setRunsFor] = useState<TriggerRuleItem | null>(null);
  const [runs, setRuns] = useState<TriggerRunItem[] | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback(
    async (silent = false) => {
      if (!silent) setLoading(true);
      try {
        const r = await listTriggers();
        setItems(r.items);
        setErr(null);
      } catch (e) {
        setErr(errorText(e));
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = (items ?? []).filter((t) => {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return t.name.toLowerCase().includes(q);
  });
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const paged = filtered.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);

  const refreshRuns = useCallback(async (rule: TriggerRuleItem) => {
    const r = await listTriggerRuns(rule.id);
    setRuns(r.items);
  }, []);

  const onFire = async (rule: TriggerRuleItem) => {
    setBusyId(rule.id);
    try {
      await fireTrigger(rule.id);
      await load(true);
      if (runsFor?.id === rule.id) await refreshRuns(rule);
    } finally {
      setBusyId(null);
    }
  };

  const onToggle = async (rule: TriggerRuleItem) => {
    setBusyId(rule.id);
    try {
      await updateTrigger(rule.id, { enabled: !rule.enabled });
      await load(true);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tr('触发器', 'Triggers')}
        subtitle={tr(
          '定时与 Webhook 触发：让工作流和助理按计划自动运行',
          'Scheduled and webhook triggers that run workflows and assistants automatically',
        )}
        actions={
          <>
            <RowAction icon={RefreshCw} onClick={() => void load()}>
              {tr('刷新', 'Refresh')}
            </RowAction>
            {canMutate && (
              <Button variant="primary" size="sm" onClick={() => setEditor({ mode: 'create' })}>
                <Plus size={12} />
                {tr('新建触发器', 'New trigger')}
              </Button>
            )}
          </>
        }
      />
      {items !== null && items.length > 0 && (
        <FilterBar className="mx-6 mt-3">
          <SearchInput
            value={query}
            onChange={(v) => {
              setQuery(v);
              setPage(0);
            }}
            placeholder={tr('按名称搜索…', 'Search by name…')}
            ariaLabel={tr('搜索触发器', 'Search triggers')}
            className="min-w-[16rem] flex-1"
          />
          <span className="ml-auto text-[11px] tabular-nums text-zinc-500">
            {tr(`${items.length} 个 · 匹配 ${filtered.length}`, `${items.length} total · ${filtered.length} matched`)}
          </span>
        </FilterBar>
      )}

      <div className="flex min-h-0 flex-1 flex-col px-6 py-4">
        {err && (
          <div className="mb-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-[12px] text-red-300">
            {err}
          </div>
        )}
        {loading && items === null ? (
          <div className="flex flex-1 items-center justify-center text-sm text-zinc-500">
            <Loader2 size={14} className="mr-2 animate-spin" />
            {tr('加载中…', 'Loading…')}
          </div>
        ) : items !== null && items.length === 0 ? (
          <EmptyStateCard
            icon={CalendarClock}
            title={tr('还没有任何触发器', 'No triggers yet')}
            hint={tr(
              '定时巡检、告警轮询都可以交给触发器自动运行。',
              'Schedule inspections and alert polls to run on their own.',
            )}
            action={
              canMutate && (
                <Button variant="primary" onClick={() => setEditor({ mode: 'create' })}>
                  <Plus size={12} />
                  {tr('新建触发器', 'New trigger')}
                </Button>
              )
            }
          />
        ) : filtered.length === 0 ? (
          <EmptyStateCard
            icon={CalendarClock}
            title={tr('没有匹配的触发器', 'No matching triggers')}
            action={
              <Button onClick={() => setQuery('')}>{tr('清除筛选', 'Clear filters')}</Button>
            }
          />
        ) : (
          <TableCard
            footer={
              <TablePager
                page={safePage}
                pageSize={PAGE_SIZE}
                total={filtered.length}
                shown={paged.length}
                onPageChange={setPage}
              />
            }
          >
            <table {...tableLayout(64)}>
              <colgroup>
                <col className={COL.main} />
                <col className={COL.tag} />
                <col className={COL.detail} />
                <col className={COL.tag} />
                <col className={COL.time} />
                <col className={COL.actions} />
              </colgroup>
              <TableHead>
                <tr className="whitespace-nowrap">
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('名称', 'Name')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('类型', 'Kind')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('目标', 'Target')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('状态', 'Status')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('下次触发', 'Next fire')}</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">{tr('操作', 'Actions')}</th>
                </tr>
              </TableHead>
              <tbody className="divide-y divide-zinc-800/40">
                {paged.map((t) => (
                  <tr
                    key={t.id}
                    className="group cursor-pointer transition-colors hover:bg-zinc-900/40"
                    onClick={() => {
                      setRunsFor(t);
                      void refreshRuns(t);
                    }}
                  >
                    <td className="px-3 py-2">
                      <span className="block truncate text-[12.5px] font-medium text-zinc-100">
                        {t.name}
                      </span>
                      <span className="block truncate text-[10px] leading-4 text-zinc-500">
                        {t.kind === 'cron' ? `cron · ${t.cron_expr}` : 'webhook'}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-[11px] text-zinc-300">
                      {t.target_kind === 'workflow'
                        ? tr('工作流', 'Workflow')
                        : tr('助理', 'Assistant')}
                    </td>
                    <td className="px-3 py-2 text-[11px] text-zinc-300">
                      {t.target_kind === 'workflow'
                        ? `#${t.workflow_id}`
                        : t.agent_id}
                    </td>
                    <td className="px-3 py-2">
                      <StatusPill
                        tone={t.enabled ? 'ok' : 'neutral'}
                        label={t.enabled ? tr('启用', 'Enabled') : tr('已停用', 'Disabled')}
                      />
                    </td>
                    <td className="px-3 py-2 text-[11px] text-zinc-500">
                      {t.next_fire_at ? new Date(t.next_fire_at).toLocaleString() : '—'}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <div
                        className="flex items-center justify-end gap-0.5 opacity-60 transition-opacity group-hover:opacity-100"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <RowAction
                          icon={busyId === t.id ? Loader2 : RefreshCw}
                          iconClassName={busyId === t.id ? 'animate-spin' : undefined}
                          disabled={busyId === t.id}
                          onClick={() => void onFire(t)}
                        >
                          {tr('触发', 'Fire')}
                        </RowAction>
                        {canMutate && (
                          <>
                            <Hint content={tr('启停', 'Toggle')}>
                              <RowActionIcon
                                icon={Webhook}
                                label={t.enabled ? tr('停用', 'Disable') : tr('启用', 'Enable')}
                                onClick={() => void onToggle(t)}
                              />
                            </Hint>
                            {isAdmin && (
                              <Hint content={tr('删除', 'Delete')}>
                                <RowActionIcon
                                  icon={Trash2}
                                  label={tr('删除', 'Delete')}
                                  tone="danger"
                                  onClick={() => setDeleting(t)}
                                />
                              </Hint>
                            )}
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableCard>
        )}
      </div>

      {editor && (
        <TriggerEditor
          onClose={() => setEditor(null)}
          onSaved={() => {
            setEditor(null);
            void load();
          }}
        />
      )}
      {runsFor && (
        <Modal
          open
          onClose={() => setRunsFor(null)}
          title={`${tr('触发记录', 'Trigger runs')} · ${runsFor.name}`}
          size="md"
        >
          {runs === null ? (
            <div className="flex h-24 items-center justify-center text-sm text-zinc-500">
              <Loader2 size={14} className="mr-2 animate-spin" />
              {tr('加载中…', 'Loading…')}
            </div>
          ) : runs.length === 0 ? (
            <p className="py-6 text-center text-sm text-zinc-500">
              {tr('还没有触发记录', 'No runs yet')}
            </p>
          ) : (
            <ul className="space-y-2">
              {runs.map((r) => (
                <li
                  key={r.id}
                  className="flex items-center justify-between gap-2 rounded-lg border border-zinc-800/60 bg-zinc-950/40 px-3 py-2"
                >
                  <div className="min-w-0">
                    <StatusPill
                      tone={
                        r.status === 'succeeded'
                          ? 'ok'
                          : r.status === 'failed'
                            ? 'danger'
                            : r.status === 'waiting'
                              ? 'warn'
                              : 'info'
                      }
                      label={r.status}
                    />
                    <div className="mt-1 truncate text-[10px] text-zinc-500">
                      {r.started_at ? new Date(r.started_at).toLocaleString() : '—'} · attempt {r.attempt}
                      {r.ref_id ? ` · ${r.ref_id}` : ''}
                    </div>
                    {r.error && (
                      <div className="mt-1 line-clamp-2 text-[10px] text-red-400">{r.error}</div>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Modal>
      )}
      {deleting && (
        <ConfirmDialog
          open
          title={tr(`删除触发器 ${deleting.name}`, `Delete trigger ${deleting.name}`)}
          description={tr(
            '删除后定时与 Webhook 触发立即停止，历史触发记录一并清理。',
            'Scheduled and webhook fires stop immediately; past runs are cleaned up too.',
          )}
          confirmLabel={tr('删除', 'Delete')}
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            await deleteTrigger(deleting.id);
            setDeleting(null);
            await load(true);
          }}
        />
      )}
    </main>
  );
}

function TriggerEditor({
  onClose,
  onSaved,
}: {
  onClose(): void;
  onSaved(): void;
}) {
  const { tr } = useI18n();
  const { isAdmin } = usePermissions();
  const [name, setName] = useState('');
  const [kind, setKind] = useState<'cron' | 'webhook'>('cron');
  const [targetKind, setTargetKind] = useState<'workflow' | 'agent'>('workflow');
  const [cronExpr, setCronExpr] = useState('0 9 * * *');
  const [workflowId, setWorkflowId] = useState<number | null>(null);
  const [agentId, setAgentId] = useState<string>('');
  const [prompt, setPrompt] = useState('');
  const [workflows, setWorkflows] = useState<{ id: number; name: string }[]>([]);
  const [agents, setAgents] = useState<{ name: string }[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const w = await listWorkflows({ limit: 100 });
        setWorkflows(w.items.map((i) => ({ id: i.id, name: i.name })));
      } catch {
        // 工作流为空不阻塞编辑器
      }
      try {
        const a = await listAgents();
        setAgents((a as { items: AgentSummary[] }).items.map((i) => ({ name: i.name })));
      } catch {
        // 同上
      }
    })();
  }, []);

  const save = async () => {
    setSaving(true);
    setErr(null);
    try {
      const r = await createTrigger({
        name,
        kind,
        target_kind: targetKind,
        cron_expr: kind === 'cron' ? cronExpr : '',
        workflow_id: targetKind === 'workflow' ? (workflowId ?? undefined) : undefined,
        agent_id: targetKind === 'agent' ? agentId : undefined,
        prompt: targetKind === 'agent' ? prompt : '',
      });
      if (typeof r === 'object' && r !== null && 'webhook_url' in (r as object)) {
        const url = (r as { webhook_url?: string }).webhook_url ?? '';
        setErr(tr(`已创建。Webhook 地址（只显示一次）：${url}`, `Created. Webhook URL (shown once): ${url}`));
        setSaving(false);
        return;
      }
      onSaved();
    } catch (e) {
      setErr(errorText(e));
      setSaving(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={tr('新建触发器', 'New trigger')}
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {tr('取消', 'Cancel')}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => void save()}
            disabled={saving || !name || (kind === 'cron' && !cronExpr) || (targetKind === 'workflow' && !workflowId) || (targetKind === 'agent' && !agentId)}
          >
            {tr('保存', 'Save')}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <label className="block">
          <span className="mb-1 block text-[11px] text-zinc-400">{tr('名称', 'Name')}</span>
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="block">
          <span className="mb-1 block text-[11px] text-zinc-400">{tr('触发方式', 'Kind')}</span>
          <Select
            value={kind}
            onValueChange={(v) => setKind(v as 'cron' | 'webhook')}
            options={[
              { value: 'cron', label: tr('定时 Cron', 'Cron schedule') },
              { value: 'webhook', label: tr('Webhook 调用', 'Webhook call') },
            ]}
          />
        </label>
        {kind === 'cron' && (
          <label className="block">
            <span className="mb-1 block text-[11px] text-zinc-400">
              {tr('Cron 表达式（五段）', 'Cron expression (5 fields)')}
            </span>
            <Input value={cronExpr} onChange={(e) => setCronExpr(e.target.value)} />
          </label>
        )}
        <label className="block">
          <span className="mb-1 block text-[11px] text-zinc-400">{tr('目标类型', 'Target')}</span>
          <Select
            value={targetKind}
            onValueChange={(v) => setTargetKind(v as 'workflow' | 'agent')}
            options={[
              { value: 'workflow', label: tr('运行工作流', 'Run workflow') },
              { value: 'agent', label: tr('发起助理会话', 'Start assistant session') },
            ]}
          />
        </label>
        {targetKind === 'workflow' ? (
          <label className="block">
            <span className="mb-1 block text-[11px] text-zinc-400">{tr('工作流', 'Workflow')}</span>
            <Select
              value={workflowId === null ? '' : String(workflowId)}
              onValueChange={(v) => setWorkflowId(Number(v))}
              options={workflows.map((w) => ({ value: String(w.id), label: w.name }))}
            />
            <span className="mt-1 block text-[10px] text-zinc-500">
              {tr('触发时运行该工作流的已发布版本。', 'The published version runs on fire.')}
            </span>
          </label>
        ) : (
          <>
            <label className="block">
              <span className="mb-1 block text-[11px] text-zinc-400">{tr('助理', 'Assistant')}</span>
              <Select
                value={agentId}
                onValueChange={setAgentId}
                options={agents.map((a) => ({ value: a.name, label: a.name }))}
              />
              <span className="mt-1 block text-[10px] text-zinc-500">
                {tr(
                  '建议绑定只读工具的助理：无人值守时审批会中止回合。',
                  'Prefer read-only assistants: approval waits stop the turn.',
                )}
              </span>
            </label>
            <label className="block">
              <span className="mb-1 block text-[11px] text-zinc-400">
                {tr('触发时的用户消息', 'Prompt on fire')}
              </span>
              <Input value={prompt} onChange={(e) => setPrompt(e.target.value)} />
            </label>
          </>
        )}
        {isAdmin && kind === 'webhook' && (
          <p className="text-[10px] text-zinc-500">
            {tr(
              'Webhook URL 在保存后只显示一次，请立即复制保存。',
              'The webhook URL is shown once after saving — copy it now.',
            )}
          </p>
        )}
        {err && <p className="break-all text-[11px] text-red-400">{err}</p>}
        {cn('') && null}
      </div>
    </Modal>
  );
}
