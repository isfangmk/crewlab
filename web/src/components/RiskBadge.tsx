import { StatusPill } from '@/components/ui';
import type { ToolRisk } from '@/api/tools';
import { useI18n } from '@/i18n/locale';

/** 工具风险标签：安全 / 需审批。内置与 MCP 工具共用；颜色遵守语义表（ok=安全 / warn=需审批）。 */
export function RiskBadge({ value }: { value: ToolRisk }) {
  const { tr } = useI18n();
  if (value === 'safe') {
    return <StatusPill tone="ok" label={tr('安全', 'safe')} />;
  }
  if (value === 'approval_required') {
    return <StatusPill tone="warn" label={tr('需审批', 'approval')} />;
  }
  throw new Error(`未知 Tool risk: ${value}`);
}
