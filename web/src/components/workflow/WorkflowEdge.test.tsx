// 连线中点弹出的插入节点面板长于可视高度时要能独立滚动。
// React Flow 默认把滚轮当画布缩放，面板必须带 nowheel 才能让滚轮落回节点列表。
import { fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { EdgeProps } from '@xyflow/react';

import { WorkflowEdge, type CanvasFlowEdge } from './WorkflowEdge';
import type { CatalogKind } from '@/api/workflows';
import { setLocale } from '@/i18n/locale';

vi.mock('@xyflow/react', () => ({
  BaseEdge: () => null,
  EdgeLabelRenderer: ({ children }: { children: ReactNode }) => <>{children}</>,
  getBezierPath: () => ['M0,0', 0, 0],
  Position: { Left: 'left', Right: 'right' },
}));

afterEach(() => setLocale('zh-CN'));

const kinds: CatalogKind[] = [
  { key: 'agent', title: '大模型', title_en: 'LLM', group: 'model' },
  { key: 'http', title: '工具调用', title_en: 'Tool', group: 'action' },
];

function renderEdge() {
  const props = {
    id: 'e1',
    data: { route: '', kinds, onInsert: vi.fn() },
    sourceX: 0,
    sourceY: 0,
    targetX: 120,
    targetY: 0,
  } as unknown as EdgeProps<CanvasFlowEdge>;
  return render(
    <svg>
      <WorkflowEdge {...props} />
    </svg>,
  );
}

describe('连线插入节点面板', () => {
  it('滚轮落在面板内，由列表滚动而不是缩放画布', () => {
    renderEdge();
    fireEvent.click(screen.getByRole('button', { name: '在连线中间插入节点' }));

    const input = screen.getByRole('textbox', { name: '搜索要插入的节点' });
    expect(input.closest('.nowheel')).not.toBeNull();
    expect(screen.getByText('大模型')).toBeInTheDocument();
  });
});
