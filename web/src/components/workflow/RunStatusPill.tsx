// 运行状态胶囊：列表与运行详情共用一套文案/配色。

import type { RunStatus } from '@/api/workflows';
import { StatusPill } from '@/components/ui';
import type { StatusTone } from '@/components/ui/StatusPill';
import { useI18n } from '@/i18n/locale';

const META: Record<RunStatus, { tone: StatusTone; zh: string; en: string; pulse?: boolean }> = {
  running: { tone: 'info', zh: '运行中', en: 'Running', pulse: true },
  succeeded: { tone: 'ok', zh: '成功', en: 'Succeeded' },
  failed: { tone: 'danger', zh: '失败', en: 'Failed' },
  waiting: { tone: 'warn', zh: '等待人工', en: 'Waiting' },
};

export function RunStatusPill({ status }: { status: RunStatus }) {
  const { tr } = useI18n();
  const meta = META[status];
  return <StatusPill tone={meta.tone} label={tr(meta.zh, meta.en)} pulse={meta.pulse} />;
}

/**
 * 草稿标记：同版本号既可能是已发布画布，也可能是没发布的改动，列表与详情都要标出来。
 *
 * 用和状态胶囊同一套 11px 实色文字 + 完整边框；之前是 10px 半透明琥珀色压在深色
 * 底上，小到看不清，也没法跟「等待人工」的琥珀色区分。
 */
export function DraftTag({ className }: { className?: string }) {
  const { tr } = useI18n();
  return <StatusPill tone="neutral" label={tr('草稿', 'Draft')} className={className} />;
}

/** 耗时展示：小于 1 秒给毫秒，否则保留一位小数的秒。 */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}
