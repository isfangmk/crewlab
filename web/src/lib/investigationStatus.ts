// 排查状态的唯一口径：文案、tone、脉冲都在这里定，
// 对话折叠块与右栏工作台都从这里取，避免同一个状态两种说法。

import type { DispatchStatus, WorkerStatus } from '@/api/investigation';
import type { StatusTone } from '@/components/ui/StatusPill';

export type InvestigationStatusMeta = {
  tone: StatusTone;
  zh: string;
  en: string;
  /** 进行中的状态要脉冲提示。 */
  pulse?: boolean;
};

export const DISPATCH_STATUS_META: Record<DispatchStatus, InvestigationStatusMeta> = {
  running: { tone: 'info', zh: '进行中', en: 'Running', pulse: true },
  succeeded: { tone: 'ok', zh: '完成', en: 'Succeeded' },
  failed: { tone: 'danger', zh: '失败', en: 'Failed' },
  stopped: { tone: 'neutral', zh: '已停止', en: 'Stopped' },
};

export const WORKER_STATUS_META: Record<WorkerStatus, InvestigationStatusMeta> = {
  queued: { tone: 'neutral', zh: '排队中', en: 'Queued' },
  running: { tone: 'info', zh: '运行中', en: 'Running', pulse: true },
  succeeded: { tone: 'ok', zh: '完成', en: 'Succeeded' },
  failed: { tone: 'danger', zh: '失败', en: 'Failed' },
  stopped: { tone: 'neutral', zh: '已停止', en: 'Stopped' },
};
