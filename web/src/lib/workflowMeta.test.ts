// workflowMeta 单元测试 — 画布、节点库、缩略图共用的那一份「节点语义」：
//   分层布局（BFS 列 / 同层居中 / 孤立节点靠右）、新节点 ID、必填提示、变量占位符。
import { describe, expect, it } from 'vitest';

import {
  emptyNode,
  layeredLayout,
  nextNodeId,
  nodeIssue,
  nodeSummary,
  placeholdersIn,
  toneOf,
} from './workflowMeta';

describe('layeredLayout', () => {
  it('从开始节点做 BFS：深度决定列，同层节点纵向居中', () => {
    const graph = {
      nodes: [
        { id: 'start', kind: 'start' as const },
        { id: 'left', kind: 'agent' as const },
        { id: 'right', kind: 'agent' as const },
        { id: 'end', kind: 'end' as const },
      ],
      edges: [
        { source: 'start', target: 'left' },
        { source: 'start', target: 'right' },
        { source: 'left', target: 'end' },
        { source: 'right', target: 'end' },
      ],
    };
    const positions = layeredLayout(graph);

    // 三列：start / 两个 agent / end
    expect(positions.start).toEqual({ x: 0, y: 0 });
    expect(positions.left).toEqual({ x: 300, y: -66 });
    expect(positions.right).toEqual({ x: 300, y: 66 });
    expect(positions.end).toEqual({ x: 600, y: 0 });
  });

  it('不可达节点排在最右一列，不与主干重叠', () => {
    const positions = layeredLayout({
      nodes: [
        { id: 'start', kind: 'start' as const },
        { id: 'end', kind: 'end' as const },
        { id: 'orphan', kind: 'assign' as const },
      ],
      edges: [{ source: 'start', target: 'end' }],
    });

    expect(positions.start).toEqual({ x: 0, y: 0 });
    expect(positions.end).toEqual({ x: 300, y: 0 });
    expect(positions.orphan).toEqual({ x: 600, y: 0 });
  });
});

describe('nextNodeId', () => {
  it('先尝试 kind 本身，被占用后按 _2、_3 递增', () => {
    const nodes = [emptyNode('agent', 'agent', { x: 0, y: 0 }), emptyNode('agent', 'agent_2', { x: 0, y: 0 })];
    expect(nextNodeId('agent', nodes)).toBe('agent_3');
    expect(nextNodeId('http', nodes)).toBe('http');
  });
});

describe('nodeIssue', () => {
  it('只提示节点自己的必填项，权威校验仍走后端', () => {
    const agent = emptyNode('agent', 'a', { x: 0, y: 0 });
    expect(nodeIssue(agent)).toBe('请选择模型');
    agent.provider = 'openai';
    agent.model = 'gpt-4o';
    expect(nodeIssue(agent)).toBe('请填写指令');
    agent.instruction = '总结 {{input}}';
    expect(nodeIssue(agent)).toBeNull();

    const start = emptyNode('start', 's', { x: 0, y: 0 });
    expect(nodeIssue(start)).toBe('需要至少一个输入变量');

    const sw = emptyNode('switch', 'sw', { x: 0, y: 0 });
    expect(nodeIssue(sw)).toBe('请填写判断表达式');
    sw.branch_mode = 'model';
    expect(nodeIssue(sw)).toBeNull();

    const assign = emptyNode('assign', 'set', { x: 0, y: 0 });
    expect(nodeIssue(assign)).toBe('至少一个赋值');
  });
});

describe('nodeSummary', () => {
  it('副标题是对节点行为的压缩描述，不是空串', () => {
    const http = emptyNode('http', 'h', { x: 0, y: 0 });
    expect(nodeSummary(http)).toBe('未填写 URL');
    http.url = 'https://example.com/api';
    expect(nodeSummary(http)).toBe('GET https://example.com/api');

    const end = emptyNode('end', 'e', { x: 0, y: 0 });
    expect(nodeSummary(end)).toBe('输出整份状态');
    end.output_key = 'answer';
    expect(nodeSummary(end)).toBe('运行输出取该变量');
  });
});

describe('placeholdersIn', () => {
  it('提取 {{变量}} 引用，允许空格，忽略非法标识符', () => {
    expect(placeholdersIn('把 {{ answer }} 和 {{context}} 合并，忽略 {{9bad}} 与 {{中文}}')).toEqual([
      'answer',
      'context',
    ]);
  });
});

describe('toneOf', () => {
  it('按分组给颜色：编排中性、模型靛蓝、动作青绿、人工琥珀', () => {
    expect(toneOf('start').dot).toBe('#64748b');
    expect(toneOf('agent').dot).toBe('#6366f1');
    expect(toneOf('knowledge').dot).toBe('#10b981');
    expect(toneOf('approval').dot).toBe('#f59e0b');
  });
});
