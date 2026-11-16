// 计划与步骤状态的唯一口径：文案、tone、脉冲都在这里定，
// 对话计划卡与右栏执行进度都从这里取，避免同一个状态两种说法。

import type { PlanStatus, StepStatus } from '@/api/plan';
import type { StatusTone } from '@/components/ui/StatusPill';

export type PlanStatusMeta = {
  tone: StatusTone;
  zh: string;
  en: string;
  /** 进行中的状态要脉冲提示。 */
  pulse?: boolean;
};

export const PLAN_STATUS_META: Record<PlanStatus, PlanStatusMeta> = {
  draft: { tone: 'warn', zh: '待确认', en: 'Draft', pulse: true },
  running: { tone: 'info', zh: '进行中', en: 'Running', pulse: true },
  paused: { tone: 'warn', zh: '已暂停', en: 'Paused' },
  succeeded: { tone: 'ok', zh: '完成', en: 'Succeeded' },
  stopped: { tone: 'neutral', zh: '已停止', en: 'Stopped' },
};

export const STEP_STATUS_META: Record<StepStatus, PlanStatusMeta> = {
  pending: { tone: 'neutral', zh: '排队中', en: 'Pending' },
  queued: { tone: 'neutral', zh: '排队中', en: 'Queued' },
  running: { tone: 'info', zh: '运行中', en: 'Running', pulse: true },
  succeeded: { tone: 'ok', zh: '完成', en: 'Succeeded' },
  failed: { tone: 'danger', zh: '失败', en: 'Failed' },
  skipped: { tone: 'warn', zh: '已跳过', en: 'Skipped' },
  stopped: { tone: 'neutral', zh: '已停止', en: 'Stopped' },
};
