// 对话内的「执行计划 · N 步」折叠块：create_plan 工具卡的专属渲染。
// 折叠头带计数与状态（规范：折叠标题必须显示数量或状态）；展开看步骤清单与
// 各步结论，待确认时可调整计划（拖拽排序/增删）或开始执行。
// 数据 = 工具卡静态参数/结果 + 计划快照补活（执行/重试后会变）。
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ChevronDown,
  ChevronRight,
  GripVertical,
  MapPin,
  Pause,
  Play,
  Plus,
  Trash2,
} from 'lucide-react';
import {
  getPlan,
  pausePlan,
  startPlan,
  subscribePlanEvents,
  updatePlanSteps,
  type PlanExecution,
  type StepStatus,
} from '@/api/plan';
import { PLAN_STATUS_META, STEP_STATUS_META } from '@/lib/planStatus';
import type { PlanLocateTarget } from './PlanWorkbench';
import { errorText } from '@/lib/errors';
import { flash } from '@/components/ui';
import { useI18n } from '@/i18n/locale';
import { cn } from '@/lib/cn';
import { RowActionIcon, StatusPill } from '@/components/ui';

/** create_plan 的工具返回形状（与后端 tool_result_wire 对齐）。 */
type PlanToolResult = {
  plan_uid?: string;
  title?: string;
  status?: string;
  message?: string;
  steps?: { step_uid: string; name: string; status: StepStatus; summary: string; error: string }[];
  stats?: { total: number; succeeded: number; running: number; failed: number };
};

/** 编辑态单步草稿：tools 以逗号分隔文本编辑，保存时拆回数组。 */
type EditStep = { key: string; name: string; instruction: string; tools: string };

function fmtDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  return `${m}m${Math.round(s - m * 60)}s`;
}

export function PlanBlock({
  call,
  pending,
  onLocatePlan,
}: {
  call: {
    name: string;
    arguments?: unknown;
    result?: unknown;
    duration_ms?: number;
  };
  /** 回合未收口：折叠块只展示进行态，交互禁用 */
  pending?: boolean;
  /** 跳右栏执行进度定位；仅非只读账号传入 */
  onLocatePlan?: (target: PlanLocateTarget) => void;
}) {
  const { tr } = useI18n();
  const [open, setOpen] = useState(false);
  const [wire, setWire] = useState<PlanExecution | null>(null);
  const [editSteps, setEditSteps] = useState<EditStep[] | null>(null);
  const [busy, setBusy] = useState(false);
  // 拖拽排序：记录按下时的行号，落点行号决定插入位置
  const dragFrom = useRef<number | null>(null);

  const args = (call.arguments ?? {}) as { title?: string; question?: string; steps_json?: string };
  const result = (call.result ?? {}) as PlanToolResult;
  const planUid = result.plan_uid ?? wire?.plan_uid ?? null;
  const interactive = onLocatePlan !== undefined && !pending;

  // 快照补活：执行/重试后状态与结论会变，工具卡静态结果是旧的
  const loadWire = useCallback(async () => {
    if (!planUid) return;
    try {
      setWire(await getPlan(planUid));
    } catch {
      // 快照拉不到不打扰对话：工具卡静态结果仍可展示
    }
  }, [planUid]);

  useEffect(() => {
    void loadWire();
  }, [loadWire]);

  // 未终态时订阅事件流，步骤完成即对齐快照
  useEffect(() => {
    if (!planUid) return;
    const terminal = wire ? ['succeeded', 'stopped'].includes(wire.status) : false;
    if (terminal || pending) return;
    const ctrl = new AbortController();
    void subscribePlanEvents(
      planUid,
      (ev) => {
        if (ev.event === 'step_finished' || ev.event === 'plan_updated' || ev.event === 'plan_finished') {
          void loadWire();
        }
      },
      ctrl.signal,
    ).catch(() => {});
    return () => ctrl.abort();
  }, [planUid, wire?.status, pending, loadWire]);

  // 步骤：快照 > 工具结果 > 工具入参
  const stepsFromArgs = (() => {
    try {
      const parsed = JSON.parse(args.steps_json ?? '[]') as { name?: string; instruction?: string; tools?: string[] }[];
      return parsed.map((s, i) => ({
        key: `arg-${i}`,
        name: s.name ?? `#${i + 1}`,
        instruction: s.instruction ?? '',
        tools: s.tools ?? [],
        status: null as StepStatus | null,
        summary: '',
        error: '',
        duration_ms: 0,
      }));
    } catch {
      return [];
    }
  })();
  const steps =
    wire?.steps?.map((s) => ({
      key: s.step_uid,
      name: s.name,
      instruction: s.instruction,
      tools: s.tools,
      status: s.status,
      summary: s.summary,
      error: s.error,
      duration_ms: s.duration_ms,
    })) ??
    result.steps?.map((s) => ({
      key: s.step_uid,
      name: s.name,
      instruction: '',
      tools: [] as string[],
      status: s.status,
      summary: s.summary,
      error: s.error,
      duration_ms: 0,
    })) ??
    stepsFromArgs;

  const stats = wire?.stats;
  const counts = {
    total: stats?.total ?? result.stats?.total ?? steps.length,
    succeeded: stats?.succeeded ?? result.stats?.succeeded ?? steps.filter((s) => s.status === 'succeeded').length,
    running: stats?.running ?? result.stats?.running ?? steps.filter((s) => s.status === 'running').length,
    failed: stats?.failed ?? result.stats?.failed ?? steps.filter((s) => s.status === 'failed').length,
    skipped: stats?.skipped ?? steps.filter((s) => s.status === 'skipped').length,
  };
  const usedTokens = wire && wire.used_tokens > 0 ? wire.used_tokens : null;

  // 总状态：快照状态优先；工具未出结果时是待确认（create_plan 只建不执行）
  const statusKey = (wire?.status ?? (pending ? 'running' : result.status ?? 'draft')) as keyof typeof PLAN_STATUS_META;
  const meta = PLAN_STATUS_META[statusKey] ?? PLAN_STATUS_META.draft;
  const isDraft = statusKey === 'draft';
  const isRunning = statusKey === 'running';
  const isPaused = statusKey === 'paused';

  const enterEdit = () => {
    setOpen(true);
    setEditSteps(
      steps.map((s, i) => ({
        key: s.key || `edit-${i}`,
        name: s.name,
        instruction: s.instruction,
        tools: s.tools.join(', '),
      })),
    );
  };

  const saveEdit = async (thenStart: boolean) => {
    if (!planUid || !editSteps) return;
    const payload = editSteps
      .filter((s) => s.name.trim() !== '')
      .map((s) => ({
        name: s.name.trim(),
        instruction: s.instruction.trim(),
        tools: s.tools
          .split(/[,，\s]+/)
          .map((t) => t.trim())
          .filter(Boolean),
      }));
    if (payload.length === 0) {
      flash('err', tr('至少保留一个步骤', 'Keep at least one step'));
      return;
    }
    setBusy(true);
    try {
      await updatePlanSteps(planUid, JSON.stringify(payload));
      if (thenStart) {
        await startPlan(planUid);
        flash('ok', tr('计划已开始执行', 'Plan started'));
      } else {
        flash('ok', tr('计划已保存', 'Plan saved'));
      }
      setEditSteps(null);
      await loadWire();
    } catch (e) {
      flash('err', errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const doStart = async () => {
    if (!planUid) return;
    setBusy(true);
    try {
      await startPlan(planUid);
      flash('ok', tr('计划已开始执行', 'Plan started'));
      await loadWire();
    } catch (e) {
      flash('err', errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const doPause = async () => {
    if (!planUid) return;
    setBusy(true);
    try {
      await pausePlan(planUid);
      flash('ok', tr('已请求暂停', 'Pause requested'));
      await loadWire();
    } catch (e) {
      flash('err', errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const onDrop = (to: number) => {
    const from = dragFrom.current;
    dragFrom.current = null;
    if (from === null || from === to || !editSteps) return;
    const next = [...editSteps];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    setEditSteps(next);
  };

  return (
    <div
      className={cn(
        'w-full overflow-hidden rounded-lg border bg-zinc-900/40 text-xs',
        counts.failed > 0 ? 'border-danger/30' : 'border-zinc-800/60',
      )}
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={() => {
          setOpen((v) => !v);
          // 展开即重拉快照：执行/重试后的状态与结论不靠订阅也能对齐
          void loadWire();
        }}
        aria-label={tr(`执行计划 ${counts.total} 步`, `Plan ${counts.total} steps`)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-zinc-300 hover:bg-zinc-800/40"
      >
        {open ? (
          <ChevronDown size={13} className="shrink-0 text-zinc-500" />
        ) : (
          <ChevronRight size={13} className="shrink-0 text-zinc-500" />
        )}
        <span className="font-medium text-zinc-200">
          {tr(`执行计划 · ${counts.total} 步`, `Plan · ${counts.total} steps`)}
        </span>
        <StatusPill tone={meta.tone} label={tr(meta.zh, meta.en)} pulse={meta.pulse} />
        <span className="ml-auto flex items-center gap-1.5 text-[10px] tabular-nums text-zinc-500">
          <span>
            {counts.succeeded} {tr('完成', 'done')}
          </span>
          {counts.skipped > 0 && (
            <>
              <span>·</span>
              <span>
                {counts.skipped} {tr('跳过', 'skipped')}
              </span>
            </>
          )}
          {counts.running > 0 && (
            <>
              <span>·</span>
              <span>
                {counts.running} {tr('运行中', 'running')}
              </span>
            </>
          )}
          {counts.failed > 0 && (
            <>
              <span>·</span>
              <span className="text-danger">
                {counts.failed} {tr('失败', 'failed')}
              </span>
            </>
          )}
          {usedTokens !== null && (
            <>
              <span>·</span>
              <span>{(usedTokens / 1000).toFixed(1)}k tok</span>
            </>
          )}
        </span>
      </button>
      {(result.message ?? null) !== null && (
        <div className="border-t border-zinc-800/60 bg-zinc-950/40 px-3 py-1.5 text-[10.5px] leading-4 text-zinc-400">
          {result.message}
        </div>
      )}
      {open && editSteps === null && (
        <div className="border-t border-zinc-800/60 bg-zinc-950/40 px-2 py-1.5">
          {steps.map((s, i) => {
            const sMeta = s.status ? STEP_STATUS_META[s.status] : null;
            return (
              <div key={s.key} className="flex items-start gap-2 rounded-md px-1.5 py-1 hover:bg-zinc-800/40">
                <span className="mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded bg-zinc-800/60 text-[9px] font-medium text-zinc-400">
                  {i + 1}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[11px] font-medium text-zinc-200">{s.name}</span>
                  {s.instruction && (
                    <span className="mt-0.5 block truncate text-[10px] text-zinc-500">{s.instruction}</span>
                  )}
                  {s.summary && (
                    <span
                      className={cn(
                        'mt-0.5 block truncate text-[10.5px]',
                        s.status === 'failed' ? 'text-danger' : 'text-zinc-400',
                      )}
                    >
                      {s.summary}
                    </span>
                  )}
                  {s.error && !s.summary && (
                    <span className="mt-0.5 block truncate text-[10.5px] text-danger">{s.error}</span>
                  )}
                </span>
                {s.tools.length > 0 && (
                  <span className="hidden shrink-0 gap-1 sm:flex">
                    {s.tools.slice(0, 2).map((t) => (
                      <span key={t} className="rounded-full border border-zinc-700 px-1.5 text-[9.5px] leading-4 text-zinc-400">
                        {t}
                      </span>
                    ))}
                  </span>
                )}
                <span className="flex shrink-0 flex-col items-end gap-1">
                  {sMeta ? (
                    <StatusPill tone={sMeta.tone} label={tr(sMeta.zh, sMeta.en)} pulse={sMeta.pulse} />
                  ) : (
                    <span className="inline-flex h-[18px] items-center rounded-full border border-zinc-700 bg-zinc-800/40 px-1.5 text-[11px] leading-4 text-zinc-400">
                      {tr('排队中', 'Queued')}
                    </span>
                  )}
                  {s.duration_ms > 0 && (
                    <span className="text-[9.5px] tabular-nums text-zinc-500">{fmtDuration(s.duration_ms)}</span>
                  )}
                </span>
              </div>
            );
          })}
          {/* 底部动作按状态切换：待确认可改可执行，执行中可暂停 */}
          <div className="mt-1.5 flex items-center gap-2 border-t border-zinc-800/60 px-1.5 pt-1.5">
            <span className="min-w-0 flex-1 truncate text-[10px] text-zinc-500">
              {isDraft
                ? tr('确认后按步骤执行；可在右栏暂停、单步重试或调整顺序', 'Confirm to run step by step; pause, retry or reorder in the workbench')
                : (wire?.title ?? args.title ?? '')}
            </span>
            {interactive && isDraft && (
              <>
                <button
                  type="button"
                  onClick={enterEdit}
                  className="shrink-0 rounded-md border border-zinc-700 px-2 py-1 text-[10.5px] text-zinc-300 hover:bg-zinc-800/60"
                >
                  {tr('调整计划', 'Edit plan')}
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void doStart()}
                  className="shrink-0 rounded-md bg-indigo-500 px-2 py-1 text-[10.5px] font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
                >
                  {busy ? tr('执行中…', 'Starting…') : tr('开始执行', 'Start')}
                </button>
              </>
            )}
            {interactive && isRunning && (
              <button
                type="button"
                disabled={busy}
                onClick={() => void doPause()}
                className="flex shrink-0 items-center gap-1 rounded-md border border-zinc-700 px-2 py-1 text-[10.5px] text-zinc-300 hover:bg-zinc-800/60 disabled:opacity-50"
              >
                <Pause size={10} />
                {tr('暂停', 'Pause')}
              </button>
            )}
            {interactive && isPaused && (
              <button
                type="button"
                disabled={busy}
                onClick={() => void doStart()}
                className="flex shrink-0 items-center gap-1 rounded-md bg-indigo-500 px-2 py-1 text-[10.5px] font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
              >
                <Play size={10} />
                {tr('继续执行', 'Resume')}
              </button>
            )}
            {onLocatePlan && planUid && (
              <RowActionIcon
                icon={MapPin}
                label={tr('在执行进度中定位', 'Locate in plan workbench')}
                onClick={() => onLocatePlan({ kind: 'step', planUid, stepUid: steps.find((s) => s.status === 'running')?.key ?? steps[0]?.key ?? '' })}
              />
            )}
          </div>
        </div>
      )}
      {open && editSteps !== null && (
        <div className="border-t border-zinc-800/60 bg-zinc-950/40 px-2 py-1.5">
          {editSteps.map((s, i) => (
            <div
              key={s.key}
              draggable
              onDragStart={() => {
                dragFrom.current = i;
              }}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => onDrop(i)}
              className="flex items-start gap-1.5 rounded-md border border-zinc-800/60 px-1.5 py-1.5"
            >
              <span className="mt-1 cursor-grab text-zinc-600" aria-hidden>
                <GripVertical size={12} />
              </span>
              <span className="mt-1 grid h-4 w-4 shrink-0 place-items-center rounded bg-zinc-800/60 text-[9px] font-medium text-zinc-400">
                {i + 1}
              </span>
              <span className="flex min-w-0 flex-1 flex-col gap-1">
                <input
                  value={s.name}
                  onChange={(e) =>
                    setEditSteps((prev) => prev!.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))
                  }
                  placeholder={tr('步骤名称', 'Step name')}
                  className="w-full rounded-md border border-zinc-800 bg-zinc-900/60 px-1.5 py-1 text-[11px] text-zinc-200 outline-none focus:border-indigo-500/60"
                />
                <input
                  value={s.instruction}
                  onChange={(e) =>
                    setEditSteps((prev) => prev!.map((x, j) => (j === i ? { ...x, instruction: e.target.value } : x)))
                  }
                  placeholder={tr('本步要求（可选）', 'Instruction (optional)')}
                  className="w-full rounded-md border border-zinc-800 bg-zinc-900/60 px-1.5 py-1 text-[10.5px] text-zinc-300 outline-none focus:border-indigo-500/60"
                />
                <input
                  value={s.tools}
                  onChange={(e) =>
                    setEditSteps((prev) => prev!.map((x, j) => (j === i ? { ...x, tools: e.target.value } : x)))
                  }
                  placeholder={tr('预估工具，逗号分隔（可选）', 'Tools, comma separated (optional)')}
                  className="w-full rounded-md border border-zinc-800 bg-zinc-900/60 px-1.5 py-1 text-[10px] text-zinc-400 outline-none focus:border-indigo-500/60"
                />
              </span>
              <RowActionIcon
                icon={Trash2}
                label={tr('删除该步骤', 'Delete step')}
                onClick={() => setEditSteps((prev) => prev!.filter((_, j) => j !== i))}
              />
            </div>
          ))}
          <div className="mt-1.5 flex items-center gap-2 border-t border-zinc-800/60 px-1.5 pt-1.5">
            <button
              type="button"
              disabled={editSteps.length >= 12}
              onClick={() =>
                setEditSteps((prev) => [
                  ...prev!,
                  { key: `new-${Date.now()}`, name: '', instruction: '', tools: '' },
                ])
              }
              className="flex shrink-0 items-center gap-1 rounded-md border border-zinc-700 px-2 py-1 text-[10.5px] text-zinc-300 hover:bg-zinc-800/60 disabled:opacity-50"
            >
              <Plus size={10} />
              {tr('添加步骤', 'Add step')}
            </button>
            <span className="min-w-0 flex-1 truncate text-[10px] text-zinc-500">
              {tr('按住手柄拖拽排序 · 最多 12 步', 'Drag the handle to reorder · up to 12 steps')}
            </span>
            <button
              type="button"
              disabled={busy}
              onClick={() => setEditSteps(null)}
              className="shrink-0 rounded-md border border-zinc-700 px-2 py-1 text-[10.5px] text-zinc-300 hover:bg-zinc-800/60 disabled:opacity-50"
            >
              {tr('取消', 'Cancel')}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void saveEdit(true)}
              className="shrink-0 rounded-md bg-indigo-500 px-2 py-1 text-[10.5px] font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
            >
              {busy ? tr('保存中…', 'Saving…') : tr('保存并执行', 'Save & run')}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
