// 自动化触发器：Cron / Webhook 驱动工作流或助理会话。
import { useCallback, useEffect, useState } from 'react';
import { CalendarClock, Check, Copy, Loader2, Plus, Power, RefreshCw, Trash2 } from 'lucide-react';
import {
  Button,
  Chip,
  CodeBlock,
  EmptyStateCard,
  ErrorBanner,
  FilterBar,
  Input,
  Label,
  PageHeader,
  RowAction,
  RowActionIcon,
  SearchInput,
  Select,
  StatusPill,
  TableCard,
  TableHead,
  TablePager,
  Textarea,
  Toast,
  type ToastTone,
  tableLayout,
  COL,
} from '@/components/ui';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { Modal } from '@/components/Modal';
import { Hint } from '@/components/ui/Tooltip';
import { errorText } from '@/lib/errors';
import { fullDateTime } from '@/lib/format';
import { useTimeout } from '@/lib/useTimeout';
import { useI18n } from '@/i18n/locale';
import { usePermissions } from '@/store/me';
import { getWorkflow, listWorkflows, type WorkflowVariable } from '@/api/workflows';
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

const DEFAULT_PAGE_SIZE = 20;
const PAGE_SIZE_OPTIONS = [20, 50, 100];

const RUN_STATUS_LABELS: Record<
  TriggerRunItem['status'],
  { zh: string; en: string; tone: 'ok' | 'danger' | 'warn' | 'info' }
> = {
  running: { zh: '进行中', en: 'Running', tone: 'info' },
  waiting: { zh: '等待审批', en: 'Waiting', tone: 'warn' },
  succeeded: { zh: '成功', en: 'Succeeded', tone: 'ok' },
  failed: { zh: '失败', en: 'Failed', tone: 'danger' },
};

export default function TriggersPage() {
  const { tr } = useI18n();
  const { canMutate, isAdmin } = usePermissions();
  const [items, setItems] = useState<TriggerRuleItem[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [editor, setEditor] = useState<null | { mode: 'create' }>(null);
  const [deleting, setDeleting] = useState<TriggerRuleItem | null>(null);
  const [runsFor, setRunsFor] = useState<TriggerRuleItem | null>(null);
  const [runs, setRuns] = useState<TriggerRunItem[] | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [workflowNames, setWorkflowNames] = useState<Record<number, string>>({});
  const [revealedWebhook, setRevealedWebhook] = useState<{ name: string; url: string } | null>(null);
  const [toast, setToast] = useState<{ tone: ToastTone; text: string } | null>(null);

  const flashTimer = useTimeout();
  const flash = useCallback(
    (tone: ToastTone, text: string) => {
      setToast({ tone, text });
      flashTimer(() => setToast(null), 4000);
    },
    [flashTimer],
  );

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

  useEffect(() => {
    void listWorkflows({ limit: 100 })
      .then((result) => {
        setWorkflowNames(
          Object.fromEntries(result.items.map((item) => [item.id, item.name])),
        );
      })
      .catch(() => {
        // 名称解析失败时保留 #id 展示，不阻塞触发器列表
      });
  }, []);

  const filtered = (items ?? []).filter((t) => {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return t.name.toLowerCase().includes(q);
  });
  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, pageCount - 1);
  const paged = filtered.slice(safePage * pageSize, (safePage + 1) * pageSize);

  const refreshRuns = useCallback(async (rule: TriggerRuleItem) => {
    const r = await listTriggerRuns(rule.id);
    setRuns(r.items);
  }, []);

  const onFire = async (rule: TriggerRuleItem) => {
    setBusyId(rule.id);
    try {
      const run = await fireTrigger(rule.id);
      await load(true);
      if (runsFor?.id === rule.id) await refreshRuns(rule);
      if (run.status === 'failed') {
        flash('err', tr('触发失败', 'Trigger failed'));
      } else {
        flash('ok', tr('已触发', 'Triggered'));
      }
    } catch (e) {
      flash('err', errorText(e));
    } finally {
      setBusyId(null);
    }
  };

  const onToggle = async (rule: TriggerRuleItem) => {
    setBusyId(rule.id);
    const nextEnabled = !rule.enabled;
    try {
      await updateTrigger(rule.id, { enabled: nextEnabled });
      await load(true);
      flash('ok', nextEnabled ? tr('已启用', 'Enabled') : tr('已停用', 'Disabled'));
    } catch (e) {
      flash('err', errorText(e));
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
      <FilterBar className="mx-6 mt-3">
        <SearchInput
          value={query}
          onChange={(v) => {
            setQuery(v);
            setPage(0);
          }}
          placeholder={tr('按名称搜索…', 'Search by name…')}
          ariaLabel={tr('搜索触发器', 'Search triggers')}
        />
        <span className="ml-auto text-[11px] tabular-nums text-zinc-500">
          {tr(`共 ${filtered.length} 条`, `${filtered.length} total`)}
        </span>
      </FilterBar>

      <div className="flex min-h-0 flex-1 flex-col px-6 py-4">
        {err && (
          <ErrorBanner className="mb-3">{err}</ErrorBanner>
        )}
        {loading && items === null ? (
          <div className="flex flex-1 items-center justify-center text-[11px] text-zinc-500">
            <Loader2 size={12} className="mr-2 animate-spin" />
            {tr('加载中…', 'Loading…')}
          </div>
        ) : err !== null && items !== null && items.length === 0 ? null : items !== null && items.length === 0 ? (
          <EmptyStateCard
            icon={CalendarClock}
            title={tr('还没有任何触发器', 'No triggers yet')}
            hint={tr(
              '定时巡检、告警轮询都可以交给触发器自动运行。',
              'Schedule inspections and alert polls to run on their own.',
            )}
            action={
              canMutate && (
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => setEditor({ mode: 'create' })}
                >
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
              <Button variant="outline" size="sm" onClick={() => setQuery('')}>{tr('清除筛选', 'Clear filters')}</Button>
            }
          />
        ) : (
          <TableCard
            className="min-h-0 flex-1"
            footer={
              <TablePager
                page={safePage}
                pageSize={pageSize}
                total={filtered.length}
                shown={paged.length}
                onPageChange={setPage}
                pageSizeOptions={PAGE_SIZE_OPTIONS}
                onPageSizeChange={(size) => {
                  setPage(0);
                  setPageSize(size);
                }}
              />
            }
          >
            <table {...tableLayout(62)}>
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
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('类型', 'Type')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('计划', 'Schedule')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('状态', 'Status')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('最近运行', 'Last run')}</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">{tr('操作', 'Actions')}</th>
                </tr>
              </TableHead>
              <tbody className="divide-y divide-zinc-800/40">
                {paged.map((t) => (
                  <tr
                    key={t.id}
                    className="group transition-colors hover:bg-zinc-900/40"
                  >
                    <td className="px-3 py-2">
                      <button
                        type="button"
                        title={t.name}
                        onClick={() => {
                          setRunsFor(t);
                          void refreshRuns(t);
                        }}
                        className="block max-w-full truncate text-left text-[12.5px] font-medium text-zinc-100 hover:text-indigo-300"
                      >
                        {t.name}
                      </button>
                      <span
                        className="block truncate text-[10px] leading-4 text-zinc-500"
                        title={
                          t.target_kind === 'workflow'
                            ? workflowNames[t.workflow_id ?? -1] ?? `#${t.workflow_id}`
                            : t.agent_id ?? ''
                        }
                      >
                        {t.target_kind === 'workflow'
                          ? `${tr('工作流', 'Workflow')} · ${
                              workflowNames[t.workflow_id ?? -1] ?? `#${t.workflow_id}`
                            }`
                          : `${tr('助理', 'Assistant')} · ${t.agent_id}`}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <Chip>{t.kind === 'cron' ? tr('定时', 'Cron') : 'Webhook'}</Chip>
                    </td>
                    <td className="px-3 py-2">
                      {t.kind === 'cron' ? (
                        <>
                          <span
                            className="block truncate font-sans text-[10.5px] text-zinc-300"
                            title={t.cron_expr}
                          >
                            {t.cron_expr}
                          </span>
                          <span className="block text-[10px] text-zinc-500">
                            {t.enabled && t.next_fire_at
                              ? tr(
                                  `下次 ${fullDateTime(t.next_fire_at)}`,
                                  `Next ${fullDateTime(t.next_fire_at)}`,
                                )
                              : tr('未排期', 'Not scheduled')}
                          </span>
                        </>
                      ) : (
                        <>
                          <span className="block text-[11px] text-zinc-300">
                            {tr('按调用', 'On call')}
                          </span>
                          <span className="block text-[10px] text-zinc-500">POST webhook</span>
                        </>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <StatusPill
                        tone={t.enabled ? 'ok' : 'neutral'}
                        label={t.enabled ? tr('启用', 'Enabled') : tr('已停用', 'Disabled')}
                      />
                    </td>
                    <td className="px-3 py-2 text-[11px] text-zinc-500">
                      {t.last_fired_at ? fullDateTime(t.last_fired_at) : '—'}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <div className="flex items-center justify-end gap-1.5">
                        {canMutate ? (
                          <>
                            <RowAction
                              icon={busyId === t.id ? Loader2 : RefreshCw}
                              iconClassName={busyId === t.id ? 'animate-spin' : undefined}
                              disabled={busyId === t.id}
                              onClick={() => void onFire(t)}
                            >
                              {tr('触发', 'Fire')}
                            </RowAction>
                            <Hint content={tr('启停', 'Toggle')}>
                              <RowActionIcon
                                icon={Power}
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
                        ) : (
                          <span className="text-[10px] text-zinc-600">{tr('只读', 'Read only')}</span>
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
          onSaved={(created) => {
            setEditor(null);
            void load();
            flash('ok', tr('触发器已创建', 'Trigger created'));
            if (created.webhookUrl !== null) {
              setRevealedWebhook({ name: created.name, url: created.webhookUrl });
            }
          }}
        />
      )}
      {revealedWebhook && (
        <WebhookRevealModal
          name={revealedWebhook.name}
          url={revealedWebhook.url}
          onCopied={() => flash('ok', tr('Webhook 地址已复制', 'Webhook URL copied'))}
          onClose={() => setRevealedWebhook(null)}
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
            <div className="flex h-24 items-center justify-center text-[11px] text-zinc-500">
              <Loader2 size={12} className="mr-2 animate-spin" />
              {tr('加载中…', 'Loading…')}
            </div>
          ) : runs.length === 0 ? (
            <p className="py-6 text-center text-[11.5px] text-zinc-500">
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
                      tone={RUN_STATUS_LABELS[r.status].tone}
                      label={tr(
                        RUN_STATUS_LABELS[r.status].zh,
                        RUN_STATUS_LABELS[r.status].en,
                      )}
                    />
                    <div className="mt-1 truncate text-[10px] text-zinc-500">
                      {r.started_at ? fullDateTime(r.started_at) : '—'} ·{' '}
                      {tr(`第 ${r.attempt} 次`, `attempt ${r.attempt}`)}
                      {r.ref_id ? ` · ${r.ref_id}` : ''}
                    </div>
                    {r.error && (
                      <div className="mt-1 line-clamp-2 text-[10px] text-danger">{r.error}</div>
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
            flash('ok', tr('触发器已删除', 'Trigger deleted'));
          }}
        />
      )}
      {toast !== null && <Toast tone={toast.tone} text={toast.text} />}
    </main>
  );
}

function TriggerEditor({
  onClose,
  onSaved,
}: {
  onClose(): void;
  onSaved(created: { name: string; webhookUrl: string | null }): void;
}) {
  const { tr } = useI18n();
  const [name, setName] = useState('');
  const [kind, setKind] = useState<'cron' | 'webhook'>('cron');
  const [targetKind, setTargetKind] = useState<'workflow' | 'agent'>('workflow');
  const [cronExpr, setCronExpr] = useState('0 9 * * *');
  const [workflowId, setWorkflowId] = useState<number | null>(null);
  const [agentId, setAgentId] = useState<string>('');
  const [prompt, setPrompt] = useState('');
  const [workflows, setWorkflows] = useState<
    { id: number; name: string; hasPublished: boolean }[]
  >([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  // 工作流触发的输入变量：按已发布快照的开始节点声明，而不是当前草稿
  const [vars, setVars] = useState<WorkflowVariable[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [varsLoading, setVarsLoading] = useState(false);
  const [varsErr, setVarsErr] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const w = await listWorkflows({ limit: 100 });
        setWorkflows(
          w.items.map((i) => ({
            id: i.id,
            name: i.name,
            hasPublished: i.has_published,
          })),
        );
      } catch {
        // 工作流为空不阻塞编辑器
      }
      try {
        const a = await listAgents();
        setAgents(a.items);
      } catch {
        // 同上
      }
    })();
  }, []);

  useEffect(() => {
    if (targetKind !== 'workflow' || workflowId === null) {
      setVars([]);
      setValues({});
      setVarsErr(null);
      setVarsLoading(false);
      return;
    }
    let cancelled = false;
    setVarsLoading(true);
    setVarsErr(null);
    void getWorkflow(workflowId, 'published')
      .then((wf) => {
        if (cancelled) return;
        const start = wf.definition.nodes.find((n) => n.kind === 'start');
        const declared = start?.variables ?? [];
        setVars(declared);
        setValues(
          Object.fromEntries(
            declared.map((v) => [
              v.name,
              v.default === null || v.default === undefined
                ? ''
                : v.type === 'json'
                  ? JSON.stringify(v.default)
                  : String(v.default),
            ]),
          ),
        );
      })
      .catch((e) => {
        if (cancelled) return;
        setVars([]);
        setValues({});
        setVarsErr(errorText(e));
      })
      .finally(() => {
        if (!cancelled) setVarsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [targetKind, workflowId]);

  /** 把表单值按声明类型转换后序列化成 values_json；非法输入显式报错。 */
  const buildValuesJson = (): { json: string } | { error: string } => {
    const out: Record<string, unknown> = {};
    for (const variable of vars) {
      const raw = (values[variable.name] ?? '').trim();
      if (raw === '') {
        if (variable.required) {
          return {
            error: tr(
              `变量 ${variable.name} 为必填`,
              `Variable ${variable.name} is required`,
            ),
          };
        }
        // 留空表示不注入，运行时回退到工作流声明的默认值
        continue;
      }
      if (variable.type === 'number') {
        const num = Number(raw);
        if (!Number.isFinite(num)) {
          return {
            error: tr(
              `变量 ${variable.name} 必须是数字`,
              `Variable ${variable.name} must be a number`,
            ),
          };
        }
        out[variable.name] = num;
      } else if (variable.type === 'boolean') {
        if (raw !== 'true' && raw !== 'false') {
          return {
            error: tr(
              `变量 ${variable.name} 必须是 true 或 false`,
              `Variable ${variable.name} must be true or false`,
            ),
          };
        }
        out[variable.name] = raw === 'true';
      } else if (variable.type === 'json') {
        try {
          out[variable.name] = JSON.parse(raw);
        } catch {
          return {
            error: tr(
              `变量 ${variable.name} 不是合法 JSON`,
              `Variable ${variable.name} is not valid JSON`,
            ),
          };
        }
      } else {
        out[variable.name] = raw;
      }
    }
    return { json: JSON.stringify(out) };
  };

  const missingRequired = vars.some(
    (variable) => variable.required && (values[variable.name] ?? '').trim() === '',
  );
  // 触发器只跑已发布快照：未发布的工作流选了也会在建触发器时被后端拒绝
  const publishedWorkflows = workflows.filter((workflow) => workflow.hasPublished);

  const save = async () => {
    setSaving(true);
    setErr(null);
    const built = buildValuesJson();
    if ('error' in built) {
      setErr(built.error);
      setSaving(false);
      return;
    }
    try {
      const created = await createTrigger({
        name: name.trim(),
        kind,
        target_kind: targetKind,
        cron_expr: kind === 'cron' ? cronExpr : '',
        workflow_id: targetKind === 'workflow' ? (workflowId ?? undefined) : undefined,
        agent_id: targetKind === 'agent' ? agentId : undefined,
        prompt: targetKind === 'agent' ? prompt : '',
        values_json: targetKind === 'workflow' ? built.json : '{}',
      });
      const rawUrl =
        typeof created.webhook_url === 'string' ? created.webhook_url : '';
      onSaved({
        name: name.trim(),
        webhookUrl:
          rawUrl === '' ? null : `${window.location.origin}${rawUrl}`,
      });
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
          <Button variant="ghost" size="sm" onClick={onClose}>
            {tr('取消', 'Cancel')}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => void save()}
            disabled={
              saving ||
              name.trim() === '' ||
              (kind === 'cron' && cronExpr.trim() === '') ||
              (targetKind === 'workflow' &&
                (workflowId === null ||
                  varsLoading ||
                  varsErr !== null ||
                  missingRequired)) ||
              (targetKind === 'agent' && agentId === '')
            }
          >
            {saving ? tr('保存中…', 'Saving…') : tr('保存', 'Save')}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {err && <ErrorBanner>{err}</ErrorBanner>}
        <div className="space-y-1.5">
          <Label htmlFor="trigger-name">{tr('名称', 'Name')}</Label>
          <Input
            id="trigger-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={128}
            autoFocus
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="trigger-kind">{tr('触发方式', 'Kind')}</Label>
          <Select
            id="trigger-kind"
            aria-label={tr('触发方式', 'Kind')}
            value={kind}
            onValueChange={(v) => setKind(v as 'cron' | 'webhook')}
            options={[
              { value: 'cron', label: tr('定时 Cron', 'Cron schedule') },
              { value: 'webhook', label: tr('Webhook 调用', 'Webhook call') },
            ]}
          />
        </div>
        {kind === 'cron' && (
          <div className="space-y-1.5">
            <Label htmlFor="trigger-cron">
              {tr('Cron 表达式（五段）', 'Cron expression (5 fields)')}
            </Label>
            <Input
              id="trigger-cron"
              value={cronExpr}
              onChange={(e) => setCronExpr(e.target.value)}
            />
          </div>
        )}
        <div className="space-y-1.5">
          <Label htmlFor="trigger-target-kind">{tr('目标类型', 'Target type')}</Label>
          <Select
            id="trigger-target-kind"
            aria-label={tr('目标类型', 'Target type')}
            value={targetKind}
            onValueChange={(v) => setTargetKind(v as 'workflow' | 'agent')}
            options={[
              { value: 'workflow', label: tr('运行工作流', 'Run workflow') },
              { value: 'agent', label: tr('发起助理会话', 'Start assistant session') },
            ]}
          />
        </div>
        {targetKind === 'workflow' ? (
          <>
            <div className="space-y-1.5">
              <Label htmlFor="trigger-workflow">{tr('工作流', 'Workflow')}</Label>
              <Select
                id="trigger-workflow"
                aria-label={tr('工作流', 'Workflow')}
                value={workflowId === null ? '' : String(workflowId)}
                onValueChange={(v) => setWorkflowId(Number(v))}
                options={publishedWorkflows.map((w) => ({
                  value: String(w.id),
                  label: w.name,
                }))}
              />
              <p className="text-[11px] text-zinc-500">
                {publishedWorkflows.length === 0
                  ? tr(
                      '没有已发布的工作流；请先在工作流页发布后再创建触发器。',
                      'No published workflow yet; publish one on the workflows page first.',
                    )
                  : tr(
                      '触发时运行该工作流的已发布版本。',
                      'The published version runs on fire.',
                    )}
              </p>
            </div>
            {workflowId !== null && (
              <div className="space-y-2 rounded-lg border border-zinc-800/60 bg-zinc-950/60 p-3">
                <div className="flex items-center gap-1.5">
                  <div className="og-label">{tr('输入变量', 'Input variables')}</div>
                  {varsLoading && (
                    <Loader2 size={11} className="animate-spin text-zinc-500" />
                  )}
                </div>
                {varsErr !== null ? (
                  <p className="text-[11px] leading-relaxed text-danger">{varsErr}</p>
                ) : varsLoading ? (
                  <p className="text-[11px] text-zinc-500">
                    {tr('读取已发布版本…', 'Loading published version…')}
                  </p>
                ) : vars.length === 0 ? (
                  <p className="text-[11px] text-zinc-500">
                    {tr(
                      '该工作流的开始节点没有声明输入变量。',
                      'The start node declares no variables.',
                    )}
                  </p>
                ) : (
                  vars.map((variable, index) => {
                    const inputId = `trigger-var-${index}`;
                    const raw = values[variable.name] ?? '';
                    return (
                      <div key={variable.name} className="space-y-1">
                        <div className="flex flex-wrap items-baseline gap-1.5">
                          <Label htmlFor={inputId} className="font-sans text-[11px]">
                            {variable.name}
                          </Label>
                          <span className="text-[10px] text-zinc-500">{variable.type}</span>
                          {variable.required && (
                            <span className="text-[10px] text-danger">*</span>
                          )}
                        </div>
                        {variable.description !== '' && (
                          <p className="text-[10.5px] leading-4 text-zinc-500">
                            {variable.description}
                          </p>
                        )}
                        {variable.type === 'boolean' ? (
                          <Select
                            id={inputId}
                            aria-label={tr(
                              `变量 ${variable.name}`,
                              `Variable ${variable.name}`,
                            )}
                            value={raw}
                            onValueChange={(v) =>
                              setValues((prev) => ({ ...prev, [variable.name]: v }))
                            }
                            options={[
                              { value: '', label: tr('使用默认值', 'Use default') },
                              { value: 'true', label: tr('是', 'True') },
                              { value: 'false', label: tr('否', 'False') },
                            ]}
                          />
                        ) : variable.type === 'json' ? (
                          <Textarea
                            id={inputId}
                            aria-label={tr(
                              `变量 ${variable.name}`,
                              `Variable ${variable.name}`,
                            )}
                            value={raw}
                            onChange={(e) =>
                              setValues((prev) => ({
                                ...prev,
                                [variable.name]: e.target.value,
                              }))
                            }
                            className="h-20 w-full resize-y font-sans text-[11px]"
                            placeholder="{ }"
                          />
                        ) : (
                          <Input
                            id={inputId}
                            type={variable.type === 'number' ? 'number' : 'text'}
                            step={variable.type === 'number' ? 'any' : undefined}
                            aria-label={tr(
                              `变量 ${variable.name}`,
                              `Variable ${variable.name}`,
                            )}
                            value={raw}
                            onChange={(e) =>
                              setValues((prev) => ({
                                ...prev,
                                [variable.name]: e.target.value,
                              }))
                            }
                          />
                        )}
                      </div>
                    );
                  })
                )}
              </div>
            )}
          </>
        ) : (
          <>
            <div className="space-y-1.5">
              <Label htmlFor="trigger-agent">{tr('助理', 'Assistant')}</Label>
              <Select
                id="trigger-agent"
                aria-label={tr('助理', 'Assistant')}
                value={agentId}
                onValueChange={setAgentId}
                options={agents.map((a) => ({
                  value: a.name,
                  label: a.display_name && a.display_name !== a.name
                    ? `${a.display_name}（${a.name}）`
                    : a.name,
                }))}
              />
              <p className="text-[11px] text-zinc-500">
                {tr(
                  '建议绑定只读工具的助理：无人值守时审批会中止回合。',
                  'Prefer read-only assistants: approval waits stop the turn.',
                )}
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="trigger-prompt">
                {tr('触发时的用户消息', 'Prompt on fire')}
              </Label>
              <Textarea
                id="trigger-prompt"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                className="h-20 w-full resize-y"
              />
            </div>
          </>
        )}
        {kind === 'webhook' && (
          <p className="text-[11px] text-zinc-500">
            {tr(
              'Webhook 地址在保存后只显示一次，请立即复制保存。',
              'The webhook URL is shown once after saving — copy it now.',
            )}
          </p>
        )}
      </div>
    </Modal>
  );
}

function WebhookRevealModal({
  name,
  url,
  onCopied,
  onClose,
}: {
  name: string;
  url: string;
  onCopied(): void;
  onClose(): void;
}) {
  const { tr } = useI18n();
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    await navigator.clipboard.writeText(url);
    setCopied(true);
    onCopied();
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={tr(`Webhook 地址 · ${name}`, `Webhook URL · ${name}`)}
      size="md"
      footer={
        <Button variant="primary" size="sm" onClick={onClose}>
          {tr('我已保存', 'I saved it')}
        </Button>
      }
    >
      <div className="space-y-3 text-xs text-zinc-300">
        <p className="rounded-lg border border-warn/30 bg-warn/10 px-3 py-2 text-[12px] leading-relaxed text-warn">
          {tr(
            '地址只显示这一次，关闭后无法再次查看；丢失请重新创建触发器。',
            'This URL is shown only once and cannot be viewed again; recreate the trigger if lost.',
          )}
        </p>
        <CodeBlock maxHeightClassName="max-h-none">{url}</CodeBlock>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => void copy()}>
            {copied ? <Check size={12} /> : <Copy size={12} />}
            {copied ? tr('已复制', 'Copied') : tr('复制地址', 'Copy URL')}
          </Button>
          <span className="text-[10px] text-zinc-500">
            {tr('外部系统 POST 到该地址即可触发', 'External systems POST to this URL')}
          </span>
        </div>
      </div>
    </Modal>
  );
}
