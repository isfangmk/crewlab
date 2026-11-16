import { StatusPill } from '@/components/ui';
import type { ToolRisk } from '@/api/tools';
import { useI18n } from '@/i18n/locale';

/** 工具风险标签：三级危险度（safe 只读无副作用 / mutating 改变状态 / dangerous 高风险破坏性）。
 * 内置、MCP 与技能共用同一词汇与中文映射；颜色遵守语义表（ok=安全 / warn=变更 / danger=危险）。 */
export function RiskBadge({ value }: { value: ToolRisk }) {
  const { tr } = useI18n();
  if (value === 'safe') {
    return <StatusPill tone="ok" label={tr('安全', 'safe')} />;
  }
  if (value === 'mutating') {
    return <StatusPill tone="warn" label={tr('变更', 'mutating')} />;
  }
  if (value === 'dangerous') {
    return <StatusPill tone="danger" label={tr('危险', 'dangerous')} />;
  }
  throw new Error(`未知 Tool risk: ${value}`);
}
