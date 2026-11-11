// 画布节点卡片的状态标识：
//   1. waiting 要区分「待审批」与「待填写」，并且整张卡片底部给出暂停提示；
//   2. succeeded / failed 只改描边与标题行圆点，不堆叠底板；
//   3. 没有状态时不出现任何状态文案（进入画布不会满屏标记）。
// Handle 依赖 React Flow 的 store，这里只验证卡片自己的渲染，替换成空实现。
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { NodeProps } from '@xyflow/react';

import { WorkflowNodeCard, type CanvasFlowNode } from './WorkflowNodeCard';
import { emptyNode } from '@/lib/workflowMeta';

vi.mock('@xyflow/react', () => ({
  Handle: () => null,
  Position: { Left: 'left', Right: 'right' },
}));

function renderCard(options: {
  status?: 'running' | 'succeeded' | 'failed' | 'waiting';
  waitingKind?: 'approval' | 'input';
}) {
  const node = emptyNode('approval', 'gate', { x: 0, y: 0 });
  node.label = '值班确认';
  const props = {
    id: node.id,
    data: {
      node,
      title: '人工审批',
      summary: '确认处置方案',
      issue: null,
      status: options.status,
      waitingKind: options.waitingKind,
    },
    selected: false,
  } as unknown as NodeProps<CanvasFlowNode>;
  return render(<WorkflowNodeCard {...props} />);
}

describe('WorkflowNodeCard 状态标识', () => {
  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
  });

  it('没有运行状态时只显示节点信息，不带状态文案', () => {
    renderCard({});
    expect(screen.getByText('人工审批')).toBeInTheDocument();
    expect(screen.queryByText('成功')).not.toBeInTheDocument();
    expect(screen.queryByText(/运行已暂停/)).not.toBeInTheDocument();
  });

  it('待审批：标题行显示「待审批」，底部说明运行已暂停', () => {
    renderCard({ status: 'waiting', waitingKind: 'approval' });
    expect(screen.getByText('待审批')).toBeInTheDocument();
    expect(screen.getByText('等待人工审批，运行已暂停')).toBeInTheDocument();
  });

  it('待填写：文案换成「待填写」，不误写成审批', () => {
    renderCard({ status: 'waiting', waitingKind: 'input' });
    expect(screen.getByText('待填写')).toBeInTheDocument();
    expect(screen.getByText('等待人工填写，运行已暂停')).toBeInTheDocument();
    expect(screen.queryByText('待审批')).not.toBeInTheDocument();
  });

  it('成功与失败沿用同一套圆点文案，不显示人工提示条', () => {
    renderCard({ status: 'succeeded' });
    expect(screen.getByText('成功')).toBeInTheDocument();
    expect(screen.queryByText(/运行已暂停/)).not.toBeInTheDocument();
  });

  it('失败只标「失败」，与等待审批区分开', () => {
    renderCard({ status: 'failed' });
    expect(screen.getByText('失败')).toBeInTheDocument();
    expect(screen.queryByText('待审批')).not.toBeInTheDocument();
    expect(screen.queryByText(/运行已暂停/)).not.toBeInTheDocument();
  });
});
