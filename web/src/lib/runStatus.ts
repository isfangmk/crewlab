// 运行状态的唯一口径：文案、配色、圆点颜色都在这里定，
// 列表胶囊、画布圆点、详情页头部都从这里取，避免同一个状态三种说法。

import type { NodeRunStatus, RunStatus } from '@/api/workflows';
import type { StatusTone } from '@/components/ui/StatusPill';

export type RunStatusMeta = {
  tone: StatusTone;
  zh: string;
  en: string;
  /** 进行中的状态要脉冲提示。 */
  pulse?: boolean;
  /** 导航圆点的背景色。 */
  dot: string;
};

export const RUN_STATUS_META: Record<RunStatus, RunStatusMeta> = {
  running: { tone: 'info', zh: '运行中', en: 'Running', pulse: true, dot: 'bg-info' },
  waiting: { tone: 'warn', zh: '等待人工', en: 'Waiting', pulse: true, dot: 'bg-warn' },
  succeeded: { tone: 'ok', zh: '成功', en: 'Succeeded', dot: 'bg-ok' },
  failed: { tone: 'danger', zh: '失败', en: 'Failed', dot: 'bg-danger' },
  stopped: { tone: 'neutral', zh: '已停止', en: 'Stopped', dot: 'bg-zinc-500' },
};

/** 节点执行状态：与运行状态同名的那部分保持一致。 */
export const NODE_STATUS_META: Record<NodeRunStatus, RunStatusMeta> = {
  succeeded: RUN_STATUS_META.succeeded,
  failed: RUN_STATUS_META.failed,
};

/** 未执行的节点用中性灰，与「已停止」区分开。 */
export const NOT_RUN_META: RunStatusMeta = {
  tone: 'neutral',
  zh: '未执行',
  en: 'Not run',
  dot: 'bg-zinc-600',
};

/** 触发方式的文案：manual 是正式运行，debug 是画布上的单节点试跑。 */
export function triggerText(trigger: string, tr: (zh: string, en: string) => string): string {
  if (trigger === 'manual') return tr('手动', 'Manual');
  if (trigger === 'debug') return tr('节点试跑', 'Node test');
  return trigger;
}
