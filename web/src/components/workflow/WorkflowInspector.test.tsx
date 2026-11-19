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
    { key: 'mcp', title: '工具', title_en: 'Tool', group: 'action' },
    { key: 'agent', title: 'LLM 节点', title_en: 'LLM', group: 'model' },
    { key: 'http', title: 'HTTP 请求', title_en: 'HTTP', group: 'action' },
    { key: 'switch', title: '条件分支', title_en: 'Switch', group: 'flow' },
    { key: 'end', title: '结束', title_en: 'End', group: 'flow' },
  ],
  models: [],
  mcp_tools: [
    {
      server: 'iomp',
      tool: 'read_ssh_start',
      title: 'read_ssh_start',
      description: '读取远端状态',
      params: [
        { name: 'host', type: 'string', required: true },
        { name: 'lines', type: 'int', required: false, default: 20 },
      ],
      params_known: true,
    },
    {
      server: 'obs-tools',
      tool: 'query_metric',
      title: 'query_metric',
      description: '指标范围查询。',
      params: [
        { name: 'metric', type: 'string', required: true },
        { name: 'step', type: 'string', required: false, default: '60s' },
      ],
      params_known: true,
    },
    {
      server: 'obs-tools',
      tool: 'legacy',
      title: 'legacy',
      description: '旧缓存没有 schema',
      params: [],
      params_known: false,
    },
  ],
};

function renderNode(kind: NodeKind, setup?: (node: WorkflowNode) => void) {
  const node = emptyNode(kind, kind, { x: 0, y: 0 });
  setup?.(node);
  const onNodeChange = vi.fn();
  render(
    <WorkflowInspector
      workflowId={1}
      catalog={catalog}
      kinds={catalog.node_kinds}
      definition={{ nodes: [node], edges: [] }}
      node={node}
      meta={{ name: '巡检流程', description: '', status: 'draft' }}
      onNodeChange={onNodeChange}
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
  return { node, onNodeChange };
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

describe('MCP 工具参数', () => {
  it('按工具 input_schema 渲染参数表单', () => {
    renderNode('mcp', (node) => {
      node.mcp_server = 'obs-tools';
      node.mcp_tool = 'query_metric';
      node.arguments = { metric: '{{x}}', step: '60s' };
    });

    expect(screen.getByText('指标范围查询。')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'metric' })).toHaveValue('{{x}}');
    expect(screen.getByRole('textbox', { name: 'step' })).toHaveValue('60s');
    expect(screen.getByRole('textbox', { name: 'metric' })).toHaveAttribute('placeholder', '固定值或 {{变量}}');
  });

  it('缓存没有 schema 时退回手动参数表', () => {
    renderNode('mcp', (node) => {
      node.mcp_server = 'obs-tools';
      node.mcp_tool = 'legacy';
    });

    expect(screen.getByText('还没有参数')).toBeInTheDocument();
  });
});
