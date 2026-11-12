// 画布缩略图：列表行与运行详情共用。
//
// 直接复用编排页那套 React Flow 画布（同一套节点卡片、连线与视口控制），
// 滚轮缩放 / 左键拖动平移都由 React Flow 内置处理，这里只做只读渲染：
// 节点不可拖动、不可连线，点节点用来定位到中栏详情。

import { useCallback, useEffect, useMemo } from 'react';
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
  /**
   * 视口模式：
   * - focus（默认）：聚焦当前节点，节点多时也能看清标题与状态；
   * - overview：装下整张图，用来一眼看全局结构。
   */
  mode?: 'focus' | 'overview';
  className?: string;
};

/** 聚焦缩放：能看清节点标题与状态描边的档位。 */
const FOCUS_ZOOM = 0.75;

/** 运行快照 → 画布状态；未执行/跳过不染色。 */
function toCanvasStatus(status: NodeRunStatus | 'running' | undefined): NodeExecutionStatus | undefined {
  if (status === 'running' || status === 'succeeded' || status === 'failed') return status;
  return undefined;
}

function GraphCanvas({ graph, statuses, gateId, selectedId, onSelect, mode = 'focus', className }: Props) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const { resolved } = useThemeMode();
  const { fitView, getNodes } = useReactFlow();
  // 视口跟着当前节点走：切换节点后要把它带回画面中央
  const focusId = gateId ?? selectedId ?? null;
  const overview = mode === 'overview';

  /**
   * 把指定节点移到画面中央。
   *
   * 用 React Flow 内置的 fitView({ nodes })：它会按目标节点的实际尺寸算视口，
   * 手算中心点容易因为节点还没量好尺寸而算偏，节点就被推到画布外了。
   */
  const focusNode = useCallback(
    (nodeId: string) => {
      const target = getNodes().find((node) => node.id === nodeId);
      if (target === undefined) return;
      void fitView({
        nodes: [{ id: nodeId }],
        padding: 0.35,
        maxZoom: FOCUS_ZOOM,
        duration: 200,
      });
    },
    [fitView, getNodes],
  );

  // 节点量好尺寸后再居中，否则算出来的中心是偏的
  useEffect(() => {
    if (overview) return;
    if (focusId === null) return;
    const timer = window.setTimeout(() => focusNode(focusId), 0);
    return () => window.clearTimeout(timer);
  }, [focusId, focusNode, overview]);

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
          // 停在人工节点时库里记的是「已执行完」，但它在等人：
          // 这里直接染成等待态，否则会显示成绿色成功。
          status: node.id === gateId ? 'waiting' : toCanvasStatus(statuses?.[node.id]),
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
      // 聚焦模式跟随当前节点；总览模式装下整张图。
      // 节点多时整图塞进窄栏会压成一条线，所以窄栏用聚焦，弹层用总览。
      minZoom={0.15}
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
        if (overview) {
          // 总览模式装下整张图，一眼看清全局结构
          void fitView({ padding: 0.2, maxZoom: 1 });
          return;
        }
        // 聚焦模式停在人工节点就看它，否则看选中节点，都没有就看第一个节点。
        // 放在 onInit 里，此时 React Flow 已经量好容器与节点，动作不会被打回去。
        const initialId = gateId ?? selectedId ?? graph.nodes[0]?.id ?? null;
        if (initialId === null) return;
        focusNode(initialId);
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
