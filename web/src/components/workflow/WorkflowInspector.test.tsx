// 节点设置里的下拉框是表单控件，必须撑满字段宽度。
// Select 默认 w-auto（给筛选器的幽灵态用），漏写 w-full 时会缩成只剩一个箭头。
// 工具节点同时覆盖内置工具与 MCP 两种来源：页签切换会清空另一套字段。
import { fireEvent, render, screen } from '@testing-library/react';
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
  mcp_tools: [{ server: 'iomp', tool: 'read_ssh_start', title: 'read_ssh_start' }],
  builtin_tools: [
    {
      name: 'query_knowledge',
      group: 'knowledge',
      group_title: '知识检索',
      group_title_en: 'Knowledge',
      description: '语义检索组织知识库',
      risk: 'safe',
      params: [
        { name: 'query', type: 'string', required: true, desc: '检索语句' },
        { name: 'limit', type: 'int', required: false, default: 5 },
      ],
    },
    {
      name: 'run_python',
      group: 'code',
      group_title: '代码解释器',
      group_title_en: 'Code interpreter',
      description: '在沙盒中执行 Python 代码',
      risk: 'safe',
      params: [{ name: 'code', type: 'string', required: true }],
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

describe('工具节点来源页签', () => {
  it('内置工具页签按工具签名渲染参数表单', () => {
    renderNode('mcp', (node) => {
      node.tool_source = 'builtin';
      node.tool_name = 'query_knowledge';
      node.arguments = { query: '{{topic}}' };
    });

    expect(screen.getByRole('tab', { name: '内置工具' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('combobox', { name: '内置工具' })).toHaveClass('w-full');
    expect(screen.getByRole('textbox', { name: 'query' })).toHaveValue('{{topic}}');
    expect(screen.getByRole('textbox', { name: 'limit' })).toHaveAttribute('placeholder', '5');
  });

  it('切到 MCP 页签会清空内置工具字段', () => {
    const { onNodeChange } = renderNode('mcp', (node) => {
      node.tool_source = 'builtin';
      node.tool_name = 'query_knowledge';
      node.arguments = { query: 'DNS' };
    });

    fireEvent.click(screen.getByRole('tab', { name: 'MCP 工具' }));

    expect(onNodeChange).toHaveBeenCalledWith({
      tool_source: 'mcp',
      tool_name: '',
      mcp_server: '',
      mcp_tool: '',
      arguments: {},
    });
  });

  it('切到内置工具页签会清空 MCP 字段', () => {
    const { onNodeChange } = renderNode('mcp', (node) => {
      node.mcp_server = 'iomp';
      node.mcp_tool = 'read_ssh_start';
      node.arguments = { cmd: 'uptime' };
    });

    fireEvent.click(screen.getByRole('tab', { name: '内置工具' }));

    expect(onNodeChange).toHaveBeenCalledWith({
      tool_source: 'builtin',
      tool_name: '',
      mcp_server: '',
      mcp_tool: '',
      arguments: {},
    });
  });
});
