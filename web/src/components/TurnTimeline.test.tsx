// TurnTimeline：折叠只收已完成且成功的步骤；失败/交互步骤恒露出，正文不受折叠影响。
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { TurnTimeline } from '@/components/TurnTimeline';
import { setLocale } from '@/i18n/locale';
import type { ChatMessage } from '@/api/chat';
import { groupTurns, type TurnGroup } from '@/lib/timeline';

function msg(partial: Partial<ChatMessage> & { id: string; role: ChatMessage['role'] }): ChatMessage {
  return { ...partial } as ChatMessage;
}

function singleGroup(messages: ChatMessage[]): TurnGroup {
  const groups = groupTurns(messages);
  expect(groups).toHaveLength(1);
  return groups[0];
}

// 隔离测折叠逻辑：renderMessage 只渲染可断言的文本
function renderGroup(group: TurnGroup) {
  render(
    <TurnTimeline group={group} renderMessage={(m) => <div>{m.content}</div>} />,
  );
}

function summaryButton() {
  return screen.getByRole('button', { name: /过程/ });
}

beforeEach(() => setLocale('zh-CN'));
afterEach(() => cleanup());

describe('TurnTimeline', () => {
  it('纯问答回合不套时间线壳：无摘要条，气泡顺序渲染', () => {
    renderGroup(singleGroup([
      msg({ id: 'u1', role: 'user', content: '你好' }),
      msg({ id: 'a1', role: 'assistant', content: '你好呀' }),
    ]));

    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.getByText('你好')).toBeInTheDocument();
    expect(screen.getByText('你好呀')).toBeInTheDocument();
  });

  it('有步骤默认收起：已完成步骤藏起，正文恒露出，摘要带数量', () => {
    renderGroup(singleGroup([
      msg({ id: 'u1', role: 'user', content: '问' }),
      msg({ id: 't1', role: 'assistant', kind: 'thinking', content: '思考内容' }),
      msg({
        id: 'tc1',
        role: 'tool',
        kind: 'tool_card',
        tool_call: { id: 'c1', name: 'query_metric', status: 'success', duration_ms: 100 },
        content: '成功工具',
      }),
      msg({ id: 'a1', role: 'assistant', content: '最终回答' }),
    ]));

    const button = summaryButton();
    expect(button).toHaveAttribute('aria-expanded', 'false');
    expect(button).toHaveTextContent('思考 1 段');
    expect(button).toHaveTextContent('工具 1');
    // 收起只藏已完成且成功的步骤；正文不受折叠影响
    expect(screen.queryByText('思考内容')).not.toBeInTheDocument();
    expect(screen.queryByText('成功工具')).not.toBeInTheDocument();
    expect(screen.getByText('最终回答')).toBeInTheDocument();
    expect(screen.getByText('问')).toBeInTheDocument();
  });

  it('失败步骤收起仍露出（含失败数进摘要），已完成步骤仍可收', async () => {
    renderGroup(singleGroup([
      msg({ id: 'u1', role: 'user', content: '问' }),
      msg({
        id: 'tc1',
        role: 'tool',
        kind: 'tool_card',
        tool_call: { id: 'c1', name: 'query_metric', status: 'error', error: 'boom' },
        content: '失败工具',
      }),
      msg({
        id: 'tc2',
        role: 'tool',
        kind: 'tool_card',
        tool_call: { id: 'c2', name: 'query_metric', status: 'success' },
        content: '成功工具',
      }),
    ]));

    // 有失败默认展开
    expect(summaryButton()).toHaveAttribute('aria-expanded', 'true');
    expect(summaryButton()).toHaveTextContent('1 失败');
    expect(screen.getByText('失败工具')).toBeInTheDocument();

    // 手动收起后：失败步骤恒露出，成功步骤收起
    await userEvent.click(summaryButton());
    expect(screen.getByText('失败工具')).toBeInTheDocument();
    expect(screen.queryByText('成功工具')).not.toBeInTheDocument();
  });

  it('待审批/运行中的交互步骤收起仍露出（卡片要保持可交互）', async () => {
    renderGroup(singleGroup([
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
        content: '待审批卡',
      }),
      msg({
        id: 'tc2',
        role: 'tool',
        kind: 'tool_card',
        tool_call: { id: 'c2', name: 'query_metric', status: 'success' },
        content: '成功工具',
      }),
    ]));

    // 有交互步骤默认展开
    expect(summaryButton()).toHaveAttribute('aria-expanded', 'true');

    await userEvent.click(summaryButton());
    expect(screen.getByText('待审批卡')).toBeInTheDocument();
    expect(screen.queryByText('成功工具')).not.toBeInTheDocument();
  });

  it('点摘要条切换展开：已完成步骤重新露出，aria-expanded 跟随', async () => {
    renderGroup(singleGroup([
      msg({ id: 'u1', role: 'user', content: '问' }),
      msg({ id: 't1', role: 'assistant', kind: 'thinking', content: '思考内容' }),
    ]));

    await userEvent.click(summaryButton());
    expect(summaryButton()).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('思考内容')).toBeInTheDocument();

    await userEvent.click(summaryButton());
    expect(summaryButton()).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('思考内容')).not.toBeInTheDocument();
  });

  it('运行中回合（正文 pending）默认展开', () => {
    renderGroup(singleGroup([
      msg({ id: 'u1', role: 'user', content: '问' }),
      msg({ id: 'a1', role: 'assistant', kind: 'thinking', content: '思考中', pending: true }),
    ]));

    expect(summaryButton()).toHaveAttribute('aria-expanded', 'true');
  });
});
