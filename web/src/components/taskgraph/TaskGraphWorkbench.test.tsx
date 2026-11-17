// 任务图工作台：拓扑分层、SSE 日志累积、节点重试/跳过、定位高亮。
// API 与 SSE 打桩，渲染/交互全真；vitest 环境 jsdom。

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const {
  listTaskGraphs,
  getTaskGraphDetail,
  subscribeGraphEvents,
  retryGraphNode,
  skipGraphNode,
} = vi.hoisted(() => ({
  listTaskGraphs: vi.fn(),
  getTaskGraphDetail: vi.fn(),
  subscribeGraphEvents: vi.fn(),
  retryGraphNode: vi.fn(),
  skipGraphNode: vi.fn(),
}));

vi.mock('@/api/taskGraph', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/taskGraph')>()),
  listTaskGraphs,
  getTaskGraphDetail,
  subscribeGraphEvents,
  retryGraphNode,
  skipGraphNode,
}));
vi.mock('@/store/auth', () => ({ getToken: () => null }));
vi.mock('@/store/me', () => ({ usePermissions: () => ({ can: () => true, role: 'admin' }) }));

import { TaskGraphWorkbench } from './TaskGraphWorkbench';

const node = (uid: string, local: string, status: string, deps: string[] = [], extra: Record<string, unknown> = {}) => ({
  node_uid: uid, local_id: local, depends_on: deps, order_index: 0, name: local, target: local,
  instruction: '', tools: [], status, attempt: 1, summary: '', findings: [], tool_trace: [],
  log_text: '', usage: {}, error: '', duration_ms: 0, started_at: null, finished_at: null, ...extra,
});

const graph = {
  id: 1, graph_uid: 'g-1', session_id: 's-1', card_message_id: '', title: '混合排查', question: '验证',
  status: 'running', config: {},
  stats: { total: 3, succeeded: 1, failed: 1, skipped: 0, stopped: 0, running: 1, pending: 0, tool_calls: 2 },
  used_tokens: 1500,
  nodes: [node('n-a', 'A', 'succeeded'), node('n-b', 'B', 'failed', [], { error: '查询失败' }), node('n-c', 'C', 'running', ['A', 'B'])],
  findings: [], error: '', duration_ms: 0, created_by: 1, created_at: null, started_at: null, finished_at: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  listTaskGraphs.mockResolvedValue({ items: [graph], total: 1 });
  getTaskGraphDetail.mockResolvedValue(graph);
  subscribeGraphEvents.mockResolvedValue(undefined);
  retryGraphNode.mockResolvedValue({ node_uid: 'n-b', status: 'pending', attempt: 2 });
  skipGraphNode.mockResolvedValue({ node_uid: 'n-b', status: 'skipped' });
});

describe('TaskGraphWorkbench', () => {
  it('渲染拓扑节点、统计与失败态', async () => {
    render(<TaskGraphWorkbench sessionId="s-1" onClose={() => {}} />);
    expect(await screen.findByText('A')).toBeTruthy();
    expect(screen.getByText('C')).toBeTruthy();
    expect(screen.getByText(/进度/)).toBeTruthy();
    // 失败节点默认展开，显示错误
    expect(await screen.findByText('查询失败')).toBeTruthy();
  });

  it('SSE node_log 累积到节点日志', async () => {
    subscribeGraphEvents.mockImplementation(async (_uid: string, onEvent: (e: unknown) => void) => {
      onEvent({ event: 'node_log', graph_uid: 'g-1', node_uid: 'n-c', name: 'C', line: '调用工具 web_search', tool: 'web_search' });
      await new Promise(() => {}); // 保持订阅打开
    });
    const user = userEvent.setup();
    render(<TaskGraphWorkbench sessionId="s-1" onClose={() => {}} />);
    // 运行中节点 C 默认展开；日志在 CodeBlock 里
    await waitFor(() => expect(screen.getByText(/调用工具 web_search/)).toBeTruthy());
    void user;
  });

  it('重试失败节点调用 API', async () => {
    const user = userEvent.setup();
    render(<TaskGraphWorkbench sessionId="s-1" onClose={() => {}} />);
    const retry = await screen.findByRole('button', { name: /重试此节点|Retry node/ });
    await user.click(retry);
    expect(retryGraphNode).toHaveBeenCalledWith('g-1', 'n-b');
  });
});
