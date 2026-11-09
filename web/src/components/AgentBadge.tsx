import { Hint } from '@/components/ui/Tooltip';
// 会话绑定助理的徽标；空 agentId 不渲染，未知 id 显示原字符串。
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
  size = 'xs',
  className,
}: {
  agentId?: string | null;
  size?: AgentBadgeSize;
  className?: string;
}) {
  const { tr } = useI18n();
  if (!agentId) return null;
  const zh = AGENT_LABELS_ZH[agentId];
  const label = zh ? trInline(zh, AGENT_LABELS_EN[agentId] ?? zh) : agentId;
  const base =
    'inline-flex items-center gap-1 rounded-md border border-indigo-500/40 bg-indigo-500/10 text-indigo-200 ring-1 ring-inset ring-indigo-500/20';
  const sizeCls =
    size === 'sm'
      ? 'px-1.5 py-0.5 text-[11px]'
      : 'px-1 py-0.5 text-[10px]';
  const iconSize = size === 'sm' ? 11 : 9;
  return (
    <Hint content={tr(`此会话固定使用 ${label}（${agentId}）`, `This session is pinned to ${label} (${agentId})`)}><span

      className={cn(base, sizeCls, className)}
    >
      <Bot size={iconSize} />
      {label}
    </span></Hint>
  );
}
