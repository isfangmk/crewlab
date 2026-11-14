// 连线中点弹出的插入节点面板：
//   1. 长于可视高度时要能独立滚动，滚轮不缩放画布；
//   2. 挂到 body 并做视口内定位，避免被节点卡片盖住或伸出画布。
import { fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { EdgeProps } from '@xyflow/react';

import { placeEdgePopup, WorkflowEdge, type CanvasFlowEdge } from './WorkflowEdge';
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
  it('滚轮落在面板内，由列表滚动而不是缩放画布', () => {
    renderEdge();
    fireEvent.click(screen.getByRole('button', { name: '在连线中间插入节点' }));

    const input = screen.getByRole('textbox', { name: '搜索要插入的节点' });
    const panel = input.closest('.nowheel');
    expect(panel).not.toBeNull();
    expect(panel?.parentElement).toBe(document.body);
    expect(screen.getByText('大模型')).toBeInTheDocument();
  });

  it('把 React Flow 的箭头 marker 透传给连线', () => {
    renderEdge();
    const line = screen.getByTestId('workflow-edge-line');
    expect(line.getAttribute('data-marker-end')).toBe('url(#react-flow__arrowclosed-e1)');
  });
});

describe('placeEdgePopup', () => {
  const viewport = { width: 1000, height: 800 };
  const popup = { width: 212, height: 260 };

  it('下方空间充足时贴在锚点下方并水平居中', () => {
    expect(placeEdgePopup({ left: 400, top: 100, bottom: 120, width: 20 }, popup, viewport)).toEqual({
      left: 304,
      top: 128,
    });
  });

  it('下方放不下时翻到锚点上方', () => {
    expect(placeEdgePopup({ left: 400, top: 700, bottom: 720, width: 20 }, popup, viewport)).toEqual({
      left: 304,
      top: 432,
    });
  });

  it('靠近视口右侧时向左收，不越界', () => {
    const { left } = placeEdgePopup({ left: 980, top: 100, bottom: 120, width: 20 }, popup, viewport);
    expect(left).toBe(viewport.width - popup.width - 8);
  });

  it('比视口还高时从顶部开始，仍保留边距', () => {
    const { top } = placeEdgePopup(
      { left: 400, top: 100, bottom: 120, width: 20 },
      { width: 212, height: 900 },
      viewport,
    );
    expect(top).toBe(8);
  });
});
