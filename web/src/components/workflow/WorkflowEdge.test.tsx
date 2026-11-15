// 连线中点弹出的插入节点面板：
//   1. 长于可视高度时要能独立滚动，滚轮不缩放画布；
//   2. 挂到 body 并由 floating-ui 做视口内定位，避免被节点卡片盖住或伸出画布。
import { fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { EdgeProps } from '@xyflow/react';

import { WorkflowEdge, type CanvasFlowEdge } from './WorkflowEdge';
import type { CatalogKind } from '@/api/workflows';
import { setLocale } from '@/i18n/locale';

vi.mock('@xyflow/react', () => ({
  BaseEdge: ({ markerEnd }: { markerEnd?: string }) => (
    <path data-testid="workflow-edge-line" data-marker-end={markerEnd} />
  ),
  EdgeLabelRenderer: ({ children }: { children: ReactNode }) => <>{children}</>,
  getBezierPath: () => ['M0,0', 0, 0],
  useViewport: () => ({ x: 0, y: 0, zoom: 1 }),
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
    markerEnd: 'url(#react-flow__arrowclosed-e1)',
  } as unknown as EdgeProps<CanvasFlowEdge>;
  return render(
    <svg>
      <WorkflowEdge {...props} />
    </svg>,
  );
}

describe('连线插入节点面板', () => {
  it('滚轮落在面板内，由列表滚动而不是缩放画布', async () => {
    renderEdge();
    fireEvent.click(screen.getByRole('button', { name: '在连线中间插入节点' }));

    // floating-ui 首帧定位是异步的，定位完成前面板 visibility:hidden
    const input = await screen.findByRole('textbox', { name: '搜索要插入的节点' });
    const panel = input.closest('.nowheel');
    expect(panel).not.toBeNull();
    expect(panel?.parentElement).toBe(document.body);
    expect(screen.getByText('大模型')).toBeInTheDocument();
  });

  it('Esc 关闭面板，按钮回到收起态', async () => {
    renderEdge();
    const trigger = screen.getByRole('button', { name: '在连线中间插入节点' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(trigger);

    const input = await screen.findByRole('textbox', { name: '搜索要插入的节点' });
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(screen.queryByRole('textbox', { name: '搜索要插入的节点' })).not.toBeInTheDocument();
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
  });

  it('把 React Flow 的箭头 marker 透传给连线', () => {
    renderEdge();
    const line = screen.getByTestId('workflow-edge-line');
    expect(line.getAttribute('data-marker-end')).toBe('url(#react-flow__arrowclosed-e1)');
  });
});
