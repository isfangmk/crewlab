// 回合时间线纯逻辑：回合切分、步骤分类、摘要统计与失败/交互态识别。
import { describe, expect, it } from 'vitest';

import type { ChatMessage } from '@/api/chat';
import { formatClock, groupTurns, summarizeTurn } from '@/lib/timeline';

const zh = (text: string) => text;

function msg(partial: Partial<ChatMessage> & { id: string; role: ChatMessage['role'] }): ChatMessage {
  return { ...partial } as ChatMessage;
}

describe('formatClock', () => {
  it('把 ISO 时刻渲染成 时:分:秒', () => {
    const iso = new Date(2026, 10, 15, 8, 9, 10).toISOString();
    expect(formatClock(iso)).toBe('08:09:10');
  });

  it('缺失或非法时刻返回空串，不渲染假时间', () => {
    expect(formatClock(undefined)).toBe('');
    expect(formatClock('not-a-date')).toBe('');
  });
});

describe('groupTurns', () => {
  it('按用户消息切回合，items 保序，回合键取首条用户消息', () => {
    const groups = groupTurns([
      msg({ id: 'u1', role: 'user', content: '查一下告警' }),
      msg({ id: 'a1', role: 'assistant', content: '结论一' }),
      msg({ id: 'u2', role: 'user', content: '再看看日志' }),
      msg({ id: 'a2', role: 'assistant', content: '结论二' }),
    ]);
    expect(groups.map((g) => g.key)).toEqual(['u1', 'u2']);
    expect(groups[0].items.map((i) => i.message.id)).toEqual(['u1', 'a1']);
    expect(groups[0].items.map((i) => i.kind)).toEqual(['user', 'content']);
    expect(groups[0].hasSteps).toBe(false);
  });

  it('思考/工具意图/工具卡归为步骤，正文保持 content', () => {
    const [group] = groupTurns([
      msg({ id: 'u1', role: 'user', content: '问' }),
      msg({ id: 't1', role: 'assistant', kind: 'thinking', content: '先想一想' }),
      msg({ id: 'ti1', role: 'assistant', kind: 'tool_intent', content: 'query_metric' }),
      msg({
        id: 'tc1',
        role: 'tool',
        kind: 'tool_card',
        tool_call: { id: 'c1', name: 'query_metric', status: 'success', duration_ms: 120 },
      }),
      msg({ id: 'a1', role: 'assistant', content: '结论' }),
    ]);
    expect(group.hasSteps).toBe(true);
    expect(group.items.map((i) => i.kind)).toEqual(['user', 'step', 'step', 'step', 'content']);
    expect(group.items.map((i) => i.stepKind)).toEqual([undefined, 'thinking', 'intent', 'tool', undefined]);
    expect(group.thinkingCount).toBe(1);
    expect(group.toolCount).toBe(1);
  });

  it('待审批/待输入信封识别为交互步骤，默认展开标记', () => {
    const [group] = groupTurns([
      msg({ id: 'u1', role: 'user', content: '问' }),
      msg({
        id: 'tc1',
        role: 'tool',
        kind: 'tool_card',
        tool_call: {
          id: 'c1',
          name: 'capture_pcap',
          status: 'pending',
          result: { status: 'pending_approval', approval_id: 'ap-1' },
        },
      }),
      msg({
        id: 'tc2',
        role: 'tool',
        kind: 'tool_card',
        tool_call: {
          id: 'c2',
          name: 'ask_user',
          status: 'pending',
          result: { status: 'pending_input', approval_id: 'ap-2' },
        },
      }),
    ]);
    expect(group.items[1].stepKind).toBe('approval');
    expect(group.items[2].stepKind).toBe('input');
    expect(group.items[1].interactive).toBe(true);
    expect(group.items[2].interactive).toBe(true);
    expect(group.approvalCount).toBe(2);
    expect(group.active).toBe(true);
  });

  it('失败/超时工具标记 failed，失败数进摘要', () => {
    const [group] = groupTurns([
      msg({ id: 'u1', role: 'user', content: '问' }),
      msg({
        id: 'tc1',
        role: 'tool',
        kind: 'tool_card',
        tool_call: { id: 'c1', name: 'query_metric', status: 'error', error: 'boom' },
      }),
      msg({
        id: 'tc2',
        role: 'tool',
        kind: 'tool_card',
        tool_call: { id: 'c2', name: 'query_metric', status: 'timeout', duration_ms: 30 },
      }),
    ]);
    expect(group.items[1].failed).toBe(true);
    expect(group.items[2].failed).toBe(true);
    expect(group.failureCount).toBe(2);
  });

  it('回合耗时优先正文 stats.duration_ms，否则工具耗时求和', () => {
    const [withStats] = groupTurns([
      msg({ id: 'u1', role: 'user', content: '问' }),
      msg({
        id: 'tc1',
        role: 'tool',
        kind: 'tool_card',
        tool_call: { id: 'c1', name: 't', status: 'success', duration_ms: 100 },
      }),
      msg({ id: 'a1', role: 'assistant', content: '答', stats: { duration_ms: 3210 } }),
    ]);
    expect(withStats.durationMs).toBe(3210);

    const [toolSum] = groupTurns([
      msg({ id: 'u1', role: 'user', content: '问' }),
      msg({
        id: 'tc1',
        role: 'tool',
        kind: 'tool_card',
        tool_call: { id: 'c1', name: 't', status: 'success', duration_ms: 100 },
      }),
      msg({
        id: 'tc2',
        role: 'tool',
        kind: 'tool_card',
        tool_call: { id: 'c2', name: 't', status: 'success', duration_ms: 200 },
      }),
    ]);
    expect(toolSum.durationMs).toBe(300);

    const [noTiming] = groupTurns([msg({ id: 'a1', role: 'assistant', content: '答' })]);
    expect(noTiming.durationMs).toBeUndefined();
  });

  it('历史降级行（无 tool_call 的 tool 消息）仍占步骤位', () => {
    const [group] = groupTurns([
      msg({ id: 'u1', role: 'user', content: '问' }),
      msg({ id: 'legacy', role: 'tool', content: '旧工具行' }),
    ]);
    expect(group.items[1].kind).toBe('step');
    expect(group.items[1].stepKind).toBe('tool');
    expect(group.items[1].status).toBe('unknown');
    expect(group.items[1].failed).toBe(false);
  });
});

describe('summarizeTurn', () => {
  it('摘要带思考/工具/失败/审批数量与耗时', () => {
    const [group] = groupTurns([
      msg({ id: 'u1', role: 'user', content: '问' }),
      msg({ id: 't1', role: 'assistant', kind: 'thinking', content: '想' }),
      msg({
        id: 'tc1',
        role: 'tool',
        kind: 'tool_card',
        tool_call: { id: 'c1', name: 't', status: 'success', duration_ms: 500 },
      }),
      msg({
        id: 'tc2',
        role: 'tool',
        kind: 'tool_card',
        tool_call: { id: 'c2', name: 't', status: 'error', error: 'x' },
      }),
      msg({
        id: 'tc3',
        role: 'tool',
        kind: 'tool_card',
        tool_call: {
          id: 'c3',
          name: 't',
          status: 'pending',
          result: { status: 'pending_approval', approval_id: 'ap-1' },
        },
      }),
    ]);
    expect(summarizeTurn(group, zh)).toBe('过程 · 思考 1 段 · 工具 2（1 失败） · 审批 1 · 0.5s');
  });

  it('纯问答回合只有耗时时摘要只剩过程与时长', () => {
    const [group] = groupTurns([
      msg({ id: 'u1', role: 'user', content: '问' }),
      msg({ id: 'a1', role: 'assistant', content: '答', stats: { duration_ms: 1500 } }),
    ]);
    expect(summarizeTurn(group, zh)).toBe('过程 · 1.5s');
  });
});
