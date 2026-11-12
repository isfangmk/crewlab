// 画布公共构件：节点卡片类型、连线类型与主题点阵色。
// 编排页与运行详情页的缩略图共用，保证两边画出来的东西一模一样。

import { WorkflowEdge, type CanvasFlowEdge } from '@/components/workflow/WorkflowEdge';
import { WorkflowNodeCard, type CanvasFlowNode, type NodeExecutionStatus } from '@/components/workflow/WorkflowNodeCard';
import type { NodeKind, WorkflowNode } from '@/api/workflows';
import { emptyNode } from '@/lib/workflowMeta';

export const NODE_TYPES = { workflow: WorkflowNodeCard };
export const EDGE_TYPES = { workflow: WorkflowEdge };

/** 画布点阵底色：跟随主题，浅色下不能还是深色网格。 */
export const DOT_COLOR = { light: '#cbd5e1', dark: '#27272a' } as const;

/**
 * 详情页只有节点 id / 类型 / 名称，节点卡片要求完整定义。
 * 直接借编排页的空节点模板补全，只覆盖卡片要展示的字段，避免两处字段各写一份。
 */
export function previewNode(node: { id: string; kind: NodeKind; label: string; x?: number; y?: number }): WorkflowNode {
  const base = emptyNode(node.kind, node.id, { x: node.x ?? 0, y: node.y ?? 0 });
  return { ...base, label: node.label };
}

export type { CanvasFlowEdge, CanvasFlowNode, NodeExecutionStatus };
