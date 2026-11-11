// 编排页画布交互测试：
//   1. 画布必须「左键拖动平移」——不是右键拖，也不是左键框选；
//   2. 框选退到 Shift+拖动，避免左键语义冲突；
//   3. 页面能真实渲染出定义里的节点（证明上面两条断言取自真实挂载的画布）。
//
// React Flow 在 jsdom 里依赖布局能力（ResizeObserver / DOMMatrix），这里把
// 画布组件换成会记录 props 的壳：断言的是「页面给画布传了什么交互配置」，
// 这正是这次改动会回归的地方。
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';

import WorkflowEditorPage from './WorkflowEditor';
import { server } from '@/test/msw-server';
import { emptyNode } from '@/lib/workflowMeta';
import type { WorkflowDefinition } from '@/api/workflows';

const captured = vi.hoisted(() => ({
  props: null as null | Record<string, unknown>,
}));

vi.mock('@xyflow/react', () => {
  const ReactFlow = (props: Record<string, unknown> & { children?: ReactNode }) => {
    captured.props = props;
    return <div data-testid="canvas">{props.children as ReactNode}</div>;
  };
  return {
    ReactFlow,
    ReactFlowProvider: ({ children }: { children?: ReactNode }) => <>{children}</>,
    Background: () => null,
    BackgroundVariant: { Dots: 'dots' },
    MiniMap: () => null,
    Panel: ({ children }: { children?: ReactNode }) => <>{children}</>,
    // 画布内部状态：测试只需要渲染初始值，变更回调不重要
    useNodesState: (initial: unknown[]) => [initial, () => {}, () => {}],
    useEdgesState: (initial: unknown[]) => [initial, () => {}, () => {}],
    useReactFlow: () => ({
      screenToFlowPosition: ({ x, y }: { x: number; y: number }) => ({ x, y }),
      zoomIn: () => {},
      zoomOut: () => {},
      zoomTo: () => {},
      setViewport: () => {},
      fitView: () => {},
      setCenter: () => {},
      getZoom: () => 1,
    }),
    useViewport: () => ({ zoom: 1 }),
    // 「适应画布」直接读画布内部状态算包围盒；测试里给空尺寸，走提前返回分支
    useStoreApi: () => ({
      getState: () => ({
        width: 0,
        height: 0,
        nodes: [],
        nodeOrigin: [0, 0] as [number, number],
        nodeLookup: new Map(),
      }),
    }),
    getNodesBounds: () => ({ x: 0, y: 0, width: 0, height: 0 }),
    getViewportForBounds: () => ({ x: 0, y: 0, zoom: 1 }),
  };
});

const detailURL = '/api/v1/workflows/12';
const catalogURL = '/api/v1/workflows/catalog';
// 进入画布会拉上次运行快照；没跑过的返回 run=null
const latestRunURL = '/api/v1/workflows/12/runs/latest';

const catalog = {
  node_groups: [{ key: 'flow', title: '编排', title_en: 'Flow' }],
  node_kinds: [
    { key: 'start', title: '开始', title_en: 'Start', group: 'flow' },
    { key: 'assign', title: '变量赋值', title_en: 'Set variables', group: 'action' },
    { key: 'end', title: '结束', title_en: 'End', group: 'flow' },
  ],
  models: [],
  mcp_tools: [],
};

function definition(): WorkflowDefinition {
  const start = emptyNode('start', 'start', { x: 0, y: 0 });
  start.variables = [
    { name: 'topic', type: 'string', description: '主题', required: true, default: null },
  ];
  const prep = emptyNode('assign', 'prep', { x: 240, y: 0 });
  prep.assignments = { subject: '{{topic}}' };
  const end = emptyNode('end', 'end', { x: 480, y: 0 });
  return {
    nodes: [start, prep, end],
    edges: [
      { id: 'e1', source: 'start', target: 'prep', route: '' },
      { id: 'e2', source: 'prep', target: 'end', route: '' },
    ],
  };
}

function renderEditor() {
  return render(
    <MemoryRouter initialEntries={['/workflows/12']}>
      <Routes>
        <Route path="/workflows/:id" element={<WorkflowEditorPage />} />
        <Route path="/workflows" element={<div>列表页占位</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function canvasProps(): Record<string, unknown> {
  if (captured.props === null) throw new Error('画布没有挂载');
  return captured.props;
}

function detailPayload() {
  return {
    id: 12,
    name: '告警处置',
    description: '',
    status: 'draft',
    version: 1,
    validation_error: '',
    node_count: 3,
    edge_count: 2,
    warnings: [],
    last_run: null,
    created_at: '2026-08-02T03:04:00Z',
    updated_at: '2026-08-02T03:04:00Z',
    definition: definition(),
  };
}

describe('WorkflowEditor 画布交互', () => {
  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
    captured.props = null;
    server.use(
      http.get(detailURL, () => HttpResponse.json(detailPayload())),
      http.get(catalogURL, () => HttpResponse.json(catalog)),
      http.get(latestRunURL, () => HttpResponse.json({ run: null })),
    );
  });

  it('左键拖动就是平移画布，不是右键拖', async () => {
    renderEditor();
    // 节点真的画出来了，说明断言来自真实挂载的画布
    expect(await screen.findByText('告警处置')).toBeInTheDocument();
    expect(screen.getByTestId('canvas')).toBeInTheDocument();

    const props = canvasProps();
    // panOnDrag=true 表示任意鼠标键拖动都平移；数组写法则只对列出的键生效
    expect(props.panOnDrag).toBe(true);
  });

  it('框选改为按住 Shift，避免和左键平移抢同一个手势', async () => {
    renderEditor();
    await screen.findByText('告警处置');

    const props = canvasProps();
    expect(props.selectionKeyCode).toBe('Shift');
    // selectionOnDrag 保留 true 会变成「左键拖=框选」，和上面的平移冲突
    expect(props.selectionOnDrag).toBeUndefined();
  });
});

describe('WorkflowEditor 运行状态恢复', () => {
  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
    captured.props = null;
    server.use(
      http.get(detailURL, () => HttpResponse.json(detailPayload())),
      http.get(catalogURL, () => HttpResponse.json(catalog)),
      http.get(latestRunURL, () => HttpResponse.json({ run: null })),
    );
  });

  it('进入画布就恢复上次运行：停在人工节点时给出等待横幅', async () => {
    server.use(
      http.get(latestRunURL, () =>
        HttpResponse.json({
          run: {
            run_uid: 'run-1',
            status: 'waiting',
            trigger: 'manual',
            workflow_version: 1,
            workflow_status: 'draft',
            gate: 'prep',
            gate_kind: 'approval',
            created_at: '2026-08-02T03:04:00Z',
            finished_at: null,
            nodes: [{ node_id: 'start', status: 'succeeded' }],
          },
        }),
      ),
    );
    renderEditor();
    expect(await screen.findByText('停在「prep」等待审批')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '去处理' })).toBeInTheDocument();
  });

  it('没有运行记录时不显示等待横幅', async () => {
    renderEditor();
    await screen.findByText('告警处置');
    expect(screen.queryByRole('button', { name: '去处理' })).not.toBeInTheDocument();
  });
});

describe('WorkflowEditor 草稿与发布', () => {
  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
    captured.props = null;
    server.use(
      http.get(detailURL, () => HttpResponse.json(detailPayload())),
      http.get(catalogURL, () => HttpResponse.json(catalog)),
      http.get(latestRunURL, () => HttpResponse.json({ run: null })),
    );
  });

  it('画布改动静默自动存草稿：不点保存也会 PUT，且不带状态、不升版本', async () => {
    const puts: Record<string, unknown>[] = [];
    server.use(
      http.put(detailURL, async ({ request }) => {
        const body = (await request.json()) as { name: string; definition: WorkflowDefinition };
        puts.push(body as unknown as Record<string, unknown>);
        return HttpResponse.json({ ...detailPayload(), name: body.name, definition: body.definition });
      }),
    );
    renderEditor();
    await screen.findByText('告警处置');

    // 改名算一次画布改动：点标题打开信息面板，停手后应该自己落盘，不需要点「保存」。
    expect(screen.queryByRole('button', { name: /^设置$/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '告警处置' }));
    // 运行输入只属于开始节点：信息面板里不再有变量编辑
    expect(screen.queryByRole('button', { name: '添加变量' })).not.toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('工作流名称'), '!');

    await waitFor(() => expect(puts.length).toBeGreaterThan(0), { timeout: 4000 });
    // 状态与版本都由后端定：草稿保存只提交内容
    expect(puts[0].status).toBeUndefined();
    // 版本号只在发布时变，草稿保存后仍是 v1
    expect(screen.getByText('v1')).toBeInTheDocument();
    expect(await screen.findByText(/草稿已自动保存/)).toBeInTheDocument();
  });

  it('发布才升版本：点发布走 /publish，成功后状态变已发布、版本 +1', async () => {
    let publishCalls = 0;
    server.use(
      http.post(`${detailURL}/publish`, () => {
        publishCalls += 1;
        return HttpResponse.json({ ...detailPayload(), status: 'published', version: 2 });
      }),
    );
    renderEditor();
    await screen.findByText('告警处置');

    // 没有未保存改动时直接发布，不额外提交草稿
    await userEvent.click(screen.getByRole('button', { name: /发布/ }));

    await waitFor(() => expect(publishCalls).toBe(1));
    expect(await screen.findByText('已发布')).toBeInTheDocument();
    expect(screen.getByText('v2')).toBeInTheDocument();
  });
});
