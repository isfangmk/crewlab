// 任务图状态语义：文案/色调/脉冲的唯一来源，对话卡与工作台共用。

import type { StatusTone } from '@/components/ui';
import type { GraphStatus, NodeStatus } from '@/api/taskGraph';

export type GraphStatusMeta = { tone: StatusTone; zh: string; en: string; pulse?: boolean };

export const GRAPH_STATUS_META: Record<GraphStatus, GraphStatusMeta> = {
  draft: { tone: 'warn', zh: '待确认', en: 'Draft', pulse: true },
  running: { tone: 'info', zh: '进行中', en: 'Running', pulse: true },
  paused: { tone: 'warn', zh: '已暂停', en: 'Paused' },
  succeeded: { tone: 'ok', zh: '完成', en: 'Succeeded' },
  failed: { tone: 'danger', zh: '失败', en: 'Failed' },
  stopped: { tone: 'neutral', zh: '已停止', en: 'Stopped' },
};

export const NODE_STATUS_META: Record<NodeStatus, GraphStatusMeta> = {
  pending: { tone: 'neutral', zh: '排队中', en: 'Pending' },
  queued: { tone: 'neutral', zh: '排队中', en: 'Queued' },
  running: { tone: 'info', zh: '运行中', en: 'Running', pulse: true },
  succeeded: { tone: 'ok', zh: '完成', en: 'Succeeded' },
  failed: { tone: 'danger', zh: '失败', en: 'Failed' },
  skipped: { tone: 'warn', zh: '已跳过', en: 'Skipped' },
  stopped: { tone: 'neutral', zh: '已停止', en: 'Stopped' },
};
