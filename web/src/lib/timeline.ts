// 对话过程时间线的纯逻辑：按用户消息切「回合」，提取过程步骤与摘要统计。
// 分组是纯视图变换——不改 ChatThread 的消息状态结构，流式合并照旧。

import type { ChatMessage } from '@/api/chat';
import { resolveToolStatus } from '@/lib/toolStatus';

export type StepStatus = 'pending' | 'success' | 'error' | 'timeout' | 'unknown' | 'done';

export type StepKind = 'thinking' | 'intent' | 'tool' | 'approval' | 'input';

export type TimelineItem = {
  message: ChatMessage;
  /** user 用户气泡 | content 正文气泡 | step 过程步骤（思考/意图/工具/审批/澄清） */
  kind: 'user' | 'content' | 'step';
  stepKind?: StepKind;
  status?: StepStatus;
  durationMs?: number;
  createdAt?: string;
  /** 待审批/待输入/运行中：不可折叠，卡片要保持可交互 */
  interactive: boolean;
  /** 失败/超时：过程收起时仍然露出 */
  failed: boolean;
};

export type TurnGroup = {
  key: string;
  items: TimelineItem[];
  hasSteps: boolean;
  thinkingCount: number;
  toolCount: number;
  approvalCount: number;
  failureCount: number;
  /** 回合运行耗时：优先正文 stats.duration_ms，否则工具耗时求和；都没有则缺省 */
  durationMs?: number;
  /** 有待决/运行中步骤：默认展开 */
  active: boolean;
};

// 步骤行的绝对时刻（时:分:秒）；title 用 fullDateTime 全量展示
export function formatClock(iso?: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toTimeString().slice(0, 8);
}

function classify(m: ChatMessage): TimelineItem {
  const base = { message: m, interactive: false, failed: false };
  if (m.role === 'user') {
    return { ...base, kind: 'user' };
  }
  if (m.kind === 'thinking') {
    return {
      ...base,
      kind: 'step',
      stepKind: 'thinking',
      status: m.pending ? 'pending' : 'done',
      createdAt: m.created_at,
      interactive: m.pending === true,
    };
  }
  if (m.kind === 'tool_intent') {
    return {
      ...base,
      kind: 'step',
      stepKind: 'intent',
      status: 'done',
      createdAt: m.created_at,
    };
  }
  const tc = m.tool_call;
  if (tc) {
    const status = resolveToolStatus(tc);
    const result = tc.result as { status?: string } | undefined;
    const envelope = result && typeof result === 'object' ? result.status : undefined;
    const isInput = envelope === 'pending_input';
    const isApproval = envelope === 'pending_approval';
    return {
      ...base,
      kind: 'step',
      stepKind: isInput ? 'input' : isApproval ? 'approval' : 'tool',
      status: isInput || isApproval ? 'pending' : status,
      durationMs: tc.duration_ms,
      createdAt: m.created_at,
      interactive: isInput || isApproval || status === 'pending',
      failed: status === 'error' || status === 'timeout',
    };
  }
  if (m.role === 'tool') {
    // 历史降级行（无 tool_call 载荷）：至少保持步骤位置可见
    return {
      ...base,
      kind: 'step',
      stepKind: 'tool',
      status: 'unknown',
      createdAt: m.created_at,
    };
  }
  return {
    ...base,
    kind: 'content',
    createdAt: m.created_at,
    interactive: m.pending === true,
  };
}

function buildGroup(items: TimelineItem[]): TurnGroup {
  const steps = items.filter((i) => i.kind === 'step');
  const statsDuration =
    items.find((i) => i.kind === 'content' && i.message.stats?.duration_ms)?.message.stats
      ?.duration_ms ?? undefined;
  const toolDurationSum = steps.reduce((sum, i) => sum + (i.durationMs ?? 0), 0);
  const firstUser = items.find((i) => i.kind === 'user');
  return {
    key: firstUser?.message.id ?? items[0].message.id,
    items,
    hasSteps: steps.length > 0,
    thinkingCount: steps.filter((i) => i.stepKind === 'thinking').length,
    toolCount: steps.filter((i) => i.stepKind === 'tool').length,
    approvalCount: steps.filter((i) => i.stepKind === 'approval' || i.stepKind === 'input').length,
    failureCount: steps.filter((i) => i.failed).length,
    durationMs: statsDuration ?? (toolDurationSum > 0 ? toolDurationSum : undefined),
    active: items.some((i) => i.interactive),
  };
}

/** 按用户消息切回合；每个回合的 items 保持原始消息顺序。 */
export function groupTurns(messages: ChatMessage[]): TurnGroup[] {
  const groups: TurnGroup[] = [];
  let current: TimelineItem[] = [];
  const flush = () => {
    if (current.length > 0) {
      groups.push(buildGroup(current));
      current = [];
    }
  };
  for (const m of messages) {
    const item = classify(m);
    if (item.kind === 'user') {
      flush();
    }
    current.push(item);
  }
  flush();
  return groups;
}

/** 摘要条文案：数量与失败状态可见（折叠规范要求标题带状态）。 */
export function summarizeTurn(group: TurnGroup, tr: (zh: string, en: string) => string): string {
  const parts: string[] = [tr('过程', 'Process')];
  if (group.thinkingCount > 0) {
    parts.push(tr(`思考 ${group.thinkingCount} 段`, `${group.thinkingCount} thought(s)`));
  }
  if (group.toolCount > 0) {
    parts.push(
      group.failureCount > 0
        ? tr(
            `工具 ${group.toolCount}（${group.failureCount} 失败）`,
            `${group.toolCount} tool(s) (${group.failureCount} failed)`,
          )
        : tr(`工具 ${group.toolCount}`, `${group.toolCount} tool(s)`),
    );
  }
  if (group.approvalCount > 0) {
    parts.push(tr(`审批 ${group.approvalCount}`, `${group.approvalCount} approval(s)`));
  }
  if (group.durationMs !== undefined) {
    parts.push(`${(group.durationMs / 1000).toFixed(1)}s`);
  }
  return parts.join(' · ');
}
