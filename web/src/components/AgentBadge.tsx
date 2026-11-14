import { Hint } from '@/components/ui/Tooltip';
// 会话绑定助理的徽标；优先展示后端中文名，缺省回退内置短名或 ADK 标识。
import { Bot } from 'lucide-react';
import { cn } from '@/lib/cn';
import { tr as trInline, useI18n } from '@/i18n/locale';

export type AgentBadgeSize = 'xs' | 'sm';

const AGENT_LABELS_ZH: Record<string, string> = {
  default: '默认助理',
  'incident-investigator': '故障诊断',
  'specialist-sre': 'SRE 专家',
  'specialist-ops': '运维专家',
  'specialist-compute': '计算专家',
  'specialist-network': '网络专家',
  'specialist-disk': '磁盘专家',
  reviewer: '审核员',
};
const AGENT_LABELS_EN: Record<string, string> = {
  default: 'Default',
  'incident-investigator': 'Incident investigator',
  'specialist-sre': 'SRE specialist',
  'specialist-ops': 'Ops specialist',
  'specialist-compute': 'Compute specialist',
  'specialist-network': 'Network specialist',
  'specialist-disk': 'Disk specialist',
  reviewer: 'Reviewer',
};

export function AgentBadge({
  agentId,
  label,
  size = 'xs',
  className,
}: {
  agentId?: string | null;
  /** 后端配置的中文名；缺省时退回内置映射或 ADK 标识 */
  label?: string | null;
  size?: AgentBadgeSize;
  className?: string;
}) {
  const { tr } = useI18n();
  if (!agentId) return null;
  const zh = AGENT_LABELS_ZH[agentId];
  const fallback = zh ? trInline(zh, AGENT_LABELS_EN[agentId] ?? zh) : agentId;
  const display = label?.trim() || fallback;
  const base =
    'inline-flex items-center gap-1 rounded-md border border-indigo-500/40 bg-indigo-500/10 text-indigo-200 ring-1 ring-inset ring-indigo-500/20';
  const sizeCls =
    size === 'sm'
      ? 'px-1.5 py-0.5 text-[11px]'
      : 'px-1 py-0.5 text-[10px]';
  const iconSize = size === 'sm' ? 11 : 9;
  return (
    <Hint content={tr(`此会话固定使用 ${display}（${agentId}）`, `This session is pinned to ${display} (${agentId})`)}><span

      className={cn(base, sizeCls, className)}
    >
      <Bot size={iconSize} />
      {display}
    </span></Hint>
  );
}
