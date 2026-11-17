// 评测集详情：用例维护、发起真实会话评测、运行结果与基线对比。
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  ChevronDown,
  ClipboardCheck,
  Download,
  GitCompare,
  Link2,
  Loader2,
  MessageSquareText,
  Pencil,
  Play,
  Plus,
  Power,
  RefreshCw,
  ShieldCheck,
  Swords,
  Trash2,
} from 'lucide-react';
import { flash, Button, Chip, EmptyStateCard, ErrorBanner, Input, Label, PageHeader, RowAction, RowActionIcon, Select, StatusPill, Switch, TableCard, TableHead, Tabs, TabsContent, TabsList, TabsTrigger, Textarea, COL, tableLayout, type ToastTone } from '@/components/ui';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { ShareEvalRunDialog } from '@/components/ShareEvalRunDialog';
import { Modal } from '@/components/Modal';
import { Hint } from '@/components/ui/Tooltip';
import { errorText } from '@/lib/errors';
import { EVAL_RUN_EVENT } from '@/lib/useNotifications';
import { saveBlob } from '@/lib/download';
import { fullDateTime } from '@/lib/format';
import { CASE_STATUS_META, RUN_STATUS_META, caseStatusMeta } from '@/lib/evalStatus';
import { useI18n } from '@/i18n/locale';
import { usePermissions } from '@/store/me';
import { listAgents, type AgentSummary } from '@/api/agents';
import { listModels, type ModelCatalog } from '@/api/chat';
import {
  claimNextAnnotation,
  compareEvalRuns,
  createEvalCase,
  deleteEvalCase,
  evaluateGate,
  exportEvalRun,
  getEvalDataset,
  getEvalRun,
  getJudgeConfig,
  listAnnotations,
  listJudgeScores,
  saveJudgeConfig,
  seedRedteamCases,
  startEvalRun,
  submitAnnotation,
  updateAnnotation,
  updateEvalCase,
  updateEvalDataset,
  type EvalAnnotation,
  type EvalCase,
  type EvalCaseInput,
  type EvalCaseKind,
  type EvalCaseResult,
  type EvalCompare,
  type EvalConsistency,
  type EvalDatasetDetail,
  type EvalGateResult,
  type EvalJudgeConfig,
  type EvalJudgeScore,
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
  const [tab, setTab] = useState<'cases' | 'runs' | 'annotations' | 'gate'>('cases');
  const [seeding, setSeeding] = useState(false);


  // 导出走文件流接口，拿到 blob 后触发浏览器下载
  const exportRun = useCallback(
    async (runUid: string, format: 'json' | 'csv') => {
      try {
        const { blob, filename } = await exportEvalRun(runUid, format);
        saveBlob(blob, filename);
        flash('ok', tr('导出已开始', 'Export started'));
      } catch (e) {
        flash('err', errorText(e));
      }
    },
    [tr],
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

  // 导入内置对抗库：同名用例跳过，可重复执行
  const seedRedteam = useCallback(async () => {
    setSeeding(true);
    try {
      const result = await seedRedteamCases(id);
      await load(true);
      flash(
        'ok',
        result.created === 0
          ? tr('对抗用例已在库中，无新增', 'Red team cases already present')
          : tr(`已导入 ${result.created} 条对抗用例`, `Imported ${result.created} red team cases`),
      );
    } catch (e) {
      flash('err', errorText(e));
    } finally {
      setSeeding(false);
    }
  }, [id, load, tr]);

  useEffect(() => {
    void load();
  }, [load]);

  const hasRunningRun = useMemo(
    () => (dataset?.runs ?? []).some((run) => run.status === 'running'),
    [dataset],
  );

  // 评测跑完由全局通知即时刷新；running 期间保留慢兜底轮询。
  useEffect(() => {
    const onDone = () => {
      void load(true);
      if (activeRunUid !== null) void getEvalRun(activeRunUid).then(setActiveRun).catch(() => {});
    };
    window.addEventListener(EVAL_RUN_EVENT, onDone);
    return () => window.removeEventListener(EVAL_RUN_EVENT, onDone);
  }, [load, activeRunUid]);
  useEffect(() => {
    if (!hasRunningRun) return;
    const timer = window.setInterval(() => void load(true), 5000);
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

        <Tabs
          value={tab}
          onValueChange={(value) => setTab(value as 'cases' | 'runs' | 'annotations' | 'gate')}
        >
          <TabsList aria-label={tr('评测集分区', 'Dataset sections')}>
            <TabsTrigger value="cases">{tr('用例', 'Cases')}</TabsTrigger>
            <TabsTrigger value="runs">{tr('运行记录', 'Runs')}</TabsTrigger>
            <TabsTrigger value="annotations">{tr('标注队列', 'Annotation queue')}</TabsTrigger>
            <TabsTrigger value="gate">{tr('门禁', 'Gate')}</TabsTrigger>
          </TabsList>

          <TabsContent value="cases" keepMounted>
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
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => void seedRedteam()}
                  disabled={seeding}
                >
                  <Swords size={12} />
                  {seeding ? tr('导入中…', 'Importing…') : tr('导入对抗用例', 'Import red team cases')}
                </Button>
                <Button size="sm" onClick={() => setCaseEditor({ case: null })}>
                  <Plus size={12} />
                  {tr('新增用例', 'Add case')}
                </Button>
              </div>
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
                      {tr('类型', 'Kind')}
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
                        {item.kind === 'redteam' ? (
                          <div className="space-y-1">
                            <Chip dense tone="warning">
                              {tr('对抗', 'Red team')}
                            </Chip>
                            {item.attack_type && (
                              <span className="block font-sans text-[10px] text-zinc-500">
                                {item.attack_type}
                              </span>
                            )}
                          </div>
                        ) : (
                          <Chip dense tone="default">
                            {tr('普通', 'Normal')}
                          </Chip>
                        )}
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
          </TabsContent>

          <TabsContent value="runs" keepMounted>
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
                          <span className="block font-sans text-[11px] text-zinc-300">
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
                            <Hint
                              content={
                                run.status === 'completed'
                                  ? tr('导出 CSV', 'Export CSV')
                                  : tr('运行完成后可导出', 'Export after completion')
                              }
                            >
                              <RowActionIcon
                                icon={Download}
                                label={tr('导出', 'Export')}
                                disabled={run.status !== 'completed'}
                                onClick={() => void exportRun(run.run_uid, 'csv')}
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
          </TabsContent>

          <TabsContent value="annotations" keepMounted>
            <AnnotationQueueTab
              runs={dataset.runs}
              onFlash={flash}
            />
          </TabsContent>

          <TabsContent value="gate" keepMounted>
            <GateTab
              dataset={dataset}
              onSaved={() => void load(true)}
              onFlash={flash}
            />
          </TabsContent>
        </Tabs>
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
          onExport={(runUid, format) => void exportRun(runUid, format)}
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

    </main>
  );
}

function CaseAssertions({ item }: { item: EvalCase }) {
  const { tr } = useI18n();
  const chips: string[] = [];
  // 对抗用例的安全断言：出现泄漏词即判失败，展示口径与其他断言一致
  for (const keyword of item.safety_keywords ?? []) {
    chips.push(tr(`安全「${keyword}」`, `safety "${keyword}"`));
  }
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
  const [kind, setKind] = useState<EvalCaseKind>(item?.kind ?? 'normal');
  const [attackType, setAttackType] = useState(item?.attack_type ?? '');
  const [safetyKeywords, setSafetyKeywords] = useState(
    (item?.safety_keywords ?? []).join('\n'),
  );
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
        kind,
        attack_type: attackType.trim(),
        safety_keywords: splitLines(safetyKeywords),
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
            disabled={
              saving ||
              name.trim() === '' ||
              input.trim() === '' ||
              (kind === 'redteam' && attackType.trim() === '')
            }
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
            <Label htmlFor="eval-case-kind">{tr('用例类型', 'Case kind')}</Label>
            <Select
              id="eval-case-kind"
              value={kind}
              onValueChange={(value) => setKind(value as EvalCaseKind)}
              options={[
                { value: 'normal', label: tr('普通回归', 'Normal') },
                { value: 'redteam', label: tr('对抗（红队）', 'Red team') },
              ]}
              aria-label={tr('用例类型', 'Case kind')}
              className="w-full"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="eval-case-attack-type">
              {tr('攻击类型', 'Attack type')}
            </Label>
            <Input
              id="eval-case-attack-type"
              value={attackType}
              onChange={(e) => setAttackType(e.target.value)}
              placeholder={tr(
                '对抗用例必填，如 jailbreak / prompt_injection',
                'Required for red team cases, e.g. jailbreak / prompt_injection',
              )}
              maxLength={64}
            />
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="eval-case-safety-keywords">
              {tr('安全断言（泄漏词）', 'Safety keywords')}
            </Label>
            <Textarea
              id="eval-case-safety-keywords"
              value={safetyKeywords}
              onChange={(e) => setSafetyKeywords(e.target.value)}
              placeholder={tr(
                '每行一个；输出出现即判失败（对抗用例核心断言）',
                'One per line; any match in the output fails the case',
              )}
              rows={2}
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
  const [judge, setJudge] = useState<EvalJudgeConfig | null>(null);
  const [starting, setStarting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([listAgents(), listModels(), getJudgeConfig(datasetId)])
      .then(([agentList, modelCatalog, judgeConfig]) => {
        if (cancelled) return;
        setAgents(agentList.items);
        setCatalog(modelCatalog);
        setJudge(judgeConfig);
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
  }, [datasetId]);

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
      // 评分配置跟着本次运行落库，运行中按该配置逐条打分
      if (judge !== null) {
        await saveJudgeConfig(datasetId, {
          enabled: judge.enabled,
          rubric: judge.rubric,
          judge_provider: judge.judge_provider,
          judge_model: judge.judge_model,
          pass_score: judge.pass_score,
        });
      }
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
        <div className="space-y-2 rounded-lg border border-zinc-800/60 bg-zinc-950/60 px-3 py-2.5">
          <Label className="flex w-full items-center justify-between gap-3 text-[11px] text-zinc-300">
            <span>
              {tr('启用 LLM 评分', 'Enable LLM judge')}
              <span className="ml-2 text-[10px] text-zinc-500">
                {tr(
                  '确定性断言之外按 rubric 打维度分，低于阈值判失败',
                  'Score output quality per rubric; below threshold fails the case',
                )}
              </span>
            </span>
            <Switch
              checked={judge?.enabled ?? false}
              onCheckedChange={(checked) =>
                setJudge((prev) =>
                  prev === null
                    ? {
                        dataset_id: datasetId,
                        enabled: checked,
                        rubric: '',
                        judge_provider: '',
                        judge_model: '',
                        pass_score: 3,
                        updated_at: null,
                      }
                    : { ...prev, enabled: checked },
                )
              }
              aria-label={tr('启用 LLM 评分', 'Enable LLM judge')}
            />
          </Label>
          {judge?.enabled && (
            <div className="space-y-2">
              <div className="space-y-1.5">
                <Label htmlFor="eval-judge-rubric">
                  {tr('评分标准（rubric）', 'Rubric')}
                </Label>
                <Textarea
                  id="eval-judge-rubric"
                  value={judge.rubric}
                  onChange={(e) =>
                    setJudge((prev) =>
                      prev === null ? prev : { ...prev, rubric: e.target.value },
                    )
                  }
                  placeholder={tr(
                    '例如：按正确性、完整性、相关性各打 0-5 分',
                    'e.g. score correctness, completeness, relevance from 0-5',
                  )}
                  rows={2}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="eval-judge-pass">
                  {tr('通过阈值（0-5 平均分）', 'Pass score (avg 0-5)')}
                </Label>
                <Input
                  id="eval-judge-pass"
                  type="number"
                  min={0}
                  max={5}
                  step={0.5}
                  value={String(judge.pass_score)}
                  onChange={(e) =>
                    setJudge((prev) =>
                      prev === null
                        ? prev
                        : { ...prev, pass_score: Number(e.target.value) || 0 },
                    )
                  }
                />
              </div>
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}

// LLM 评分平均分：解析失败的 parse 记录不计入，无有效维度返回 null
function judgeAvg(items: EvalJudgeScore[] | undefined): number | null {
  const dims = (items ?? []).filter((item) => item.dimension !== 'parse');
  if (dims.length === 0) return null;
  return dims.reduce((sum, item) => sum + item.score, 0) / dims.length;
}

function RunResultDialog({
  run,
  error,
  onClose,
  onExport,
}: {
  run: EvalRunDetail | null;
  error: string | null;
  onClose(): void;
  onExport(runUid: string, format: 'json' | 'csv'): void;
}) {
  const { tr } = useI18n();
  const meta = run ? RUN_STATUS_META[run.status] : null;
  // 评分数据独立接口：运行完成后一次性取回逐结果 judge 分与阈值配置
  const [scores, setScores] = useState<Record<string, EvalJudgeScore[]> | null>(null);
  const [judgeConfig, setJudgeConfig] = useState<EvalJudgeConfig | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [annotate, setAnnotate] = useState<EvalCaseResult | null>(null);
  const [shareOpen, setShareOpen] = useState(false);

  useEffect(() => {
    if (run === null || run.status !== 'completed') return;
    // 只在进入 completed 的这一刻取一次；此后 run 引用不再变化
    const runUid = run.run_uid;
    const datasetId = run.dataset_id;
    const resultIds = run.results.map((item) => item.id);
    let cancelled = false;
    void (async () => {
      try {
        const [config, pairs] = await Promise.all([
          getJudgeConfig(datasetId),
          Promise.all(
            resultIds.map(async (resultId) => {
              const data = await listJudgeScores(runUid, resultId);
              return [resultId, data.items] as const;
            }),
          ),
        ]);
        if (cancelled) return;
        setJudgeConfig(config);
        setScores(Object.fromEntries(pairs));
      } catch {
        // 评分加载失败不影响结果展示；展开行会显示缺省提示
        if (!cancelled) setScores({});
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [run]);

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
            {run.status === 'completed' && (
              <>
                <RowAction
                  icon={Download}
                  onClick={() => onExport(run.run_uid, 'csv')}
                >
                  {tr('导出 CSV', 'Export CSV')}
                </RowAction>
                <RowAction
                  icon={Download}
                  onClick={() => onExport(run.run_uid, 'json')}
                >
                  {tr('导出 JSON', 'Export JSON')}
                </RowAction>
                <RowAction icon={Link2} onClick={() => setShareOpen(true)}>
                  {tr('分享', 'Share')}
                </RowAction>
              </>
            )}
            <span className="ml-auto font-sans text-[10px] text-zinc-500">
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
                    <th className="px-3 py-2 font-medium">{tr('评分', 'Score')}</th>
                    <th className="px-3 py-2 font-medium">{tr('失败原因', 'Reasons')}</th>
                    <th className="px-3 py-2 font-medium">{tr('输出 / 追踪', 'Output / trace')}</th>
                    <th className="px-3 py-2 text-right font-medium">{tr('操作', 'Actions')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/40 align-top">
                  {run.results.map((result) => {
                    const itemMeta = CASE_STATUS_META[result.status];
                    const avg = judgeAvg(scores?.[result.id]);
                    const dims = scores?.[result.id] ?? [];
                    return (
                      <Fragment key={result.id}>
                        <tr className="hover:bg-zinc-900/40">
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
                          <td className="whitespace-nowrap px-3 py-2">
                            {avg === null ? (
                              <span className="text-[10px] text-zinc-600">—</span>
                            ) : (
                              <div className="flex items-center gap-1">
                                <span className="tabular-nums text-[11px] text-zinc-200">
                                  {avg.toFixed(1)}
                                </span>
                                {judgeConfig?.enabled && (
                                  <Chip
                                    dense
                                    tone={avg >= judgeConfig.pass_score ? 'success' : 'danger'}
                                  >
                                    {avg >= judgeConfig.pass_score
                                      ? tr('过线', 'Above')
                                      : tr('低于阈值', 'Below')}
                                  </Chip>
                                )}
                              </div>
                            )}
                            {dims.some((item) => item.dimension === 'parse') && (
                              <span className="mt-1 block text-[10px] text-danger">
                                {tr('评分解析失败', 'Judge parse error')}
                              </span>
                            )}
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
                          <td className="px-3 py-2 text-right">
                            <div className="flex items-center justify-end gap-0.5">
                              <Hint content={tr('评分详情', 'Score details')}>
                                <RowActionIcon
                                  icon={ChevronDown}
                                  label={tr('评分详情', 'Score details')}
                                  className={expanded === result.id ? 'rotate-180' : ''}
                                  onClick={() =>
                                    setExpanded((prev) => (prev === result.id ? null : result.id))
                                  }
                                />
                              </Hint>
                              <Hint content={tr('标注', 'Annotate')}>
                                <RowActionIcon
                                  icon={MessageSquareText}
                                  label={tr('标注', 'Annotate')}
                                  onClick={() => setAnnotate(result)}
                                />
                              </Hint>
                            </div>
                          </td>
                        </tr>
                        {expanded === result.id && (
                          <tr className="bg-zinc-950/40">
                            <td colSpan={6} className="px-3 py-2">
                              {dims.length === 0 ? (
                                <span className="text-[10px] text-zinc-500">
                                  {tr(
                                    '本次运行没有 LLM 评分记录（未启用评分或结果为 error）。',
                                    'No LLM judge scores for this result.',
                                  )}
                                </span>
                              ) : (
                                <table className="w-full text-left text-[10px]">
                                  <thead className="text-zinc-500">
                                    <tr>
                                      <th className="py-1 pr-3 font-medium">{tr('维度', 'Dimension')}</th>
                                      <th className="py-1 pr-3 font-medium">{tr('分数', 'Score')}</th>
                                      <th className="py-1 pr-3 font-medium">{tr('理由', 'Reason')}</th>
                                      <th className="py-1 font-medium">{tr('裁判模型', 'Judge model')}</th>
                                    </tr>
                                  </thead>
                                  <tbody className="divide-y divide-zinc-800/40">
                                    {dims.map((item) => (
                                      <tr key={item.id}>
                                        <td className="py-1 pr-3 text-zinc-200">{item.dimension}</td>
                                        <td className="py-1 pr-3 tabular-nums text-zinc-300">
                                          {item.score.toFixed(1)}
                                        </td>
                                        <td className="py-1 pr-3 text-zinc-400">{item.reason}</td>
                                        <td className="py-1 font-sans text-zinc-500">{item.judge_model}</td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              )}
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
      {annotate !== null && (
        <AnnotationDialog
          resultId={annotate.id}
          caseName={annotate.case_name}
          onClose={() => setAnnotate(null)}
          onSaved={() => setAnnotate(null)}
        />
      )}
      {shareOpen && run !== null && (
        <ShareEvalRunDialog
          open
          runUid={run.run_uid}
          onClose={() => setShareOpen(false)}
        />
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
          <span className="ml-auto font-sans text-[10px] text-zinc-500">
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

// 人工判定标签的展示口径：颜色跟语义走，文案双语
const LABEL_META: Record<
  string,
  { zh: string; en: string; tone: 'success' | 'warning' | 'danger' }
> = {
  good: { zh: '好', en: 'Good', tone: 'success' },
  needs_work: { zh: '待改进', en: 'Needs work', tone: 'warning' },
  bad: { zh: '差', en: 'Bad', tone: 'danger' },
};

function AnnotationDialog({
  resultId,
  caseName,
  existing,
  onClose,
  onSaved,
}: {
  resultId: string;
  caseName: string;
  existing?: EvalAnnotation | null;
  onClose(): void;
  onSaved(): void;
}) {
  const { tr } = useI18n();
  const [label, setLabel] = useState<EvalAnnotation['label']>(existing?.label ?? 'good');
  const [comment, setComment] = useState(existing?.comment ?? '');
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = async () => {
    setSaving(true);
    setErr(null);
    try {
      if (existing != null) {
        await updateAnnotation(existing.id, { label, comment });
      } else {
        await submitAnnotation({ result_id: resultId, label, comment });
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
      title={tr('标注评测结果', 'Annotate result')}
      size="sm"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose}>
            {tr('取消', 'Cancel')}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => void save()}
            disabled={saving}
          >
            {saving ? tr('保存中…', 'Saving…') : tr('提交标注', 'Submit')}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {err && <ErrorBanner>{err}</ErrorBanner>}
        <p className="text-[11px] text-zinc-400">
          {tr('用例', 'Case')}:{' '}
          <span className="text-zinc-200">{caseName}</span>
        </p>
        <div className="space-y-1.5">
          <Label htmlFor="eval-annotation-label">{tr('判定', 'Label')}</Label>
          <Select
            id="eval-annotation-label"
            value={label}
            onValueChange={(value) => setLabel(value as EvalAnnotation['label'])}
            options={[
              { value: 'good', label: tr('好', 'Good') },
              { value: 'needs_work', label: tr('待改进', 'Needs work') },
              { value: 'bad', label: tr('差', 'Bad') },
            ]}
            aria-label={tr('判定', 'Label')}
            className="w-full"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="eval-annotation-comment">{tr('备注', 'Comment')}</Label>
          <Textarea
            id="eval-annotation-comment"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder={tr('可选：记录判定依据', 'Optional: note your reasoning')}
            rows={3}
            maxLength={2000}
          />
        </div>
      </div>
    </Modal>
  );
}

function AnnotationQueueTab({
  runs,
  onFlash,
}: {
  runs: EvalRun[];
  onFlash(tone: ToastTone, text: string): void;
}) {
  const { tr } = useI18n();
  const completed = useMemo(
    () => runs.filter((run) => run.status === 'completed'),
    [runs],
  );
  const [runUid, setRunUid] = useState(completed[0]?.run_uid ?? '');
  const [stats, setStats] = useState<EvalConsistency | null>(null);
  const [items, setItems] = useState<EvalAnnotation[]>([]);
  const [claim, setClaim] = useState<EvalCaseResult | null>(null);
  const [editing, setEditing] = useState<EvalAnnotation | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (runUid === '') return;
    try {
      const data = await listAnnotations(runUid);
      setItems(data.items);
      setStats(data.consistency);
      setErr(null);
    } catch (e) {
      setErr(errorText(e));
    }
  }, [runUid]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const claimNext = async () => {
    try {
      const data = await claimNextAnnotation(runUid);
      if (data.result === null) {
        setClaim(null);
        onFlash('ok', tr('标注队列已清空', 'Annotation queue is empty'));
      } else {
        setClaim(data.result);
      }
    } catch (e) {
      onFlash('err', errorText(e));
    }
  };

  if (completed.length === 0) {
    return (
      <EmptyStateCard
        icon={ClipboardCheck}
        title={tr('还没有可标注的运行', 'No runs to annotate')}
        hint={tr(
          '先运行一次评测，完成后在这里逐条人工判定结果。',
          'Run an eval first, then annotate its results here.',
        )}
      />
    );
  }

  return (
    <section className="space-y-3">
      {err && <ErrorBanner>{err}</ErrorBanner>}
      <div className="flex flex-wrap items-end gap-2">
        <div className="space-y-1.5">
          <Label htmlFor="eval-ann-run">{tr('运行', 'Run')}</Label>
          <Select
            id="eval-ann-run"
            value={runUid}
            onValueChange={(value) => {
              setRunUid(value);
              setClaim(null);
            }}
            options={completed.map((run) => ({
              value: run.run_uid,
              label: `${run.started_at ? fullDateTime(run.started_at) : '—'} · ${run.model} · ${run.passed_cases}/${run.total_cases}`,
            }))}
            aria-label={tr('运行', 'Run')}
            className="min-w-[22rem]"
          />
        </div>
        <Button size="sm" onClick={() => void claimNext()} disabled={runUid === ''}>
          <ClipboardCheck size={12} />
          {tr('认领下一条', 'Claim next')}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => void reload()}>
          <RefreshCw size={12} />
          {tr('刷新', 'Refresh')}
        </Button>
      </div>

      {stats !== null && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <SummaryCard
            label={tr('已标注', 'Annotated')}
            value={`${stats.annotated_results}/${stats.results_total}`}
            tone="text-zinc-200"
          />
          <SummaryCard
            label={tr('多人标注', 'Multi-annotated')}
            value={String(stats.multi_annotated)}
            tone="text-zinc-200"
          />
          <SummaryCard
            label={tr('一致率', 'Agreement')}
            value={
              stats.agreement_rate === null
                ? '—'
                : `${(stats.agreement_rate * 100).toFixed(0)}%`
            }
            tone="text-zinc-200"
          />
          <div className="rounded-lg border border-zinc-800/60 bg-zinc-950/60 px-3 py-2">
            <div className="text-[10px] text-zinc-500">{tr('判定分布', 'Labels')}</div>
            <div className="mt-1 flex flex-wrap gap-1">
              {(['good', 'needs_work', 'bad'] as const).map((key) => (
                <Chip key={key} dense tone={LABEL_META[key].tone}>
                  {tr(LABEL_META[key].zh, LABEL_META[key].en)} {stats.label_counts[key] ?? 0}
                </Chip>
              ))}
            </div>
          </div>
        </div>
      )}

      {items.length === 0 ? (
        <EmptyStateCard
          icon={ClipboardCheck}
          title={tr('还没有标注', 'No annotations yet')}
          hint={tr(
            '点「认领下一条」开始逐条判定；同一结果可多人标注，一致率自动统计。',
            'Claim the next result to start; multiple annotators feed the agreement rate.',
          )}
        />
      ) : (
        <TableCard>
          <table {...tableLayout(56)}>
            <colgroup>
              <col className={COL.main} />
              <col className={COL.tag} />
              <col className={COL.detail} />
              <col className={COL.tag} />
              <col className={COL.time} />
              <col className={COL.actionsIcon} />
            </colgroup>
            <TableHead>
              <tr className="whitespace-nowrap">
                <th scope="col" className="px-3 py-2 text-left font-medium">
                  {tr('用例', 'Case')}
                </th>
                <th scope="col" className="px-3 py-2 text-left font-medium">
                  {tr('判定', 'Label')}
                </th>
                <th scope="col" className="px-3 py-2 text-left font-medium">
                  {tr('备注', 'Comment')}
                </th>
                <th scope="col" className="px-3 py-2 text-left font-medium">
                  {tr('标注人', 'Annotator')}
                </th>
                <th scope="col" className="px-3 py-2 text-left font-medium">
                  {tr('时间', 'Time')}
                </th>
                <th scope="col" className="px-3 py-2 text-right font-medium">
                  {tr('操作', 'Actions')}
                </th>
              </tr>
            </TableHead>
            <tbody className="divide-y divide-zinc-800/40">
              {items.map((item) => {
                const meta = LABEL_META[item.label] ?? LABEL_META.good;
                return (
                  <tr key={item.id} className="group align-top transition-colors hover:bg-zinc-900/40">
                    <td className="px-3 py-2">
                      <span className="block truncate text-[12px] text-zinc-100">
                        {item.case_name ?? item.result_id}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <Chip dense tone={meta.tone}>
                        {tr(meta.zh, meta.en)}
                      </Chip>
                    </td>
                    <td className="max-w-[16rem] px-3 py-2 text-[10px] text-zinc-400">
                      {item.comment || <span className="text-zinc-600">—</span>}
                    </td>
                    <td className="px-3 py-2 text-[11px] text-zinc-400">
                      {item.annotator_user_id ?? '—'}
                    </td>
                    <td className="px-3 py-2 text-[11px] text-zinc-500">
                      {item.created_at ? fullDateTime(item.created_at) : '—'}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <div className="flex items-center justify-end gap-0.5 opacity-60 transition-opacity group-hover:opacity-100">
                        <Hint content={tr('编辑标注', 'Edit annotation')}>
                          <RowActionIcon
                            icon={Pencil}
                            label={tr('编辑标注', 'Edit annotation')}
                            onClick={() => setEditing(item)}
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

      {claim !== null && (
        <AnnotationDialog
          resultId={claim.id}
          caseName={claim.case_name}
          onClose={() => setClaim(null)}
          onSaved={() => {
            setClaim(null);
            void reload();
            onFlash('ok', tr('标注已提交', 'Annotation saved'));
          }}
        />
      )}
      {editing !== null && (
        <AnnotationDialog
          resultId={editing.result_id}
          caseName={editing.case_name ?? editing.result_id}
          existing={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void reload();
            onFlash('ok', tr('标注已更新', 'Annotation updated'));
          }}
        />
      )}
    </section>
  );
}

function GateTab({
  dataset,
  onSaved,
  onFlash,
}: {
  dataset: EvalDatasetDetail;
  onSaved(): void;
  onFlash(tone: ToastTone, text: string): void;
}) {
  const { tr } = useI18n();
  const completed = useMemo(
    () => dataset.runs.filter((run) => run.status === 'completed'),
    [dataset.runs],
  );
  const [baseline, setBaseline] = useState(dataset.baseline_run_uid ?? '');
  const [maxRegressions, setMaxRegressions] = useState(
    String(dataset.gate_max_regressions),
  );
  const [minPassRate, setMinPassRate] = useState(String(dataset.gate_min_pass_rate));
  const [saving, setSaving] = useState(false);
  const [gateRunUid, setGateRunUid] = useState(completed[0]?.run_uid ?? '');
  const [gate, setGate] = useState<EvalGateResult | null>(null);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = async () => {
    setSaving(true);
    try {
      await updateEvalDataset(dataset.id, {
        baseline_run_uid: baseline,
        gate_max_regressions: Math.max(0, Math.round(Number(maxRegressions) || 0)),
        gate_min_pass_rate: Math.min(1, Math.max(0, Number(minPassRate) || 0)),
      });
      onSaved();
      onFlash('ok', tr('门禁配置已保存', 'Gate config saved'));
    } catch (e) {
      onFlash('err', errorText(e));
    } finally {
      setSaving(false);
    }
  };

  const evaluate = async () => {
    setRunning(true);
    setErr(null);
    try {
      setGate(await evaluateGate(gateRunUid));
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setRunning(false);
    }
  };

  if (completed.length === 0) {
    return (
      <EmptyStateCard
        icon={ShieldCheck}
        title={tr('还没有可门禁的运行', 'No runs to gate')}
        hint={tr(
          '门禁需要至少一次已完成的运行作为基线或被评对象。',
          'The gate needs at least one completed run as baseline or target.',
        )}
      />
    );
  }

  return (
    <section className="space-y-3">
      {err && <ErrorBanner>{err}</ErrorBanner>}
      <div className="rounded-lg border border-zinc-800/60 bg-zinc-950/60 p-3">
        <h3 className="text-[12px] font-semibold text-zinc-200">
          {tr('门禁配置', 'Gate config')}
        </h3>
        <p className="mt-0.5 text-[11px] text-zinc-500">
          {tr(
            '与基线运行对比：回归数超过上限、或通过率低于下限，门禁即 fail；CI 调同一判定。',
            'Compared against the baseline run: too many regressions or low pass rate fails the gate; CI uses the same rule.',
          )}
        </p>
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div className="space-y-1.5">
            <Label htmlFor="eval-gate-baseline">{tr('基线运行', 'Baseline run')}</Label>
            <Select
              id="eval-gate-baseline"
              value={baseline}
              onValueChange={setBaseline}
              options={[
                { value: '', label: tr('未设门禁', 'Not configured') },
                ...completed.map((run) => ({
                  value: run.run_uid,
                  label: `${run.started_at ? fullDateTime(run.started_at) : '—'} · ${run.model} · ${run.passed_cases}/${run.total_cases}`,
                })),
              ]}
              aria-label={tr('基线运行', 'Baseline run')}
              className="w-full"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="eval-gate-max-regressions">
              {tr('允许回归数上限', 'Max regressions')}
            </Label>
            <Input
              id="eval-gate-max-regressions"
              type="number"
              min={0}
              value={maxRegressions}
              onChange={(e) => setMaxRegressions(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="eval-gate-min-pass">
              {tr('通过率下限（0-1）', 'Min pass rate (0-1)')}
            </Label>
            <Input
              id="eval-gate-min-pass"
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={minPassRate}
              onChange={(e) => setMinPassRate(e.target.value)}
            />
          </div>
        </div>
        <div className="mt-3 flex items-center gap-2">
          <Button size="sm" variant="primary" onClick={() => void save()} disabled={saving}>
            {saving ? tr('保存中…', 'Saving…') : tr('保存配置', 'Save config')}
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-end gap-2">
        <div className="space-y-1.5">
          <Label htmlFor="eval-gate-run">{tr('执行门禁的运行', 'Run to gate')}</Label>
          <Select
            id="eval-gate-run"
            value={gateRunUid}
            onValueChange={setGateRunUid}
            options={completed.map((run) => ({
              value: run.run_uid,
              label: `${run.started_at ? fullDateTime(run.started_at) : '—'} · ${run.model} · ${run.passed_cases}/${run.total_cases}`,
            }))}
            aria-label={tr('执行门禁的运行', 'Run to gate')}
            className="min-w-[22rem]"
          />
        </div>
        <Button
          size="sm"
          onClick={() => void evaluate()}
          disabled={running || gateRunUid === '' || baseline === ''}
        >
          {running ? (
            <>
              <Loader2 size={12} className="animate-spin" />
              {tr('判定中…', 'Evaluating…')}
            </>
          ) : (
            <>
              <ShieldCheck size={12} />
              {tr('执行门禁', 'Run gate')}
            </>
          )}
        </Button>
      </div>

      {gate !== null && (
        <div className="space-y-2 rounded-lg border border-zinc-800/60 bg-zinc-950/60 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill
              tone={gate.pass ? 'ok' : 'danger'}
              label={gate.pass ? tr('门禁通过', 'Gate passed') : tr('门禁未通过', 'Gate failed')}
            />
            <span className="text-[11px] text-zinc-400">
              {tr('基线', 'Baseline')}:{' '}
              <span className="font-sans text-[10px] text-zinc-500">{gate.baseline_run_uid}</span>
            </span>
          </div>
          <div className="grid grid-cols-3 gap-2">
            <SummaryCard
              label={tr('通过率', 'Pass rate')}
              value={`${(gate.pass_rate * 100).toFixed(0)}%`}
              tone="text-zinc-200"
            />
            <SummaryCard
              label={tr('回归', 'Regressions')}
              value={String(gate.regressions)}
              tone={gate.regressions > 0 ? 'text-danger' : 'text-zinc-200'}
            />
            <SummaryCard
              label={tr('改善', 'Improved')}
              value={String(gate.improved)}
              tone="text-ok"
            />
          </div>
          {gate.reasons.length > 0 && (
            <ul className="list-disc space-y-0.5 pl-4 text-[11px] text-zinc-400">
              {gate.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          )}
          {gate.regressed_cases.length > 0 && (
            <div className="max-h-[30vh] overflow-y-auto rounded-lg border border-zinc-800/60">
              <table className="w-full text-left text-[11px]">
                <thead className="sticky top-0 z-10 bg-zinc-900/95 text-zinc-500">
                  <tr>
                    <th className="px-3 py-2 font-medium">{tr('回归用例', 'Regressed case')}</th>
                    <th className="px-3 py-2 font-medium">{tr('当前失败原因', 'Current reasons')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/40">
                  {gate.regressed_cases.map((row) => (
                    <tr key={`${row.case_id ?? row.case_name}`}>
                      <td className="px-3 py-2 font-medium text-zinc-200">{row.case_name}</td>
                      <td className="px-3 py-2 text-[10px] text-zinc-400">
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
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
