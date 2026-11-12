// 画布缩略图：列表行与运行详情共用。
//
// 直接复用编排页那套 React Flow 画布（同一套节点卡片、连线与视口控制），
// 滚轮缩放 / 左键拖动平移都由 React Flow 内置处理，这里只做只读渲染：
// 节点不可拖动、不可连线，点节点用来定位到中栏详情。

import { useCallback, useMemo } from 'react';
import { Background, BackgroundVariant, ReactFlow, ReactFlowProvider, useReactFlow, type Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type { NodeRunStatus, WorkflowPreview } from '@/api/workflows';
import {
  DOT_COLOR,
  EDGE_TYPES,
  NODE_TYPES,
  previewNode,
  type CanvasFlowEdge,
  type CanvasFlowNode,
  type NodeExecutionStatus,
} from '@/components/workflow/WorkflowCanvas';
import { kindTitle } from '@/lib/workflowMeta';
import { useI18n } from '@/i18n/locale';
import { useThemeMode } from '@/store/mode';

type Props = {
  graph: WorkflowPreview;
  /** 运行快照：node_id → 执行结果。 */
  statuses?: Record<string, NodeRunStatus | 'running'>;
  /** 停在人工节点等审批/填写：这个节点染成警示色，而不是成功色。 */
  gateId?: string | null;
  selectedId?: string | null;
  onSelect?(nodeId: string): void;
  className?: string;
};

/** 运行快照 → 画布状态；未执行/跳过不染色。 */
function toCanvasStatus(status: NodeRunStatus | 'running' | undefined): NodeExecutionStatus | undefined {
  if (status === 'running' || status === 'succeeded' || status === 'failed') return status;
  return undefined;
}

function GraphCanvas({ graph, statuses, gateId, selectedId, onSelect, className }: Props) {
  console.log('THUMB-PROPS>>>', selectedId, gateId, statuses ? Object.keys(statuses).join(',') : '');
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const { resolved } = useThemeMode();
  const { fitView, getViewport, setCenter } = useReactFlow();

  const flowNodes = useMemo<CanvasFlowNode[]>(
    () =>
      graph.nodes.map((node) => ({
        id: node.id,
        type: 'workflow' as const,
        position: { x: node.x ?? 0, y: node.y ?? 0 },
        selected: node.id === selectedId,
        data: {
          node: previewNode(node),
          title: kindTitle(node.kind, null, zh),
          summary: '',
          issue: null,
          status: toCanvasStatus(statuses?.[node.id]),
          waitingKind: node.id === gateId ? 'approval' : undefined,
        },
      })),
    [gateId, graph.nodes, selectedId, statuses, zh],
  );

  const flowEdges = useMemo<CanvasFlowEdge[]>(
    () =>
      graph.edges.map((edge, index) => ({
        id: edge.id ?? `${edge.source}-${edge.target}-${edge.route}-${index}`,
        source: edge.source,
        target: edge.target,
        type: 'workflow' as const,
        data: { route: edge.route, kinds: [], onInsert: () => {} },
      })),
    [graph.edges],
  );

  const onNodeClick = useCallback(
    (_: unknown, node: Node) => {
      onSelect?.(node.id);
    },
    [onSelect],
  );

  return (
    <ReactFlow
      nodes={flowNodes}
      edges={flowEdges}
      nodeTypes={NODE_TYPES}
      edgeTypes={EDGE_TYPES}
      // 只读：缩略图里不允许改结构，拖节点和拉连线都关掉
      nodesDraggable={false}
      nodesConnectable={false}
      edgesReconnectable={false}
      onNodeClick={onNodeClick}
      // 进画布先装下整张图；缩放下限与编排页一致
      fitView
      fitViewOptions={{ padding: 0.25, maxZoom: 1 }}
      minZoom={0.1}
      maxZoom={1.6}
      // 滚轮缩放、左键拖动平移、双击放大都是 React Flow 内置能力
      panOnDrag
      zoomOnScroll
      zoomOnPinch
      zoomOnDoubleClick
      // 光标在画布上时拦掉页面滚动，滚轮专用于缩放
      preventScrolling
      defaultEdgeOptions={{ type: 'workflow' }}
      className={className}
      style={{ height: '100%', width: '100%' }}
      onInit={() => {
        // 初始视口：停在人工节点就看它，否则看选中节点，都没有就整图自适应。
        // 放在 onInit 里，此时 React Flow 已经量好容器与节点，动作不会被打回去。
        const focusId = gateId ?? selectedId ?? null;
        const focus = focusId === null ? null : (graph.nodes.find((node) => node.id === focusId) ?? null);
        if (focus !== null && focus.x != null && focus.y != null) {
          const { zoom } = getViewport();
          void setCenter(focus.x + 120, focus.y + 38, { zoom, duration: 0 });
          return;
        }
        void fitView({ padding: 0.25, maxZoom: 1 });
      }}
    >
      <Background variant={BackgroundVariant.Dots} gap={16} size={1.5} color={DOT_COLOR[resolved]} />
    </ReactFlow>
  );
}

export function WorkflowGraphThumb(props: Props) {
  return (
    <ReactFlowProvider>
      <GraphCanvas {...props} />
    </ReactFlowProvider>
  );
}
