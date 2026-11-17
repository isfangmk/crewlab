// 草稿卡交互门：回合未收口（pending）时开始执行/编辑禁点且给提示，
// 收口后放开。API 层打桩，Focus 事件订阅是空实现。
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { PlanBlock } from './PlanBlock';

vi.mock('@/api/plan', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/plan')>()),
  getPlan: vi.fn(async () => {
    throw new Error('no snapshot');
  }),
  subscribePlanEvents: vi.fn(async () => {}),
  startPlan: vi.fn(async () => ({ started: true })),
  updatePlanSteps: vi.fn(async () => ({})),
}));

const draftCall = {
  name: 'create_plan',
  status: 'ok',
  result: {
    plan_uid: 'plan-1',
    title: '梳理架构',
    status: 'draft',
    steps: [{ step_uid: 's-1', name: '查讲义', status: 'pending', summary: '', error: '' }],
  },
};

describe('PlanBlock 草稿交互门', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('回合未收口：按钮禁用且给出等待提示', () => {
    render(<PlanBlock call={draftCall} pending interactive />);
    expect(screen.getByRole('button', { name: /开始执行/ })).toBeDisabled();
    expect(screen.getByText(/模型正在输出，结束后可执行/)).toBeInTheDocument();
  });

  it('回合已收口：按钮可点', () => {
    render(<PlanBlock call={draftCall} interactive />);
    expect(screen.getByRole('button', { name: /开始执行/ })).toBeEnabled();
    expect(screen.queryByText(/模型正在输出/)).not.toBeInTheDocument();
  });

  it('只读账号：按钮禁用', () => {
    render(<PlanBlock call={draftCall} pending={false} interactive={false} />);
    expect(screen.getByRole('button', { name: /开始执行/ })).toBeDisabled();
  });
});
