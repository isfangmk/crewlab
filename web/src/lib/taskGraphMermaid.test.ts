// 任务图 → Mermaid 图文本：纯函数单测，覆盖并行起点、汇合依赖、状态着色与转义。

import { describe, expect, it } from 'vitest';
import { buildMermaidGraph } from '@/lib/taskGraphMermaid';
import type { TaskGraphNode } from '@/api/taskGraph';

const node = (
  localId: string,
  status: string,
  dependsOn: string[] = [],
  name = localId,
): TaskGraphNode => ({
  node_uid: `uid-${localId}`,
  local_id: localId,
  depends_on: dependsOn,
  order_index: 0,
  name,
  target: '',
  instruction: '',
  tools: [],
  status: status as TaskGraphNode['status'],
  attempt: 1,
  summary: '',
  findings: [],
  tool_trace: [],
  log_text: '',
  usage: {},
  error: '',
  duration_ms: 0,
  started_at: null,
  finished_at: null,
});

describe('buildMermaidGraph', () => {
  it('空节点返回空文本', () => {
    expect(buildMermaidGraph([])).toBe('');
  });

  it('表达并行起点与汇合依赖边', () => {
    const text = buildMermaidGraph([
      node('A', 'succeeded'),
      node('B', 'succeeded'),
      node('C', 'running', ['A', 'B']),
      node('D', 'pending', ['C']),
    ]);
    expect(text).toContain('flowchart TD');
    // 无依赖的 A、B 是并行起点，没有入边；C 有两条入边即汇合点
    expect(text).toContain('A --> C');
    expect(text).toContain('B --> C');
    expect(text).toContain('C --> D');
    expect(text.match(/-->/g)?.length).toBe(3);
  });

  it('按状态语义色着色节点', () => {
    const text = buildMermaidGraph([
      node('A', 'succeeded'),
      node('B', 'running'),
      node('C', 'failed'),
      node('D', 'pending'),
    ]);
    expect(text).toContain('style A fill:#0f7a5a'); // ok 绿
    expect(text).toContain('style B fill:#15698a'); // info 蓝
    expect(text).toContain('style C fill:#b3261e'); // danger 红
    expect(text).toContain('style D fill:#4b5563'); // neutral 灰
  });

  it('转义特殊字符并避免非法 id', () => {
    const text = buildMermaidGraph([
      node('a-b', 'pending', [], '带"引号"的名称'),
      node('2start', 'pending'),
    ]);
    expect(text).toContain('n_2start');
    expect(text).not.toMatch(/a-b/); // id 中连字符被替换为下划线
    expect(text).not.toContain('"引号"'); // 名称中的引号被清理
  });
});
