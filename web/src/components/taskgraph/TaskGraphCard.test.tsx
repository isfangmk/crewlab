// 任务图卡：草稿确认、拓扑渲染、状态展示。API/SSE 打桩，交互与渲染全真。

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { getTaskGraphDetail, confirmGraph, updateGraphNodes, subscribeGraphEvents } = vi.hoisted(() => ({
  getTaskGraphDetail: vi.fn(),
  confirmGraph: vi.fn(),
  updateGraphNodes: vi.fn(),
  subscribeGraphEvents: vi.fn(async () => {}),
}));

vi.mock('@/api/taskGraph', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/taskGraph')>()),
  getTaskGraphDetail,
  confirmGraph,
  updateGraphNodes,
  subscribeGraphEvents,
}));
vi.mock('@/store/auth', () => ({ getToken: () => null }));

import { TaskGraphCard } from './TaskGraphCard';

const draftWire = {
  id: 1,
  graph_uid: 'g-1',
  session_id: 's-1',
  card_message_id: '',
  title: '混合排查',
  question: '验证拓扑',
  status: 'draft',
  config: {},
  stats: { total: 3, succeeded: 0, failed: 0, skipped: 0, stopped: 0, running: 0, pending: 3, tool_calls: 0 },
  used_tokens: 0,
  nodes: [
    { node_uid: 'n-a', local_id: 'A', depends_on: [], order_index: 0, name: '甲', target: '查 A', instruction: '', tools: [], status: 'pending', attempt: 1, summary: '', findings: [], tool_trace: [], log_text: '', usage: {}, error: '', duration_ms: 0, started_at: null, finished_at: null },
    { node_uid: 'n-b', local_id: 'B', depends_on: [], order_index: 1, name: '乙', target: '查 B', instruction: '', tools: [], status: 'pending', attempt: 1, summary: '', findings: [], tool_trace: [], log_text: '', usage: {}, error: '', duration_ms: 0, started_at: null, finished_at: null },
    { node_uid: 'n-c', local_id: 'C', depends_on: ['A', 'B'], order_index: 2, name: '丙', target: '汇总', instruction: '', tools: [], status: 'pending', attempt: 1, summary: '', findings: [], tool_trace: [], log_text: '', usage: {}, error: '', duration_ms: 0, started_at: null, finished_at: null },
  ],
  findings: [],
  error: '',
  duration_ms: 0,
  created_by: 1,
  created_at: null,
  started_at: null,
  finished_at: null,
};

const call = {
  name: 'submit_task_graph',
  status: 'success',
  arguments: {},
  result: { graph_uid: 'g-1', title: '混合排查', status: 'draft' },
};

beforeEach(() => {
  vi.clearAllMocks();
  getTaskGraphDetail.mockResolvedValue(draftWire);
  confirmGraph.mockResolvedValue({ started: true });
  updateGraphNodes.mockResolvedValue(draftWire);
});

describe('TaskGraphCard', () => {
  it('按 depends_on 拓扑渲染节点与汇合标记', async () => {
    render(<TaskGraphCard call={call} />);
    expect(await screen.findByText('甲')).toBeTruthy();
    expect(screen.getByText('乙')).toBeTruthy();
    expect(screen.getByText('丙')).toBeTruthy();
    // C 依赖两个前驱，显示 ←2
    expect(screen.getByText('←2')).toBeTruthy();
    expect(screen.getByText(/3 节点/)).toBeTruthy();
  });

  it('草稿态显示开始执行，pending 时禁用', async () => {
    render(<TaskGraphCard call={call} pending />);
    const btn = await screen.findByRole('button', { name: /开始执行|Run/ });
    expect((btn as HTMLButtonElement).disabled).toBe(true);
  });

  it('非交互账号（viewer）禁止确认', async () => {
    render(<TaskGraphCard call={call} interactive={false} />);
    const btn = await screen.findByRole('button', { name: /开始执行|Run/ });
    expect((btn as HTMLButtonElement).disabled).toBe(true);
  });

  it('可交互账号点击开始执行触发确认', async () => {
    const user = userEvent.setup();
    render(<TaskGraphCard call={call} interactive />);
    const btn = await screen.findByRole('button', { name: /开始执行|Run/ });
    await user.click(btn);
    expect(confirmGraph).toHaveBeenCalledWith('g-1');
  });

  it('调整任务图进入编辑态并可保存', async () => {
    const user = userEvent.setup();
    render(<TaskGraphCard call={call} interactive />);
    const editBtn = await screen.findByRole('button', { name: /调整任务图|Edit graph/ });
    await user.click(editBtn);
    expect(screen.getByRole('button', { name: /保存并执行|Save & run/ })).toBeTruthy();
    expect(screen.getAllByLabelText(/节点名|Node name/).length).toBe(3);
  });
});
