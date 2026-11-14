// 画布公共构件：节点卡片类型、连线类型与主题点阵色。
// 编排页与运行详情页的缩略图共用，保证两边画出来的东西一模一样。

import { WorkflowEdge, type CanvasFlowEdge } from '@/components/workflow/WorkflowEdge';
import { WorkflowNodeCard, type CanvasFlowNode, type NodeExecutionStatus } from '@/components/workflow/WorkflowNodeCard';
import type { NodeKind, WorkflowNode } from '@/api/workflows';
import { emptyNode } from '@/lib/workflowMeta';

export const NODE_TYPES = { workflow: WorkflowNodeCard };
export const EDGE_TYPES = { workflow: WorkflowEdge };

// 颜色只在 canvasTheme 里维护，这里转出给画布页面使用。
export { DOT_COLOR, EDGE_ACTIVE_COLOR, EDGE_COLOR } from '@/components/workflow/canvasTheme';

/**
 * 详情页只有节点 id / 类型 / 名称，节点卡片要求完整定义。
 * 直接借编排页的空节点模板补全，只覆盖卡片要展示的字段，避免两处字段各写一份。
 */
export function previewNode(node: { id: string; kind: NodeKind; label: string; x?: number; y?: number }): WorkflowNode {
  const base = emptyNode(node.kind, node.id, { x: node.x ?? 0, y: node.y ?? 0 });
  return { ...base, label: node.label };
}

export type { CanvasFlowEdge, CanvasFlowNode, NodeExecutionStatus };
