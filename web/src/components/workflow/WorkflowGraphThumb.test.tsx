// 只读画布缩略图的状态映射：
//   1. 停在人工节点的节点要染成等待态，不能显示成绿色成功；
//   2. 普通节点按运行结果染色；
//   3. 未执行的节点不染色。
// React Flow 的画布在 jsdom 下量不出真实尺寸，这里替换成空实现，
// 只验证节点数据里的状态映射。
import { render } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { WorkflowGraphThumb } from './WorkflowGraphThumb';

const captured: { nodes: { id: string; data: { status?: string } }[] } = {
  nodes: [],
};

vi.mock('@xyflow/react', () => ({
  Background: () => null,
  BackgroundVariant: { Dots: 'dots' },
  Handle: () => null,
  Position: { Left: 'left', Right: 'right' },
  ReactFlow: ({ nodes }: { nodes: { id: string; data: { status?: string } }[] }) => {
    captured.nodes = nodes;
    return <div data-testid="react-flow" />;
  },
  ReactFlowProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useReactFlow: () => ({
    fitView: () => Promise.resolve(true),
    getNodes: () => [],
    getViewport: () => ({ x: 0, y: 0, zoom: 1 }),
    setCenter: () => Promise.resolve(true),
  }),
}));

const graph = {
  nodes: [
    { id: 'start', kind: 'start' as const, label: '开始', x: 0, y: 0 },
    { id: 'kb', kind: 'knowledge' as const, label: '查手册', x: 300, y: 0 },
    { id: 'gate', kind: 'approval' as const, label: '值班长审批', x: 600, y: 0 },
    { id: 'end', kind: 'end' as const, label: '结束', x: 900, y: 0 },
  ],
  edges: [],
};

function statusOf(nodeId: string): string | undefined {
  return captured.nodes.find((node) => node.id === nodeId)?.data.status;
}

describe('WorkflowGraphThumb 状态映射', () => {
  beforeEach(() => {
    captured.nodes = [];
  });

  it('停在人工节点的节点染成等待态，不显示成功', () => {
    render(<WorkflowGraphThumb graph={graph} gateId="gate" statuses={{ start: 'succeeded', gate: 'succeeded' }} />);

    // 库里记的是「已执行完」，但它在等人：这里必须显示等待
    expect(statusOf('gate')).toBe('waiting');
    expect(statusOf('start')).toBe('succeeded');
  });

  it('未执行的节点不染色', () => {
    render(<WorkflowGraphThumb graph={graph} statuses={{ start: 'succeeded' }} />);

    expect(statusOf('kb')).toBeUndefined();
    expect(statusOf('end')).toBeUndefined();
  });

  it('运行中的节点染成运行中', () => {
    render(<WorkflowGraphThumb graph={graph} statuses={{ kb: 'running' }} />);

    expect(statusOf('kb')).toBe('running');
  });

  it('没有状态时全部不染色', () => {
    render(<WorkflowGraphThumb graph={graph} />);

    expect(captured.nodes.every((node) => node.data.status === undefined)).toBe(true);
  });
});
