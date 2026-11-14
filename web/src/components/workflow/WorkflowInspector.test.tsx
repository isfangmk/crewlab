// 节点设置里的下拉框是表单控件，必须撑满字段宽度。
// Select 默认 w-auto（给筛选器的幽灵态用），漏写 w-full 时会缩成只剩一个箭头。
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { WorkflowInspector } from './WorkflowInspector';
import { emptyNode } from '@/lib/workflowMeta';
import type { NodeKind, WorkflowCatalog, WorkflowNode } from '@/api/workflows';

const catalog: WorkflowCatalog = {
  node_groups: [],
  node_kinds: [
    { key: 'mcp', title: 'MCP 工具', title_en: 'MCP tool', group: 'action' },
    { key: 'agent', title: 'LLM 节点', title_en: 'LLM', group: 'model' },
    { key: 'http', title: 'HTTP 请求', title_en: 'HTTP', group: 'action' },
    { key: 'switch', title: '条件分支', title_en: 'Switch', group: 'flow' },
    { key: 'end', title: '结束', title_en: 'End', group: 'flow' },
  ],
  models: [],
  mcp_tools: [{ server: 'iomp', tool: 'read_ssh_start', title: 'read_ssh_start' }],
};

function renderNode(kind: NodeKind, setup?: (node: WorkflowNode) => void) {
  const node = emptyNode(kind, kind, { x: 0, y: 0 });
  setup?.(node);
  render(
    <WorkflowInspector
      workflowId={1}
      catalog={catalog}
      kinds={catalog.node_kinds}
      definition={{ nodes: [node], edges: [] }}
      node={node}
      meta={{ name: '巡检流程', description: '', status: 'draft' }}
      onNodeChange={vi.fn()}
      onDeleteNode={vi.fn()}
      onDuplicateNode={vi.fn()}
      onRunNode={vi.fn()}
      running={false}
      probe={null}
      live={null}
      tab="settings"
      onTabChange={vi.fn()}
      onMetaChange={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  return node;
}

describe('节点设置表单宽度', () => {
  it.each([
    ['MCP Server', 'mcp' as const, (node: WorkflowNode) => { node.mcp_server = 'iomp'; }],
    ['工具', 'mcp' as const, (node: WorkflowNode) => { node.mcp_server = 'iomp'; node.mcp_tool = 'read_ssh_start'; }],
    ['模型', 'agent' as const, undefined],
    ['运行输出', 'end' as const, undefined],
    ['方法', 'http' as const, undefined],
    ['判断方式', 'switch' as const, undefined],
  ])('%s 下拉框撑满字段宽度', (label, kind, setup) => {
    renderNode(kind, setup);
    expect(screen.getByRole('combobox', { name: label })).toHaveClass('w-full');
  });
});
