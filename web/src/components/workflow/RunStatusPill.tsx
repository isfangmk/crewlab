// 运行状态胶囊：列表与运行详情共用一套文案/配色。

import type { RunStatus } from '@/api/workflows';
import { StatusPill } from '@/components/ui';
import { RUN_STATUS_META } from '@/lib/runStatus';
import { useI18n } from '@/i18n/locale';

export function RunStatusPill({ status }: { status: RunStatus }) {
  const { tr } = useI18n();
  const meta = RUN_STATUS_META[status];
  return <StatusPill tone={meta.tone} label={tr(meta.zh, meta.en)} pulse={meta.pulse} />
}

/**
 * 草稿标记：同版本号既可能是已发布画布，也可能是没发布的改动，列表与详情都要标出来。
 *
 * 用和状态胶囊同一套 11px 实色文字 + 完整边框，保证与「等待人工」等状态一眼可区分。
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
