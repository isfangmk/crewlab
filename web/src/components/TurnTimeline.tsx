// 回合过程时间线：左脊线把思考/工具/审批 步骤与正文串成一条过程线。
//
// 折叠只收起「已完成且成功」的过程步骤；失败、待审批、待输入与运行中的
// 步骤始终露出（卡片要保持可交互），正文气泡不受折叠影响（阅读节奏优先）。
import { useState, type ReactNode } from 'react';
import {
  Brain,
  ChevronDown,
  HelpCircle,
  PenLine,
  ShieldCheck,
  Wrench,
  type LucideIcon,
} from 'lucide-react';
import { cn } from '@/lib/cn';
import { fullDateTime } from '@/lib/format';
import { useI18n } from '@/i18n/locale';
import { formatClock, summarizeTurn, type StepKind, type TurnGroup } from '@/lib/timeline';

const STEP_ICONS: Record<StepKind, LucideIcon> = {
  thinking: Brain,
  intent: PenLine,
  tool: Wrench,
  approval: ShieldCheck,
  input: HelpCircle,
};

function StepRow({
  item,
  renderMessage,
}: {
  item: TurnGroup['items'][number];
  renderMessage(message: TurnGroup['items'][number]['message']): ReactNode;
}) {
  const Icon = item.stepKind ? STEP_ICONS[item.stepKind] : Wrench;
  const clock = formatClock(item.createdAt);
  return (
    <div className="relative ml-1 border-l border-zinc-800/60 pl-4">
      <span
        aria-hidden
        className={cn(
          'absolute -left-[4.5px] top-[7px] h-2 w-2 rounded-full',
          item.failed
            ? 'bg-danger'
            : item.interactive
              ? 'animate-pulse bg-warn'
              : 'bg-zinc-600',
        )}
      />
      <div className="flex items-center gap-1.5">
        <Icon size={11} className="text-zinc-500" aria-hidden />
        {clock !== '' && (
          <time
            className="font-sans text-[10px] tabular-nums text-zinc-600"
            dateTime={item.createdAt}
            title={item.createdAt ? fullDateTime(item.createdAt) : undefined}
          >
            {clock}
          </time>
        )}
      </div>
      <div className="mt-1">{renderMessage(item.message)}</div>
    </div>
  );
}

export function TurnTimeline({
  group,
  renderMessage,
}: {
  group: TurnGroup;
  renderMessage(message: TurnGroup['items'][number]['message']): ReactNode;
}) {
  const { tr } = useI18n();
  // null = 跟随默认（运行中/失败默认展开）；用户点过以后以用户选择为准
  const [userToggled, setUserToggled] = useState<boolean | null>(null);
  const expanded = userToggled ?? (group.active || group.failureCount > 0);

  if (!group.hasSteps) {
    // 纯问答回合没有过程可看：直接顺序渲染气泡，不套时间线壳
    return (
      <>
        {group.items.map((item) => (
          <div key={item.message.id}>{renderMessage(item.message)}</div>
        ))}
      </>
    );
  }

  return (
    <div>
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setUserToggled(!expanded)}
        className="flex w-full items-center gap-1.5 rounded-lg px-1 py-1 text-left transition-colors hover:bg-zinc-800/30"
      >
        <ChevronDown
          size={11}
          aria-hidden
          className={cn(
            'shrink-0 text-zinc-500 transition-transform',
            expanded ? 'rotate-0' : '-rotate-90',
          )}
        />
        <span className="text-[11px] text-zinc-500">{summarizeTurn(group, tr)}</span>
      </button>
      <div className="mt-1 space-y-2">
        {group.items.map((item) => {
          if (item.kind !== 'step') {
            return <div key={item.message.id}>{renderMessage(item.message)}</div>;
          }
          const hideable = !item.interactive && !item.failed;
          if (hideable && !expanded) return null;
          return <StepRow key={item.message.id} item={item} renderMessage={renderMessage} />;
        })}
      </div>
    </div>
  );
}
