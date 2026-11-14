// 评测集详情：用例维护、发起真实会话评测、运行结果与基线对比。
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  GitCompare,
  Loader2,
  Pencil,
  Play,
  Plus,
  Power,
  RefreshCw,
  Trash2,
} from 'lucide-react';
import {
  Button,
  Chip,
  EmptyStateCard,
  ErrorBanner,
  Input,
  Label,
  PageHeader,
  RowAction,
  RowActionIcon,
  Select,
  StatusPill,
  TableCard,
  TableHead,
  Textarea,
  Toast,
  type ToastTone,
  COL,
  tableLayout,
} from '@/components/ui';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { Modal } from '@/components/Modal';
import { Hint } from '@/components/ui/Tooltip';
import { errorText } from '@/lib/errors';
import { fullDateTime } from '@/lib/format';
import { CASE_STATUS_META, RUN_STATUS_META, caseStatusMeta } from '@/lib/evalStatus';
import { useTimeout } from '@/lib/useTimeout';
import { useI18n } from '@/i18n/locale';
import { usePermissions } from '@/store/me';
import { listAgents, type AgentSummary } from '@/api/agents';
import { listModels, type ModelCatalog } from '@/api/chat';
import {
  compareEvalRuns,
  createEvalCase,
  deleteEvalCase,
  getEvalDataset,
  getEvalRun,
  startEvalRun,
  updateEvalCase,
  type EvalCase,
  type EvalCaseInput,
  type EvalCompare,
  type EvalDatasetDetail,
  type EvalRun,
  type EvalRunDetail,
} from '@/api/evals';

export default function EvalDatasetPage() {
  const { tr } = useI18n();
  const { datasetId } = useParams();
  const id = Number(datasetId);
  const { canMutate } = usePermissions();
  const [dataset, setDataset] = useState<EvalDatasetDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [caseEditor, setCaseEditor] = useState<null | { case: EvalCase | null }>(
    null,
  );
  const [deletingCase, setDeletingCase] = useState<EvalCase | null>(null);
  const [runOpen, setRunOpen] = useState(false);
  const [activeRunUid, setActiveRunUid] = useState<string | null>(null);
  const [activeRun, setActiveRun] = useState<EvalRunDetail | null>(null);
  const [runErr, setRunErr] = useState<string | null>(null);
  const [compareFor, setCompareFor] = useState<EvalRun | null>(null);
  const [toast, setToast] = useState<{ tone: ToastTone; text: string } | null>(null);

  const flashTimer = useTimeout();
  const flash = useCallback(
    (tone: ToastTone, text: string) => {
      setToast({ tone, text });
      flashTimer(() => setToast(null), 4000);
    },
    [flashTimer],
  );

  const load = useCallback(async (silent = false) => {
    if (!Number.isInteger(id) || id <= 0) {
      setErr('评测集 id 非法');
      setLoading(false);
      return;
    }
    if (!silent) setLoading(true);
    try {
      const result = await getEvalDataset(id);
      setDataset(result);
      setErr(null);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  const hasRunningRun = useMemo(
    () => (dataset?.runs ?? []).some((run) => run.status === 'running'),
    [dataset],
  );

  useEffect(() => {
    if (!hasRunningRun) return;
    const timer = window.setInterval(() => void load(true), 2000);
    return () => window.clearInterval(timer);
  }, [hasRunningRun, load]);

  const pollingRun =
    activeRunUid !== null && (activeRun === null || activeRun.status === 'running');

  useEffect(() => {
    if (!pollingRun || activeRunUid === null) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const result = await getEvalRun(activeRunUid);
        if (cancelled) return;
        setActiveRun(result);
        if (result.status !== 'running') {
          void load(true);
        }
      } catch (e) {
        if (!cancelled) setRunErr(errorText(e));
      }
    };
    void tick();
    const timer = window.setInterval(tick, 1500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [pollingRun, activeRunUid, load]);

  if (loading && dataset === null) {
    return (
      <main className="anim-fade flex flex-1 items-center justify-center text-[11px] text-zinc-500">
        <Loader2 size={12} className="mr-2 animate-spin" />
        {tr('加载中…', 'Loading…')}
      </main>
    );
  }

  if (dataset === null) {
    return (
      <main className="anim-fade flex flex-1 flex-col overflow-hidden">
        <PageHeader
          title={tr('评测集不存在', 'Dataset not found')}
          leading={
            <Link to="/evals" className="hover:text-zinc-300">
              {tr('评测', 'Evals')}
            </Link>
          }
        />
        <div className="px-6 py-6">
          <ErrorBanner>{err ?? tr('评测集不存在', 'Dataset not found')}</ErrorBanner>
        </div>
      </main>
    );
  }

  const enabledCases = dataset.cases.filter((item) => item.enabled).length;

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={dataset.name}
        subtitle={
          dataset.description ||
          tr('用真实助理会话跑回归用例', 'Run regression cases against real assistant sessions')
        }
        leading={
          <Link
            to="/evals"
            className="inline-flex items-center gap-1 text-zinc-500 transition-colors hover:text-zinc-300"
          >
            <ArrowLeft size={12} />
            {tr('评测', 'Evals')}
          </Link>
        }
        actions={
          <>
            <RowAction icon={RefreshCw} onClick={() => void load()}>
              {tr('刷新', 'Refresh')}
            </RowAction>
            {canMutate && (
              <Button
                variant="primary"
                size="sm"
                onClick={() => setRunOpen(true)}
                disabled={enabledCases === 0 || hasRunningRun}
              >
                <Play size={12} />
                {hasRunningRun ? tr('评测运行中', 'Run in progress') : tr('运行评测', 'Run evals')}
              </Button>
            )}
          </>
        }
      />

      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-6 py-4">
        {err && <ErrorBanner>{err}</ErrorBanner>}

        <section className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-[12px] font-semibold text-zinc-200">
                {tr('用例', 'Cases')}
              </h2>
              <p className="text-[11px] text-zinc-500">
                {tr(
                  `共 ${dataset.cases.length} 条，${enabledCases} 条参与评测`,
                  `${dataset.cases.length} total, ${enabledCases} enabled`,
                )}
              </p>
            </div>
            {canMutate && (
              <Button size="sm" onClick={() => setCaseEditor({ case: null })}>
                <Plus size={12} />
                {tr('新增用例', 'Add case')}
              </Button>
            )}
          </div>
          {dataset.cases.length === 0 ? (
            <EmptyStateCard
              icon={Play}
              title={tr('还没有用例', 'No cases yet')}
              hint={tr(
                '一条用例 = 一个真实提问 + 关键词 / 工具 / 时延 / token 断言。',
                'A case is a real prompt plus deterministic keyword, tool, latency or token assertions.',
              )}
            />
          ) : (
            <TableCard>
              <table {...tableLayout(56)}>
                <colgroup>
                  <col className={COL.main} />
                  <col className={COL.detail} />
                  <col className={COL.tag} />
                  <col className={COL.actionsIcon} />
                </colgroup>
                <TableHead>
                  <tr className="whitespace-nowrap">
                    <th scope="col" className="px-3 py-2 text-left font-medium">
                      {tr('用例', 'Case')}
                    </th>
                    <th scope="col" className="px-3 py-2 text-left font-medium">
                      {tr('断言', 'Assertions')}
                    </th>
                    <th scope="col" className="px-3 py-2 text-left font-medium">
                      {tr('状态', 'Status')}
                    </th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">
                      {tr('操作', 'Actions')}
                    </th>
                  </tr>
                </TableHead>
                <tbody className="divide-y divide-zinc-800/40">
                  {dataset.cases.map((item) => (
                    <tr
                      key={item.id}
                      className="group align-top transition-colors hover:bg-zinc-900/40"
                    >
                      <td className="px-3 py-2">
                        <span className="block truncate text-[12.5px] font-medium text-zinc-100">
                          {item.name}
                        </span>
                        <span className="mt-0.5 block max-h-8 overflow-hidden text-[10px] leading-4 text-zinc-500">
                          {item.input}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <CaseAssertions item={item} />
                      </td>
                      <td className="px-3 py-2">
                        <StatusPill
                          tone={item.enabled ? 'ok' : 'neutral'}
                          label={
                            item.enabled ? tr('启用', 'Enabled') : tr('停用', 'Disabled')
                          }
                        />
                      </td>
                      <td className="px-3 py-2 text-right">
                        {canMutate ? (
                          <div className="flex items-center justify-end gap-0.5 opacity-60 transition-opacity group-hover:opacity-100">
                            <Hint content={item.enabled ? tr('停用', 'Disable') : tr('启用', 'Enable')}>
                              <RowActionIcon
                                icon={Power}
                                label={item.enabled ? tr('停用', 'Disable') : tr('启用', 'Enable')}
                                onClick={async () => {
                                  try {
                                    await updateEvalCase(item.id, { enabled: !item.enabled });
                                    await load(true);
                                  } catch (e) {
                                    flash('err', errorText(e));
                                  }
                                }}
                              />
                            </Hint>
                            <Hint content={tr('编辑', 'Edit')}>
                              <RowActionIcon
                                icon={Pencil}
                                label={tr('编辑', 'Edit')}
                                onClick={() => setCaseEditor({ case: item })}
                              />
                            </Hint>
                            <Hint content={tr('删除', 'Delete')}>
                              <RowActionIcon
                                icon={Trash2}
                                label={tr('删除', 'Delete')}
                                tone="danger"
                                onClick={() => setDeletingCase(item)}
                              />
                            </Hint>
                          </div>
                        ) : (
                          <span className="text-[11px] text-zinc-500">{tr('只读', 'Read only')}</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableCard>
          )}
        </section>

        <section className="space-y-2">
          <div>
            <h2 className="text-[12px] font-semibold text-zinc-200">
              {tr('运行记录', 'Run history')}
            </h2>
            <p className="text-[11px] text-zinc-500">
              {tr(
                '每次运行冻结当时的用例；点开结果可查看逐用例失败原因与 trace。',
                'Each run freezes its cases; open a result for per-case reasons and traces.',
              )}
            </p>
          </div>
          {dataset.runs.length === 0 ? (
            <EmptyStateCard
              icon={Play}
              title={tr('还没有运行记录', 'No runs yet')}
              hint={tr(
                '运行评测会为每条用例创建真实会话并消耗模型额度。',
                'Running evals creates real sessions and consumes model quota.',
              )}
            />
          ) : (
            <TableCard>
              <table {...tableLayout(58)}>
                <colgroup>
                  <col className={COL.tag} />
                  <col className={COL.tag} />
                  <col className={COL.detail} />
                  <col className={COL.time} />
                  <col className={COL.actionsIconWide} />
                </colgroup>
                <TableHead>
                  <tr className="whitespace-nowrap">
                    <th scope="col" className="px-3 py-2 text-left font-medium">
                      {tr('状态', 'Status')}
                    </th>
                    <th scope="col" className="px-3 py-2 text-left font-medium">
                      {tr('通过', 'Passed')}
                    </th>
                    <th scope="col" className="px-3 py-2 text-left font-medium">
                      {tr('助理 / 模型', 'Assistant / model')}
                    </th>
                    <th scope="col" className="px-3 py-2 text-left font-medium">
                      {tr('开始时间', 'Started')}
                    </th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">
                      {tr('操作', 'Actions')}
                    </th>
                  </tr>
                </TableHead>
                <tbody className="divide-y divide-zinc-800/40">
                  {dataset.runs.map((run) => {
                    const meta = RUN_STATUS_META[run.status];
                    return (
                      <tr
                        key={run.run_uid}
                        className="group transition-colors hover:bg-zinc-900/40"
                      >
                        <td className="px-3 py-2">
                          <StatusPill
                            tone={meta.tone}
                            label={tr(meta.zh, meta.en)}
                            pulse={run.status === 'running'}
                          />
                        </td>
                        <td className="px-3 py-2 text-[11px] tabular-nums text-zinc-300">
                          {run.passed_cases}/{run.total_cases}
                          {run.failed_cases > 0 && (
                            <span className="ml-1 text-[10px] text-zinc-500">
                              {tr(`${run.failed_cases} 条未过`, `${run.failed_cases} failed`)}
                            </span>
                          )}
                        </td>
                        <td className="px-3 py-2">
                          <span className="block font-mono text-[11px] text-zinc-300">
                            {run.agent_id}
                          </span>
                          <span className="block text-[10px] text-zinc-500">
                            {run.provider} · {run.model}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-[11px] text-zinc-500">
                          {run.started_at ? fullDateTime(run.started_at) : '—'}
                        </td>
                        <td className="px-3 py-2 text-right">
                          <div className="flex items-center justify-end gap-0.5 opacity-60 transition-opacity group-hover:opacity-100">
                            <Hint content={tr('查看结果', 'View results')}>
                              <RowActionIcon
                                icon={Play}
                                label={tr('查看结果', 'View results')}
                                onClick={() => {
                                  setRunErr(null);
                                  setActiveRun(null);
                                  setActiveRunUid(run.run_uid);
                                }}
                              />
                            </Hint>
                            <Hint
                              content={
                                run.status === 'completed'
                                  ? tr('与基线对比', 'Compare with baseline')
                                  : tr('运行完成后可对比', 'Compare after completion')
                              }
                            >
                              <RowActionIcon
                                icon={GitCompare}
                                label={tr('对比', 'Compare')}
                                disabled={run.status !== 'completed'}
                                onClick={() => setCompareFor(run)}
                              />
                            </Hint>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableCard>
          )}
        </section>
      </div>

      {caseEditor !== null && (
        <CaseEditor
          datasetId={dataset.id}
          item={caseEditor.case}
          onClose={() => setCaseEditor(null)}
          onSaved={() => {
            setCaseEditor(null);
            void load(true);
            flash('ok', tr('用例已保存', 'Case saved'));
          }}
        />
      )}

      {deletingCase !== null && (
        <ConfirmDialog
          open
          title={tr('删除用例', 'Delete case')}
          description={tr(
            `删除「${deletingCase.name}」不会影响历史运行记录中的快照。`,
            `Deleting "${deletingCase.name}" keeps snapshots in past runs.`,
          )}
          confirmLabel={tr('删除', 'Delete')}
          onClose={() => setDeletingCase(null)}
          onConfirm={async () => {
            await deleteEvalCase(deletingCase.id);
            await load(true);
            flash('ok', tr('用例已删除', 'Case deleted'));
          }}
        />
      )}

      {runOpen && (
        <RunDialog
          datasetId={dataset.id}
          enabledCases={enabledCases}
          onClose={() => setRunOpen(false)}
          onStarted={(run) => {
            setRunOpen(false);
            setRunErr(null);
            setActiveRun(null);
            setActiveRunUid(run.run_uid);
            void load(true);
          }}
        />
      )}

      {activeRunUid !== null && (
        <RunResultDialog
          run={activeRun}
          error={runErr}
          onClose={() => {
            setActiveRunUid(null);
            setActiveRun(null);
            setRunErr(null);
          }}
        />
      )}

      {compareFor !== null && (
        <CompareDialog
          current={compareFor}
          runs={dataset.runs}
          onClose={() => setCompareFor(null)}
        />
      )}

      {toast && <Toast tone={toast.tone} text={toast.text} />}
    </main>
  );
}

function CaseAssertions({ item }: { item: EvalCase }) {
  const { tr } = useI18n();
  const chips: string[] = [];
  for (const keyword of item.expected_keywords) {
    chips.push(tr(`含「${keyword}」`, `contains "${keyword}"`));
  }
  for (const keyword of item.forbidden_keywords) {
    chips.push(tr(`不含「${keyword}」`, `excludes "${keyword}"`));
  }
  for (const tool of item.expected_tools) {
    chips.push(tr(`调用 ${tool}`, `calls ${tool}`));
  }
  for (const tool of item.forbidden_tools) {
    chips.push(tr(`禁用 ${tool}`, `forbids ${tool}`));
  }
  if (item.max_duration_ms !== null) {
    chips.push(tr(`≤ ${item.max_duration_ms}ms`, `≤ ${item.max_duration_ms}ms`));
  }
  if (item.max_total_tokens !== null) {
    chips.push(tr(`≤ ${item.max_total_tokens} tokens`, `≤ ${item.max_total_tokens} tokens`));
  }
  if (chips.length === 0) {
    return <span className="text-[11px] text-zinc-500">{tr('仅校验回合成功', 'Only checks turn success')}</span>;
  }
  return (
    <div className="flex max-w-full flex-wrap gap-1">
      {chips.slice(0, 6).map((text) => (
        <Chip key={text} dense tone="default">
          {text}
        </Chip>
      ))}
      {chips.length > 6 && (
        <Chip dense tone="default">
          +{chips.length - 6}
        </Chip>
      )}
    </div>
  );
}

function splitLines(value: string): string[] {
  return value
    .split('\n')
    .map((item) => item.trim())
    .filter((item) => item !== '');
}

function parseOptionalNumber(value: string): number | null {
  const text = value.trim();
  if (text === '') return null;
  const parsed = Number(text);
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new Error('数值必须是非负数字');
  }
  return Math.round(parsed);
}

function CaseEditor({
  datasetId,
  item,
  onClose,
  onSaved,
}: {
  datasetId: number;
  item: EvalCase | null;
  onClose(): void;
  onSaved(): void;
}) {
  const { tr } = useI18n();
  const editing = item !== null;
  const [name, setName] = useState(item?.name ?? '');
  const [input, setInput] = useState(item?.input ?? '');
  const [expectedKeywords, setExpectedKeywords] = useState(
    (item?.expected_keywords ?? []).join('\n'),
  );
  const [forbiddenKeywords, setForbiddenKeywords] = useState(
    (item?.forbidden_keywords ?? []).join('\n'),
  );
  const [expectedTools, setExpectedTools] = useState(
    (item?.expected_tools ?? []).join('\n'),
  );
  const [forbiddenTools, setForbiddenTools] = useState(
    (item?.forbidden_tools ?? []).join('\n'),
  );
  const [maxDuration, setMaxDuration] = useState(
    item?.max_duration_ms == null ? '' : String(item.max_duration_ms),
  );
  const [maxTokens, setMaxTokens] = useState(
    item?.max_total_tokens == null ? '' : String(item.max_total_tokens),
  );
  const [enabled, setEnabled] = useState(item?.enabled ?? true);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = async () => {
    setSaving(true);
    setErr(null);
    try {
      const payload: EvalCaseInput = {
        name: name.trim(),
        input: input.trim(),
        expected_keywords: splitLines(expectedKeywords),
        forbidden_keywords: splitLines(forbiddenKeywords),
        expected_tools: splitLines(expectedTools),
        forbidden_tools: splitLines(forbiddenTools),
        max_duration_ms: parseOptionalNumber(maxDuration),
        max_total_tokens: parseOptionalNumber(maxTokens),
        enabled,
      };
      if (item !== null) {
        await updateEvalCase(item.id, payload);
      } else {
        await createEvalCase(datasetId, payload);
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
      title={editing ? tr('编辑用例', 'Edit case') : tr('新增用例', 'New case')}
      size="lg"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose}>
            {tr('取消', 'Cancel')}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => void save()}
            disabled={saving || name.trim() === '' || input.trim() === ''}
          >
            {saving ? tr('保存中…', 'Saving…') : tr('保存用例', 'Save case')}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {err && <ErrorBanner>{err}</ErrorBanner>}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="eval-case-name">{tr('用例名称', 'Case name')}</Label>
            <Input
              id="eval-case-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={tr('例如 网络排障：DNS 解析失败', 'e.g. DNS resolution failure')}
              maxLength={128}
            />
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="eval-case-input">{tr('输入', 'Input')}</Label>
            <Textarea
              id="eval-case-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={tr(
                '发给助理的真实问题，会创建一条独立会话',
                'The real prompt sent to the assistant in a fresh session',
              )}
              rows={3}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="eval-case-expected-keywords">
              {tr('期望关键词', 'Expected keywords')}
            </Label>
            <Textarea
              id="eval-case-expected-keywords"
              value={expectedKeywords}
              onChange={(e) => setExpectedKeywords(e.target.value)}
              placeholder={tr('每行一个，全部命中才通过', 'One per line; all must appear')}
              rows={3}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="eval-case-forbidden-keywords">
              {tr('禁止关键词', 'Forbidden keywords')}
            </Label>
            <Textarea
              id="eval-case-forbidden-keywords"
              value={forbiddenKeywords}
              onChange={(e) => setForbiddenKeywords(e.target.value)}
              placeholder={tr('每行一个，出现即失败', 'One per line; any match fails')}
              rows={3}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="eval-case-expected-tools">
              {tr('期望工具', 'Expected tools')}
            </Label>
            <Textarea
              id="eval-case-expected-tools"
              value={expectedTools}
              onChange={(e) => setExpectedTools(e.target.value)}
              placeholder={tr(
                '每行一个工具名，必须被调用；MCP 用 mcp__server__tool',
                'One tool name per line; MCP uses mcp__server__tool',
              )}
              rows={3}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="eval-case-forbidden-tools">
              {tr('禁止工具', 'Forbidden tools')}
            </Label>
            <Textarea
              id="eval-case-forbidden-tools"
              value={forbiddenTools}
              onChange={(e) => setForbiddenTools(e.target.value)}
              placeholder={tr('每行一个，调用即失败', 'One per line; any call fails')}
              rows={3}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="eval-case-duration">{tr('最大耗时（ms）', 'Max latency (ms)')}</Label>
            <Input
              id="eval-case-duration"
              type="number"
              min={0}
              value={maxDuration}
              onChange={(e) => setMaxDuration(e.target.value)}
              placeholder={tr('留空不限', 'Unlimited')}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="eval-case-tokens">{tr('最大 tokens', 'Max tokens')}</Label>
            <Input
              id="eval-case-tokens"
              type="number"
              min={0}
              value={maxTokens}
              onChange={(e) => setMaxTokens(e.target.value)}
              placeholder={tr('留空不限', 'Unlimited')}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="eval-case-enabled">{tr('状态', 'Status')}</Label>
            <Select
              id="eval-case-enabled"
              value={enabled ? 'true' : 'false'}
              onValueChange={(value) => setEnabled(value === 'true')}
              options={[
                { value: 'true', label: tr('启用', 'Enabled') },
                { value: 'false', label: tr('停用', 'Disabled') },
              ]}
              aria-label={tr('状态', 'Status')}
              className="w-full"
            />
          </div>
        </div>
      </div>
    </Modal>
  );
}

function RunDialog({
  datasetId,
  enabledCases,
  onClose,
  onStarted,
}: {
  datasetId: number;
  enabledCases: number;
  onClose(): void;
  onStarted(run: EvalRun): void;
}) {
  const { tr } = useI18n();
  const [agents, setAgents] = useState<AgentSummary[] | null>(null);
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [agentId, setAgentId] = useState('default');
  const [modelValue, setModelValue] = useState('');
  const [starting, setStarting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([listAgents(), listModels()])
      .then(([agentList, modelCatalog]) => {
        if (cancelled) return;
        setAgents(agentList.items);
        setCatalog(modelCatalog);
        if (!agentList.items.some((item) => item.name === 'default')) {
          setAgentId(agentList.items[0]?.name ?? '');
        }
      })
      .catch((e: Error) => {
        if (!cancelled) setErr(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const agentOptions = (agents ?? []).map((agent) => ({
    value: agent.name,
    label: agent.display_name && agent.display_name !== agent.name
      ? `${agent.display_name}（${agent.name}） — ${agent.description}`
      : `${agent.name} — ${agent.description}`,
  }));
  const modelOptions = [{ value: '', label: tr('跟随助理 / 全局默认', 'Assistant / global default') }];
  for (const provider of catalog?.providers ?? []) {
    for (const model of provider.models) {
      modelOptions.push({
        value: `${provider.id}::${model}`,
        label: `${provider.label} · ${model}`,
      });
    }
  }

  const start = async () => {
    setStarting(true);
    setErr(null);
    try {
      const [provider, model] = modelValue === '' ? ['', ''] : modelValue.split('::', 2);
      const run = await startEvalRun(datasetId, {
        agent_id: agentId,
        provider,
        model,
      });
      onStarted(run);
    } catch (e) {
      setErr(errorText(e));
      setStarting(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={tr('运行评测', 'Run evals')}
      size="md"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose}>
            {tr('取消', 'Cancel')}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => void start()}
            disabled={starting || agentId === '' || agents === null}
          >
            {starting ? tr('发起中…', 'Starting…') : tr('开始运行', 'Start run')}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {err && <ErrorBanner>{err}</ErrorBanner>}
        <p className="text-[11px] leading-5 text-zinc-400">
          {tr(
            `将依次运行 ${enabledCases} 条启用用例，每条创建独立会话并真实消耗模型额度。`,
            `${enabledCases} enabled cases will run sequentially in fresh sessions and consume model quota.`,
          )}
        </p>
        <div className="space-y-1.5">
          <Label htmlFor="eval-run-agent">{tr('助理', 'Assistant')}</Label>
          <Select
            id="eval-run-agent"
            value={agentId}
            onValueChange={setAgentId}
            options={agentOptions}
            disabled={agents === null}
            aria-label={tr('助理', 'Assistant')}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="eval-run-model">{tr('模型', 'Model')}</Label>
          <Select
            id="eval-run-model"
            value={modelValue}
            onValueChange={setModelValue}
            options={modelOptions}
            disabled={catalog === null}
            aria-label={tr('模型', 'Model')}
          />
          <p className="text-[11px] text-zinc-500">
            {tr(
              '留空时优先助理配置的模型，再回落到全局默认。',
              'When empty, the assistant model is used, then the global default.',
            )}
          </p>
        </div>
      </div>
    </Modal>
  );
}

function RunResultDialog({
  run,
  error,
  onClose,
}: {
  run: EvalRunDetail | null;
  error: string | null;
  onClose(): void;
}) {
  const { tr } = useI18n();
  const meta = run ? RUN_STATUS_META[run.status] : null;
  const totalTokens = (run?.results ?? []).reduce(
    (sum, item) => sum + (item.total_tokens ?? 0),
    0,
  );
  const totalDuration = (run?.results ?? []).reduce(
    (sum, item) => sum + (item.duration_ms ?? 0),
    0,
  );

  return (
    <Modal
      open
      onClose={onClose}
      title={tr('评测结果', 'Eval results')}
      size="xl"
      resizable
    >
      {error && <ErrorBanner className="mb-3">{error}</ErrorBanner>}
      {run === null ? (
        <div className="flex items-center justify-center py-10 text-[11px] text-zinc-500">
          <Loader2 size={12} className="mr-2 animate-spin" />
          {tr('加载结果…', 'Loading results…')}
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-zinc-800/60 bg-zinc-950/60 px-3 py-2">
            {meta && (
              <StatusPill
                tone={meta.tone}
                label={tr(meta.zh, meta.en)}
                pulse={run.status === 'running'}
              />
            )}
            <span className="text-[11px] text-zinc-300">
              {tr(
                `通过 ${run.passed_cases}/${run.total_cases}`,
                `${run.passed_cases}/${run.total_cases} passed`,
              )}
            </span>
            <span className="text-[11px] tabular-nums text-zinc-500">
              {tr(
                `累计 ${totalTokens.toLocaleString()} tokens · ${(totalDuration / 1000).toFixed(1)}s`,
                `${totalTokens.toLocaleString()} tokens · ${(totalDuration / 1000).toFixed(1)}s`,
              )}
            </span>
            <span className="ml-auto font-mono text-[10px] text-zinc-500">
              {run.agent_id} · {run.provider} · {run.model}
            </span>
          </div>
          {run.error && <ErrorBanner>{run.error}</ErrorBanner>}
          {run.status === 'running' && run.results.length < run.total_cases && (
            <p className="text-[11px] text-zinc-500">
              {tr(
                `已完成 ${run.results.length}/${run.total_cases} 条，结果会自动刷新。`,
                `${run.results.length}/${run.total_cases} finished; refreshing automatically.`,
              )}
            </p>
          )}
          {run.results.length === 0 ? (
            <div className="py-8 text-center text-[11px] text-zinc-500">
              {tr('还没有用例结果', 'No case results yet')}
            </div>
          ) : (
            <div className="max-h-[55vh] overflow-y-auto rounded-lg border border-zinc-800/60">
              <table className="w-full text-left text-[11px]">
                <thead className="sticky top-0 z-10 bg-zinc-900/95 text-zinc-500">
                  <tr>
                    <th className="px-3 py-2 font-medium">{tr('用例', 'Case')}</th>
                    <th className="px-3 py-2 font-medium">{tr('结果', 'Result')}</th>
                    <th className="px-3 py-2 font-medium">{tr('失败原因', 'Reasons')}</th>
                    <th className="px-3 py-2 font-medium">{tr('输出 / 追踪', 'Output / trace')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/40 align-top">
                  {run.results.map((result) => {
                    const itemMeta = CASE_STATUS_META[result.status];
                    return (
                      <tr key={result.id} className="hover:bg-zinc-900/40">
                        <td className="max-w-[10rem] px-3 py-2">
                          <span className="block truncate font-medium text-zinc-200">
                            {result.case_name}
                          </span>
                          <span className="block truncate text-[10px] text-zinc-500">
                            {result.input}
                          </span>
                        </td>
                        <td className="whitespace-nowrap px-3 py-2">
                          <StatusPill tone={itemMeta.tone} label={tr(itemMeta.zh, itemMeta.en)} />
                          <span className="mt-1 block tabular-nums text-[10px] text-zinc-500">
                            {result.duration_ms ?? '—'}ms · {result.total_tokens ?? '—'} tk
                          </span>
                        </td>
                        <td className="max-w-[14rem] px-3 py-2 text-[10px] text-zinc-400">
                          {result.failure_reasons.length === 0 ? (
                            <span className="text-zinc-600">—</span>
                          ) : (
                            <ul className="list-disc space-y-0.5 pl-3">
                              {result.failure_reasons.map((reason) => (
                                <li key={reason}>{reason}</li>
                              ))}
                            </ul>
                          )}
                          {result.tool_names.length > 0 && (
                            <div className="mt-1 flex flex-wrap gap-1">
                              {result.tool_names.map((tool) => (
                                <Chip key={tool} dense tone="info">
                                  {tool}
                                </Chip>
                              ))}
                            </div>
                          )}
                        </td>
                        <td className="max-w-[18rem] px-3 py-2">
                          <pre className="max-h-24 overflow-y-auto whitespace-pre-wrap break-words rounded bg-zinc-950/70 px-2 py-1 text-[10px] leading-4 text-zinc-400">
                            {result.output || '—'}
                          </pre>
                          {result.session_id && (
                            <Link
                              to={`/observability?session_id=${encodeURIComponent(result.session_id)}`}
                              className="mt-1 inline-block whitespace-nowrap text-[10px] text-indigo-300 hover:text-indigo-200"
                            >
                              {tr('查看 trace', 'View trace')}
                            </Link>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}

function CompareDialog({
  current,
  runs,
  onClose,
}: {
  current: EvalRun;
  runs: EvalRun[];
  onClose(): void;
}) {
  const { tr } = useI18n();
  const candidates = runs.filter(
    (run) => run.run_uid !== current.run_uid && run.status === 'completed',
  );
  const [baseline, setBaseline] = useState(candidates[0]?.run_uid ?? '');
  const [data, setData] = useState<EvalCompare | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (baseline === '') return;
    let cancelled = false;
    setErr(null);
    void compareEvalRuns(current.run_uid, baseline)
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((e: Error) => {
        if (!cancelled) setErr(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [current.run_uid, baseline]);

  return (
    <Modal
      open
      onClose={onClose}
      title={tr('运行对比', 'Compare runs')}
      size="xl"
      resizable
    >
      <div className="space-y-3">
        {err && <ErrorBanner>{err}</ErrorBanner>}
        <div className="flex flex-wrap items-center gap-2">
          <Label htmlFor="eval-compare-baseline">{tr('基线运行', 'Baseline')}</Label>
          <Select
            id="eval-compare-baseline"
            value={baseline}
            onValueChange={setBaseline}
            options={candidates.map((run) => ({
              value: run.run_uid,
              label: `${run.started_at ? fullDateTime(run.started_at) : '—'} · ${run.agent_id} · ${run.model} · ${run.passed_cases}/${run.total_cases}`,
            }))}
            aria-label={tr('基线运行', 'Baseline')}
            className="min-w-[22rem]"
          />
          <span className="ml-auto font-mono text-[10px] text-zinc-500">
            {tr('当前', 'Current')}: {current.agent_id} · {current.model}
          </span>
        </div>
        {candidates.length === 0 ? (
          <div className="py-6 text-center text-[11px] text-zinc-500">
            {tr('还没有其他已完成的运行可作为基线', 'No other completed run to compare')}
          </div>
        ) : data === null ? (
          <div className="flex items-center justify-center py-8 text-[11px] text-zinc-500">
            <Loader2 size={12} className="mr-2 animate-spin" />
            {tr('加载对比…', 'Loading comparison…')}
          </div>
        ) : (
          <>
            <div className="grid grid-cols-3 gap-2">
              <SummaryCard
                label={tr('改善', 'Improved')}
                value={String(data.summary.improved)}
                tone="text-ok"
              />
              <SummaryCard
                label={tr('回退', 'Regressed')}
                value={String(data.summary.regressed)}
                tone="text-danger"
              />
              <SummaryCard
                label={tr('通过数', 'Passed')}
                value={`${data.summary.baseline_passed} → ${data.summary.current_passed}`}
                tone="text-zinc-200"
              />
            </div>
            <div className="max-h-[50vh] overflow-y-auto rounded-lg border border-zinc-800/60">
              <table className="w-full text-left text-[11px]">
                <thead className="sticky top-0 z-10 bg-zinc-900/95 text-zinc-500">
                  <tr>
                    <th className="px-3 py-2 font-medium">{tr('用例', 'Case')}</th>
                    <th className="px-3 py-2 font-medium">{tr('基线 → 当前', 'Baseline → current')}</th>
                    <th className="px-3 py-2 font-medium">{tr('变化', 'Change')}</th>
                    <th className="px-3 py-2 font-medium">{tr('当前失败原因', 'Current reasons')}</th>
                    <th className="px-3 py-2 font-medium">{tr('tokens / 耗时', 'Tokens / latency')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/40 align-top">
                  {data.cases.map((row) => {
                    const before = caseStatusMeta(row.baseline_status);
                    const now = caseStatusMeta(row.current_status);
                    return (
                      <tr key={`${row.case_id ?? row.case_name}`} className="hover:bg-zinc-900/40">
                        <td className="px-3 py-2 font-medium text-zinc-200">{row.case_name}</td>
                        <td className="whitespace-nowrap px-3 py-2">
                          <StatusPill tone={before.tone} label={tr(before.zh, before.en)} />
                          <span className="mx-1 text-zinc-600">→</span>
                          <StatusPill tone={now.tone} label={tr(now.zh, now.en)} />
                        </td>
                        <td className="px-3 py-2">
                          <Chip
                            dense
                            tone={
                              row.change === 'improved'
                                ? 'success'
                                : row.change === 'regressed'
                                  ? 'danger'
                                  : 'default'
                            }
                          >
                            {row.change === 'improved'
                              ? tr('改善', 'Improved')
                              : row.change === 'regressed'
                                ? tr('回退', 'Regressed')
                                : tr('持平', 'Same')}
                          </Chip>
                        </td>
                        <td className="max-w-[16rem] px-3 py-2 text-[10px] text-zinc-400">
                          {row.current_reasons.length === 0 ? (
                            <span className="text-zinc-600">—</span>
                          ) : (
                            <ul className="list-disc space-y-0.5 pl-3">
                              {row.current_reasons.map((reason) => (
                                <li key={reason}>{reason}</li>
                              ))}
                            </ul>
                          )}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2 tabular-nums text-[10px] text-zinc-400">
                          <span className="block">
                            {row.baseline_total_tokens ?? '—'} → {row.current_total_tokens ?? '—'}
                          </span>
                          <span className="block text-zinc-500">
                            {row.baseline_duration_ms ?? '—'}ms → {row.current_duration_ms ?? '—'}ms
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}

function SummaryCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: string;
}) {
  return (
    <div className="rounded-lg border border-zinc-800/60 bg-zinc-950/60 px-3 py-2">
      <div className="text-[10px] text-zinc-500">{label}</div>
      <div className={`mt-0.5 text-sm font-semibold tabular-nums ${tone}`}>{value}</div>
    </div>
  );
}
