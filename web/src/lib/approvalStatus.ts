// 审批状态的唯一口径：文案、语义色、图标都从这里取。
//
// 列表、详情、会话内工具卡曾经各写一份映射，同一个状态三种说法
// （等待确认 / 待确认 / Pending），这里统一后各处只引用这一张表。

import {
  BadgeCheck,
  CircleAlert,
  CircleSlash,
  Clock,
  PlayCircle,
  TimerOff,
  type LucideIcon,
} from 'lucide-react';
import type { ApprovalStatus } from '@/api/approvals';
import type { StatusTone } from '@/components/ui/StatusPill';

export type ApprovalStatusMeta = {
  tone: StatusTone;
  zh: string;
  en: string;
  /** 指标卡与列表行用的状态图标。 */
  icon: LucideIcon;
};

export const APPROVAL_STATUS_META: Record<ApprovalStatus, ApprovalStatusMeta> = {
  pending: { tone: 'warn', zh: '待确认', en: 'Pending', icon: Clock },
  approved: { tone: 'ok', zh: '已批准', en: 'Approved', icon: BadgeCheck },
  executed: { tone: 'ok', zh: '已执行', en: 'Executed', icon: PlayCircle },
  rejected: { tone: 'neutral', zh: '已拒绝', en: 'Rejected', icon: CircleSlash },
  failed: { tone: 'danger', zh: '执行失败', en: 'Failed', icon: CircleAlert },
  expired: { tone: 'neutral', zh: '已失效', en: 'Expired', icon: TimerOff },
};

/** 状态清单：筛选栏与后端契约保持同一顺序。 */
export const APPROVAL_STATUSES = Object.keys(APPROVAL_STATUS_META) as ApprovalStatus[];

/** 列表/详情共用的状态标签。 */
export function approvalStatusLabel(
  status: ApprovalStatus,
  tr: (zh: string, en: string) => string,
): string {
  const meta = APPROVAL_STATUS_META[status];
  return tr(meta.zh, meta.en);
}
