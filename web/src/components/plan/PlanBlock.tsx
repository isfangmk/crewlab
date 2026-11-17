// 对话内的「执行计划 · N 步」卡片：create_plan 工具卡的专属渲染（设计稿
// parallel-serial-ui 对话内卡片形态）。头部 = 折叠箭头 + 模式图标 + 状态 pill
// （第 k 步执行中）+ 全程计时；分段进度条每步一段；行列表 = 编号徽 + 名称 +
// 心跳/结论 + 计时；脚注 = 形态说明 + 最近动作心跳。待确认时保留执行前动作
// （调整计划/开始执行/保存并执行）；执行全程零干预按钮。
import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, GripVertical, ListChecks, Plus, Trash2 } from 'lucide-react';
import {
  getPlan,
  startPlan,
  subscribePlanEvents,
  updatePlanSteps,
  type PlanExecution,
  type StepStatus,
} from '@/api/plan';
import { PLAN_STATUS_META, STEP_STATUS_META } from '@/lib/planStatus';
import { fmtAgo, fmtDuration, isStale, useTicker } from '@/lib/runTimer';
import { relaySummaryDelta, requestChatRefetch } from '@/lib/chatLiveBus';
import { useExecLock } from '@/lib/useExecLock';
import { errorText } from '@/lib/errors';
import { flash } from '@/components/ui';
import { useI18n } from '@/i18n/locale';
import { cn } from '@/lib/cn';
import { StatusPill } from '@/components/ui';

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

export function PlanBlock({
  call,
  pending,
  interactive: interactiveProp,
}: {
  call: {
    name: string;
    status?: string;
    arguments?: unknown;
    result?: unknown;
    duration_ms?: number;
  };
  /** 回合未收口：折叠块只展示进行态，交互禁用 */
  pending?: boolean;
  /** 非只读账号才可交互：草稿态显示执行前动作（调整计划/开始执行） */
  interactive?: boolean;
}) {
  const { tr } = useI18n();
  const [open, setOpen] = useState(true);
  const [wire, setWire] = useState<PlanExecution | null>(null);
  const [editSteps, setEditSteps] = useState<EditStep[] | null>(null);
  const [busy, setBusy] = useState(false);
  // 当前步最近动作时刻：心跳与停滞判断
  const [beat, setBeat] = useState<{ at: number; tool: string } | null>(null);
  // 拖拽排序：记录按下时的行号，落点行号决定插入位置
  const dragFrom = useRef<number | null>(null);

  const args = (call.arguments ?? {}) as { title?: string; question?: string; steps_json?: string };
  const result = (call.result ?? {}) as PlanToolResult;
  const planUid = result.plan_uid ?? wire?.plan_uid ?? null;
  const interactive = interactiveProp && !pending;

  const statusKey = (wire?.status ?? result.status ?? 'draft') as keyof typeof PLAN_STATUS_META;
  const active = statusKey === 'running';
  useExecLock(planUid, active);
  const tick = useTicker(active);

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

  // 未终态时订阅事件流：心跳就地记录，收口即对齐快照并刷新消息
  useEffect(() => {
    if (!planUid) return;
    if (!active && wire !== null) return;
    if (pending) return;
    const ctrl = new AbortController();
    void subscribePlanEvents(
      planUid,
      (ev) => {
        if (ev.event === 'summary_delta') {
          relaySummaryDelta(ev.message_id, ev.delta, ev.partial);
          return;
        }
        if (ev.event === 'step_log') {
          setBeat({ at: Date.now(), tool: ev.tool });
          return;
        }
        if (ev.event === 'plan_finished') {
          void loadWire();
          // 自动汇总已随终态落库：拉进消息列表
          if (ev.summary_message_id) requestChatRefetch();
          return;
        }
        void loadWire();
      },
      ctrl.signal,
    ).catch(() => {});
    return () => ctrl.abort();
  }, [planUid, active, wire !== null, pending, loadWire]);

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
        started_at: null as string | null,
        finished_at: null as string | null,
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
      started_at: s.started_at,
      finished_at: s.finished_at,
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
      started_at: null as string | null,
      finished_at: null as string | null,
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

  const meta = PLAN_STATUS_META[statusKey] ?? PLAN_STATUS_META.draft;
  const isDraft = statusKey === 'draft';
  const isPaused = statusKey === 'paused';
  const runningIndex = steps.findIndex((s) => s.status === 'running');
  // 状态 pill：执行中显示「第 k 步执行中」，其余用状态文案
  const pillLabel =
    active && runningIndex >= 0
      ? tr(`第 ${runningIndex + 1} 步执行中`, `step ${runningIndex + 1} running`)
      : tr(meta.zh, meta.en);
  // 全程计时：收口后定格总用时，运行中从计划开始时刻读秒
  const totalMs =
    wire?.finished_at && wire.started_at
      ? new Date(wire.finished_at).getTime() - new Date(wire.started_at).getTime()
      : wire?.duration_ms && wire.duration_ms > 0
        ? wire.duration_ms
        : active && wire?.started_at
          ? tick - new Date(wire.started_at).getTime()
          : 0;

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
        'w-full overflow-hidden rounded-[13px] border bg-zinc-900/40',
        counts.failed > 0 ? 'border-danger/30' : 'border-zinc-800/60',
      )}
    >
      {/* c-head：折叠 + 模式图标 + 名称 + 状态 pill + 全程计时 */}
      <button
        type="button"
        aria-expanded={open}
        onClick={() => {
          setOpen((v) => !v);
          // 展开即重拉快照：执行/重试后的状态与结论不靠订阅也能对齐
          void loadWire();
        }}
        aria-label={tr(`执行计划 ${counts.total} 步`, `Plan ${counts.total} steps`)}
        className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left hover:bg-zinc-800/30"
      >
        {open ? (
          <ChevronDown size={12} className="shrink-0 text-zinc-500" />
        ) : (
          <ChevronRight size={12} className="shrink-0 text-zinc-500" />
        )}
        <span className="grid h-6 w-6 shrink-0 place-items-center rounded-lg bg-accent/12 text-accent">
          <ListChecks size={12} />
        </span>
        <span className="text-[12px] font-semibold text-zinc-100">
          {tr(`执行计划 · ${counts.total} 步`, `Plan · ${counts.total} steps`)}
        </span>
        <StatusPill tone={meta.tone} label={pillLabel} pulse={meta.pulse} />
        <span className="ml-auto shrink-0 text-right">
          {totalMs > 0 && (
            <>
              <span
                className={cn(
                  'block text-[11px] font-semibold tabular-nums',
                  active ? 'text-accent' : 'text-zinc-300',
                )}
              >
                {fmtDuration(totalMs)}
              </span>
              <span className="block text-[8.5px] tracking-[0.1em] text-zinc-600">
                {tr(active ? '已运行' : '用时', active ? 'elapsed' : 'took')}
              </span>
            </>
          )}
        </span>
      </button>
      {open && (
        <>
          {/* c-strip 分段进度条：每步一段 */}
          <div className="flex gap-1.5 px-3.5 pb-2.5">
            {steps.map((s) => (
              <div
                key={s.key}
                className={cn(
                  'relative h-[5px] flex-1 overflow-hidden rounded-full',
                  s.status === 'succeeded' && 'bg-ok/20',
                  s.status === 'skipped' && 'bg-warn/20',
                  s.status === 'failed' && 'bg-danger/20',
                  s.status === 'running' && 'bg-accent/15',
                  (s.status === null || s.status === 'pending' || s.status === 'queued' || s.status === 'stopped') &&
                    'bg-zinc-800',
                )}
              >
                {s.status === 'running' && (
                  <div className="absolute inset-0 rounded-full bg-accent/50" />
                )}
              </div>
            ))}
          </div>
          {/* c-body 行列表：流水线缩影（编号徽 + 名称 + 心跳/结论 + 计时） */}
          <div className="flex flex-col gap-1 border-t border-zinc-800/60 bg-zinc-950/40 px-3.5 py-2.5">
            {steps.map((s, i) => {
              const isRunning = s.status === 'running';
              const startMs = s.started_at ? new Date(s.started_at).getTime() : null;
              const elapsedMs = isRunning && startMs ? tick - startMs : s.duration_ms;
              const stale = isRunning && isStale(beat?.at ?? null, tick);
              const sMeta = s.status ? STEP_STATUS_META[s.status] : null;
              return (
                <div key={s.key} className="flex items-center gap-2">
                  <span
                    className={cn(
                      'grid h-[18px] w-[18px] shrink-0 place-items-center rounded-md text-[9px] font-bold',
                      s.status === 'succeeded' && 'bg-ok/15 text-ok',
                      s.status === 'skipped' && 'bg-warn/15 text-warn',
                      isRunning && 'bg-accent text-accent-fg',
                      s.status === 'failed' && 'bg-danger/15 text-danger',
                      (s.status === null || s.status === 'pending' || s.status === 'queued' || s.status === 'stopped') &&
                        'bg-zinc-800/60 text-zinc-500',
                    )}
                  >
                    {s.status === 'succeeded' ? '✓' : s.status === 'skipped' ? '↷' : s.status === 'failed' ? '✕' : i + 1}
                  </span>
                  <span
                    className={cn(
                      'min-w-0 flex-1 truncate text-[11px]',
                      isRunning ? 'font-medium text-zinc-200' : 'text-zinc-400',
                    )}
                    title={sMeta ? `${s.name} · ${tr(sMeta.zh, sMeta.en)}` : s.name}
                  >
                    {isRunning
                      ? stale
                        ? tr(`${s.name} · 疑似停滞`, `${s.name} · possibly stalled`)
                        : beat
                          ? tr(
                              `${s.name} · 正在调用 ${beat.tool || '工具'}`,
                              `${s.name} · calling ${beat.tool || 'tool'}`,
                            )
                          : tr(`${s.name} · 等待工具响应…`, `${s.name} · waiting…`)
                      : s.status === 'failed' && s.error
                        ? `${s.name} · ${s.error}`
                        : s.summary
                          ? `${s.name} · ${s.summary}`
                          : `${s.name}${s.instruction ? ` · ${s.instruction}` : ''}`}
                  </span>
                  <span
                    className={cn(
                      'shrink-0 text-[10px] tabular-nums',
                      isRunning ? 'font-semibold text-accent' : 'text-zinc-500',
                    )}
                  >
                    {isRunning || s.duration_ms > 0
                      ? fmtDuration(elapsedMs)
                      : (s.status === 'pending' || s.status === 'queued' || s.status === null)
                        ? tr('排队中', 'Queued')
                        : ''}
                  </span>
                </div>
              );
            })}
          </div>
          {/* 草稿动作条：执行前动作（开始执行/调整计划）；回合未收口禁点，执行全程零干预 */}
          {editSteps === null && (isDraft || isPaused) && (
            <div className="flex items-center gap-2 border-t border-zinc-800/60 px-3.5 py-2">
              <span className="min-w-0 flex-1 truncate text-[10px] text-zinc-500">
                {pending
                  ? tr('模型正在输出，结束后可执行', 'Available when the reply finishes')
                  : isDraft
                    ? tr('确认后按步骤执行；执行全程可看实时进度', 'Confirm to run step by step; watch live progress')
                    : tr('已暂停，可继续执行', 'Paused; resume to continue')}
              </span>
              {isDraft && (
                <button
                  type="button"
                  disabled={!interactive || busy}
                  onClick={enterEdit}
                  className="shrink-0 rounded-md border border-zinc-700 px-2 py-1 text-[10.5px] text-zinc-300 hover:bg-zinc-800/60 disabled:opacity-50"
                >
                  {tr('调整计划', 'Edit plan')}
                </button>
              )}
              <button
                type="button"
                disabled={!interactive || busy}
                onClick={() => void doStart()}
                className="shrink-0 rounded-md bg-accent px-2 py-1 text-[10.5px] font-medium text-accent-fg hover:brightness-110 disabled:opacity-50"
              >
                {busy
                  ? tr(isPaused ? '恢复中…' : '执行中…', isPaused ? 'Resuming…' : 'Starting…')
                  : tr(isPaused ? '继续执行' : '开始执行', isPaused ? 'Resume' : 'Start')}
              </button>
            </div>
          )}
          {/* c-foot：形态说明 + 最近动作心跳 */}
          <div className="flex items-center gap-2 border-t border-zinc-800/60 px-3.5 py-2 text-[10px] text-zinc-500">
            <span>
              {tr(
                `流水线式：${counts.total} 步按序推进，只有一处在跑`,
                `Pipeline: ${counts.total} steps in order, one at a time`,
              )}
            </span>
            {active && (
              <span className="ml-auto tabular-nums">
                {beat
                  ? tr(`最近动作 ${fmtAgo(beat.at, tick)}`, `last activity ${fmtAgo(beat.at, tick)}`)
                  : tr('等待首个动作…', 'waiting for first activity…')}
              </span>
            )}
          </div>
        </>
      )}
      {/* 编辑态：拖拽排序/增删步骤（草稿专属） */}
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
                  className="w-full rounded-md border border-zinc-800 bg-zinc-900/60 px-1.5 py-1 text-[11px] text-zinc-200 outline-none focus:border-accent/60"
                />
                <input
                  value={s.instruction}
                  onChange={(e) =>
                    setEditSteps((prev) => prev!.map((x, j) => (j === i ? { ...x, instruction: e.target.value } : x)))
                  }
                  placeholder={tr('本步要求（可选）', 'Instruction (optional)')}
                  className="w-full rounded-md border border-zinc-800 bg-zinc-900/60 px-1.5 py-1 text-[10.5px] text-zinc-300 outline-none focus:border-accent/60"
                />
                <input
                  value={s.tools}
                  onChange={(e) =>
                    setEditSteps((prev) => prev!.map((x, j) => (j === i ? { ...x, tools: e.target.value } : x)))
                  }
                  placeholder={tr('所需工具，逗号分隔（可选）', 'Tools, comma separated (optional)')}
                  className="w-full rounded-md border border-zinc-800 bg-zinc-900/60 px-1.5 py-1 text-[10px] text-zinc-400 outline-none focus:border-accent/60"
                />
              </span>
              <button
                type="button"
                aria-label={tr('删除该步骤', 'Delete step')}
                onClick={() => setEditSteps((prev) => prev!.filter((_, j) => j !== i))}
                className="mt-1 grid h-6 w-6 shrink-0 place-items-center rounded-md text-zinc-500 hover:bg-zinc-800/60 hover:text-danger"
              >
                <Trash2 size={12} />
              </button>
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
              className="shrink-0 rounded-md bg-accent px-2 py-1 text-[10.5px] font-medium text-accent-fg hover:brightness-110 disabled:opacity-50"
            >
              {busy ? tr('保存中…', 'Saving…') : tr('保存并执行', 'Save & run')}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
