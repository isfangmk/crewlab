import type { StatusTone } from '@/components/ui';
import type { EvalCaseStatus, EvalRunStatus } from '@/api/evals';

type Meta = { zh: string; en: string; tone: StatusTone };

export const RUN_STATUS_META: Record<EvalRunStatus, Meta> = {
  running: { zh: '进行中', en: 'Running', tone: 'info' },
  completed: { zh: '已完成', en: 'Completed', tone: 'ok' },
  failed: { zh: '失败', en: 'Failed', tone: 'danger' },
};

export const CASE_STATUS_META: Record<EvalCaseStatus, Meta> = {
  passed: { zh: '通过', en: 'Passed', tone: 'ok' },
  failed: { zh: '未通过', en: 'Failed', tone: 'danger' },
  error: { zh: '执行失败', en: 'Error', tone: 'warn' },
};

export const CASE_MISSING_META: Meta = {
  zh: '缺少结果',
  en: 'Missing',
  tone: 'neutral',
};

export function caseStatusMeta(status: EvalCaseStatus | 'missing'): Meta {
  return status === 'missing' ? CASE_MISSING_META : CASE_STATUS_META[status];
}
