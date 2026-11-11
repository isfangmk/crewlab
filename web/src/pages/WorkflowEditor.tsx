// 工作流编排页：左侧节点库、中间画布、右侧属性面板。
//
// 画布只是定义的视图：节点坐标写回 definition.nodes[i].x/y，连线写回 edges，
// 保存时整份定义交给后端校验。所有结构限制（唯一开始节点、结束节点无出边、
// 分支必须带 route）在交互层先拦一次，最终仍以后端校验为准。

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Background,
  BackgroundVariant,
  MiniMap,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
  useViewport,
  type Connection,
  type EdgeChange,
  type NodeChange,
  type OnConnect,
  type OnEdgesChange,
  type OnNodesChange,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { ApiError } from '@/api/client';
import {
  deleteWorkflow,
  getCatalog,
  getWorkflow,
  publishWorkflow,
  updateWorkflow,
  validateDefinition,
  type CatalogGroup,
  type CatalogKind,
  type NodeKind,
  type NodeProbeResult,
  type ValidationResult,
  type WorkflowCatalog,
  type WorkflowDefinition,
  type WorkflowDetail,
  type WorkflowNode,
  type WorkflowRun,
} from '@/api/workflows';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { NodeRunDialog } from '@/components/workflow/NodeRunDialog';
import { RunWorkflowDialog } from '@/components/workflow/RunWorkflowDialog';
import { WorkflowInspector, type PanelTabKey, type WorkflowMeta } from '@/components/workflow/WorkflowInspector';
import { WorkflowNodeCard, type CanvasFlowNode } from '@/components/workflow/WorkflowNodeCard';
import { WorkflowConnectionLine, WorkflowEdge, type CanvasFlowEdge } from '@/components/workflow/WorkflowEdge';
import { WorkflowPalette } from '@/components/workflow/WorkflowPalette';
import { Button, PageHeader, RowAction, Toast, type ToastTone } from '@/components/ui';
import { Hint } from '@/components/ui/Tooltip';
import { cn } from '@/lib/cn';
import { useI18n } from '@/i18n/locale';
import { useThemeMode } from '@/store/mode';
import {
  autoOutputKey,
  emptyNode,
  kindTitle,
  layeredLayout,
  nextNodeId,
  nodeIssue,
  nodeSummary,
  toneOf,
  withAutoOutputKeys,
} from '@/lib/workflowMeta';
import { AlertTriangle, ArrowLeft, Check, LayoutTemplate, Minus, Pencil, Play, Plus, RefreshCw, Rocket, Save, ShieldCheck, Trash2, X } from 'lucide-react';

const KIND_MIME = 'application/x-workflow-kind';
const NODE_TYPES = { workflow: WorkflowNodeCard };
const EDGE_TYPES = { workflow: WorkflowEdge };
// 自动存草稿的静默间隔：停手 2 秒就落盘，边填表单边丢改动的概率很低。
const AUTO_SAVE_DELAY_MS = 2000;
// 稳定空数组：catalog 未就绪时避免每次渲染都换新引用（会触发画布同步的 effect）。
const EMPTY_KINDS: CatalogKind[] = [];
const EMPTY_GROUPS: CatalogGroup[] = [];

export default function WorkflowEditorPage() {
  const { id } = useParams();
  const workflowId = Number(id);
  if (!Number.isInteger(workflowId) || workflowId <= 0) {
    return <InvalidId />;
  }
  return (
    <ReactFlowProvider>
      <EditorScreen workflowId={workflowId} />
    </ReactFlowProvider>
  );
}

function InvalidId() {
  const { tr } = useI18n();
  return (
    <main className="flex flex-1 items-center justify-center">
      <div className="rounded-xl border border-zinc-800/60 bg-zinc-900/40 px-6 py-8 text-center">
        <div className="text-[13px] text-zinc-200">{tr('工作流 ID 不合法', 'Invalid workflow ID')}</div>
        <div className="mt-1 text-[11.5px] text-zinc-500">{tr('请从工作流列表进入编排页', 'Open the editor from the workflow list')}</div>
      </div>
    </main>
  );
}

function EditorScreen({ workflowId }: { workflowId: number }) {
  const { tr, locale } = useI18n();
  const zh = locale === 'zh-CN';
  const navigate = useNavigate();
  const { resolved } = useThemeMode();
  const { screenToFlowPosition, zoomIn, zoomOut, fitView } = useReactFlow();
  const { zoom } = useViewport();
  const canvasRef = useRef<HTMLDivElement | null>(null);
  // 最新定义的引用：异步保存回来时判断画布有没有被继续改动。
  const definitionRef = useRef<WorkflowDefinition>({ nodes: [], edges: [] });

  const [detail, setDetail] = useState<WorkflowDetail | null>(null);
  const [definition, setDefinition] = useState<WorkflowDefinition>({ nodes: [], edges: [] });
  const [meta, setMeta] = useState<WorkflowMeta>({ name: '', description: '', status: 'draft' });
  const [catalog, setCatalog] = useState<WorkflowCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [dirty, setDirty] = useState(false);
  // 最近一次成功落盘的时间：静默自动保存靠它给用户回执。
  const [savedAt, setSavedAt] = useState<Date | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // 面板与 Dify 一样按需出现：选中节点开面板，关闭后画布恢复整宽。
  const [panel, setPanel] = useState<'node' | 'workflow' | null>(null);
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [checking, setChecking] = useState(false);
  const [toast, setToast] = useState<{ tone: ToastTone; text: string } | null>(null);
  const [runOpen, setRunOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  // 单节点试跑：待填输入的节点 id、正在跑的节点 id、最近一次试跑结果。
  const [probeNodeId, setProbeNodeId] = useState<string | null>(null);
  const [runningNodeId, setRunningNodeId] = useState<string | null>(null);
  const [probe, setProbe] = useState<NodeProbeResult | null>(null);
  const [panelTab, setPanelTab] = useState<PanelTabKey>('settings');

  const flash = useCallback((tone: ToastTone, text: string) => {
    setToast({ tone, text });
    window.setTimeout(() => setToast(null), 4000);
  }, []);

  // ---- 加载 ---------------------------------------------------------------
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [workflow, catalogResult] = await Promise.all([getWorkflow(workflowId), getCatalog()]);
      setDetail(workflow);
      // 老定义里的 output_key 是空的，这里补成节点 ID，用户不用再自己起变量名。
      setDefinition(withAutoOutputKeys(workflow.definition));
      setMeta({ name: workflow.name, description: workflow.description, status: workflow.status });
      setCatalog(catalogResult);
      setLoadError(null);
    } catch (error) {
      setLoadError(error instanceof ApiError ? error.message : (error as Error).message);
    } finally {
      setLoading(false);
    }
  }, [workflowId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!dirty) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  useEffect(() => {
    definitionRef.current = definition;
  }, [definition]);

  const kinds = catalog?.node_kinds ?? EMPTY_KINDS;
  const groups = catalog?.node_groups ?? EMPTY_GROUPS;
  const startNode = definition.nodes.find((node) => node.kind === 'start') ?? null;
  const selected = definition.nodes.find((node) => node.id === selectedId) ?? null;
  // 画布上有多少节点还没填全：顶部提示、自动保存与发布共用这一份判断。
  const issueCount = definition.nodes.filter((node) => nodeIssue(node, zh) !== null).length;
  // 自动保存的前提：有名字且节点都填全，半成品定义后端必填校验会直接拒绝。
  const canAutoSave = detail !== null && meta.name.trim() !== '' && issueCount === 0;

  // ---- 保存草稿 / 自动保存 / 发布 -------------------------------------------
  // 画布保存只落草稿：不动版本号，也不改发布状态；升版本只发生在「发布」。
  const save = useCallback(
    async (options: { silent?: boolean } = {}): Promise<WorkflowDetail | null> => {
      if (meta.name.trim() === '') {
        flash('err', tr('工作流名称不能为空', 'Name is required'));
        return null;
      }
      setSaving(true);
      // 请求期间用户可能继续改画布：拿引用判断这段时间有没有新改动。
      const submitted = definition;
      try {
        const updated = await updateWorkflow(workflowId, {
          name: meta.name.trim(),
          description: meta.description,
          definition: submitted,
        });
        setDetail(updated);
        setSavedAt(new Date());
        // 有新改动就不回滚本地定义，否则会把用户刚拖的节点弹回服务端那份。
        if (definitionRef.current === submitted) {
          setDefinition(updated.definition);
          setDirty(false);
          setValidation(null);
        }
        // 只同步服务端的状态，名称/描述可能已经在请求期间被继续编辑。
        setMeta((current) => ({ ...current, status: updated.status }));
        if (options.silent !== true) {
          flash('ok', tr('草稿已保存', 'Draft saved'));
        }
        return updated;
      } catch (error) {
        flash('err', error instanceof ApiError ? error.message : (error as Error).message);
        return null;
      } finally {
        setSaving(false);
      }
    },
    [definition, flash, meta.description, meta.name, tr, workflowId],
  );

  // 自动存草稿：停手 2 秒落一次盘。节点没填全时先不存，后端必填校验会直接 400。
  useEffect(() => {
    if (!dirty || saving || publishing || !canAutoSave) return;
    const timer = window.setTimeout(() => {
      void save({ silent: true });
    }, AUTO_SAVE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [canAutoSave, dirty, publishing, save, saving]);

  // 发布：先把当前画布落成草稿，再让后端校验并升版本。
  const publish = useCallback(async () => {
    if (meta.name.trim() === '') {
      flash('err', tr('工作流名称不能为空', 'Name is required'));
      return;
    }
    if (issueCount > 0) {
      flash('err', tr(`还有 ${issueCount} 个节点未配置完整，无法发布`, `${issueCount} node(s) incomplete, cannot publish`));
      return;
    }
    // 发布的就是画布当前内容：有未落盘的改动先存草稿，别把旧定义发布出去。
    if (dirty) {
      const saved = await save({ silent: true });
      if (saved === null) return;
    }
    // 发布请求返回前画布可能又被改了：记下这份定义，回来时只在没变的情况下对齐。
    const canvasAtPublish = definitionRef.current;
    setPublishing(true);
    try {
      const published = await publishWorkflow(workflowId);
      setDetail(published);
      if (definitionRef.current === canvasAtPublish) {
        setDefinition(withAutoOutputKeys(published.definition));
        setDirty(false);
        setValidation(null);
      }
      // 只同步状态：名称/描述可能还在被编辑。
      setMeta((current) => ({ ...current, status: published.status }));
      setSavedAt(new Date());
      flash('ok', tr(`已发布 v${published.version}`, `Published v${published.version}`));
    } catch (error) {
      flash('err', error instanceof ApiError ? error.message : (error as Error).message);
    } finally {
      setPublishing(false);
    }
  }, [dirty, flash, issueCount, meta.name, save, tr, workflowId]);

  // ---- 定义更新 -----------------------------------------------------------
  const patchNode = useCallback((nodeId: string, patch: Partial<WorkflowNode>) => {
    setDefinition((current) => ({
      ...current,
      nodes: current.nodes.map((node) => (node.id === nodeId ? { ...node, ...patch } : node)),
    }));
    setDirty(true);
  }, []);

  // 单节点试跑：先收输入（开始节点声明的变量），再让后端真实执行到这一步。
  const startNodeRun = useCallback(async (nodeId: string) => {
    const target = definition.nodes.find((node) => node.id === nodeId);
    if (target === undefined) return;
    if (target.kind === 'start') {
      flash('err', tr('开始节点没有上游逻辑可试跑，请直接运行工作流', 'The start node has nothing upstream; run the workflow instead'));
      return;
    }
    if (target.kind === 'approval' || target.kind === 'input') {
      flash('err', tr('人工节点不能试跑，需要在完整运行里提交', 'Human-in-the-loop nodes cannot be test-run'));
      return;
    }
    if (startNode === null) {
      flash('err', tr('画布缺少开始节点，无法试跑', 'No start node, cannot test-run'));
      return;
    }
    if (dirty) {
      // 后端试跑读的是已保存的定义：先自动落一次草稿，用户不用先手点保存。
      const saved = await save();
      if (saved === null) return;
    }
    setSelectedId(nodeId);
    setPanel('node');
    setPanelTab('settings');
    setProbeNodeId(nodeId);
  }, [definition.nodes, dirty, flash, save, startNode, tr]);

  const addNode = useCallback(
    (kind: NodeKind, position?: { x: number; y: number }) => {
      if (kind === 'start' && startNode !== null) {
        flash('err', tr('画布上只能有一个开始节点', 'Only one start node is allowed'));
        return;
      }
      const id = nextNodeId(kind, definition.nodes);
      const base = position ?? viewportCenter(screenToFlowPosition, canvasRef.current) ?? { x: 120, y: 120 };
      // 多个节点叠在同一个点会让用户以为是 bug：按已有节点数量错开一点。
      const offset = (definition.nodes.length % 5) * 24;
      const node = emptyNode(kind, id, { x: base.x + offset, y: base.y + offset });
      if (kind === 'start') {
        node.variables = [{ name: 'input', type: 'string', description: '', required: true, default: null }];
      }
      setDefinition((current) => ({ ...current, nodes: [...current.nodes, node] }));
      setSelectedId(id);
      setPanel('node');
      setDirty(true);
    },
    [definition.nodes, flash, screenToFlowPosition, startNode, tr],
  );

  /** 复制节点：同类型、同配置，坐标错开一点，不带连线。 */
  const duplicateNode = useCallback(
    (nodeId: string) => {
      const source = definition.nodes.find((node) => node.id === nodeId);
      if (source === undefined) return;
      if (source.kind === 'start') {
        flash('err', tr('开始节点全图唯一，不能复制', 'Only one start node is allowed'));
        return;
      }
      const id = nextNodeId(source.kind, definition.nodes);
      setDefinition((current) => ({
        ...current,
        nodes: [
          ...current.nodes,
          // 复制出来的节点是一条新链路，结果变量要跟着新 ID 走，不能沿用原节点的名字。
          { ...source, id, output_key: autoOutputKey(source.kind, id), x: source.x + 40, y: source.y + 40 },
        ],
      }));
      setSelectedId(id);
      setPanel('node');
      setDirty(true);
    },
    [definition.nodes, flash, tr],
  );

  const removeNode = useCallback((nodeId: string) => {
    setDefinition((current) => ({
      nodes: current.nodes.filter((node) => node.id !== nodeId),
      edges: current.edges.filter((edge) => edge.source !== nodeId && edge.target !== nodeId),
    }));
    setSelectedId((current) => (current === nodeId ? null : current));
    // 删掉的正是当前面板里的节点，面板一起收起，避免展示空壳。
    setPanel((current) => (current === 'node' && selectedId === nodeId ? null : current));
    setDirty(true);
  }, [selectedId]);

  /** 连线中间插入节点：原连线拆成「上游 → 新节点 → 下游」，分支名留在上游那段。 */
  const insertOnEdge = useCallback(
    (edgeId: string, kind: NodeKind) => {
      const edge = definition.edges.find((item) => item.id === edgeId);
      if (edge === undefined) return;
      const source = definition.nodes.find((node) => node.id === edge.source);
      const target = definition.nodes.find((node) => node.id === edge.target);
      if (source === undefined || target === undefined) return;

      const id = nextNodeId(kind, definition.nodes);
      const node = emptyNode(kind, id, {
        x: Math.round((source.x + target.x) / 2),
        y: Math.round((source.y + target.y) / 2),
      });
      const suffix = Math.random().toString(36).slice(2, 6);
      setDefinition({
        nodes: [...definition.nodes, node],
        edges: [
          ...definition.edges.filter((item) => item.id !== edgeId),
          { id: `e_${source.id}_${id}_${suffix}`, source: source.id, target: id, route: edge.route },
          { id: `e_${id}_${target.id}_${suffix}`, source: id, target: target.id, route: '' },
        ],
      });
      setSelectedId(id);
      setPanel('node');
      setDirty(true);
    },
    [definition],
  );

  // 定义一改，之前的试跑结果就过期了，不能继续挂在面板上误导人。
  useEffect(() => {
    setProbe(null);
  }, [definition]);

  // ---- React Flow 状态 ----------------------------------------------------
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState<CanvasFlowNode>([]);
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState<CanvasFlowEdge>([]);

  useEffect(() => {
    setRfNodes(
      definition.nodes.map((node) => ({
        id: node.id,
        type: 'workflow' as const,
        position: { x: node.x, y: node.y },
        selected: node.id === selectedId,
        data: {
          node,
          title: kindTitle(node.kind, kinds, zh),
          summary: nodeSummary(node, zh),
          issue: nodeIssue(node, zh),
          onRun: () => void startNodeRun(node.id),
          running: runningNodeId === node.id,
          onDuplicate: () => duplicateNode(node.id),
          onDelete: () => removeNode(node.id),
        },
      })),
    );
  }, [definition, kinds, removeNode, duplicateNode, runningNodeId, selectedId, setRfNodes, startNodeRun, zh]);

  // 连线中间只允许插入「普通执行节点」：开始节点全图唯一，结束节点必须是叶子。
  const insertableKinds = useMemo(
    () => kinds.filter((kind) => kind.key !== 'start' && kind.key !== 'end'),
    [kinds],
  );

  useEffect(() => {
    setRfEdges(
      definition.edges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        type: 'workflow' as const,
        data: { route: edge.route, kinds: insertableKinds, onInsert: (kind: NodeKind) => insertOnEdge(edge.id, kind) },
      })),
    );
  }, [definition, insertOnEdge, insertableKinds, setRfEdges]);

  const handleNodesChange: OnNodesChange<CanvasFlowNode> = useCallback(
    (changes: NodeChange<CanvasFlowNode>[]) => {
      onNodesChange(changes);
      const removed = changes.filter((change) => change.type === 'remove');
      if (removed.length > 0) {
        const ids = new Set(removed.map((change) => change.id));
        setDefinition((current) => ({
          nodes: current.nodes.filter((node) => !ids.has(node.id)),
          edges: current.edges.filter((edge) => !ids.has(edge.source) && !ids.has(edge.target)),
        }));
        setDirty(true);
      }
    },
    [onNodesChange],
  );

  const handleEdgesChange: OnEdgesChange<CanvasFlowEdge> = useCallback(
    (changes: EdgeChange<CanvasFlowEdge>[]) => {
      onEdgesChange(changes);
      const removed = changes.filter((change) => change.type === 'remove');
      if (removed.length > 0) {
        const ids = new Set(removed.map((change) => change.id));
        setDefinition((current) => ({ ...current, edges: current.edges.filter((edge) => !ids.has(edge.id)) }));
        setDirty(true);
      }
    },
    [onEdgesChange],
  );

  const onConnect: OnConnect = useCallback(
    (connection: Connection) => {
      const { source, target } = connection;
      if (source === null || target === null) return;
      const sourceNode = definition.nodes.find((node) => node.id === source);
      const targetNode = definition.nodes.find((node) => node.id === target);
      if (!sourceNode || !targetNode) return;

      if (source === target) {
        flash('err', tr('不能连接自己', 'A node cannot connect to itself'));
        return;
      }
      if (targetNode.kind === 'start') {
        flash('err', tr('开始节点不能有入边', 'The start node cannot have incoming edges'));
        return;
      }
      if (sourceNode.kind === 'end') {
        flash('err', tr('结束节点不能有出边', 'The end node cannot have outgoing edges'));
        return;
      }

      // 条件分支与循环的出边必须带 route：新连线自动挑一个还没用的。
      let route = '';
      if (sourceNode.kind === 'switch') {
        const used = new Set(definition.edges.filter((edge) => edge.source === source).map((edge) => edge.route));
        const free = sourceNode.branches.find((branch) => !used.has(branch));
        if (free === undefined) {
          flash('err', tr('该分支的所有出口都已被占用，请先修改分支名', 'All branches already have edges; rename a branch first'));
          return;
        }
        route = free;
      } else if (sourceNode.kind === 'loop') {
        const used = new Set(definition.edges.filter((edge) => edge.source === source).map((edge) => edge.route));
        route = used.has('exit') ? 'loop' : 'exit';
      }
      if (targetNode.kind === 'join' && route !== '') {
        flash('err', tr('汇合节点只接受无条件边', 'Join nodes only accept unconditional edges'));
        return;
      }
      if (definition.edges.some((edge) => edge.source === source && edge.target === target && edge.route === route)) {
        return;
      }
      const id = `e_${source}_${target}_${route === '' ? 'any' : route}_${Math.random().toString(36).slice(2, 6)}`;
      setDefinition((current) => ({ ...current, edges: [...current.edges, { id, source, target, route }] }));
      setDirty(true);
    },
    [definition.edges, definition.nodes, flash, tr],
  );

  const autoLayout = useCallback(() => {
    const positions = layeredLayout(definition);
    setDefinition((current) => ({
      ...current,
      nodes: current.nodes.map((node) => ({ ...node, x: positions[node.id]?.x ?? node.x, y: positions[node.id]?.y ?? node.y })),
    }));
    setDirty(true);
    window.setTimeout(() => void fitView({ padding: 0.22, minZoom: 0.7, duration: 300 }), 30);
  }, [definition, fitView]);

  // ---- 校验 / 运行 ---------------------------------------------------------
  const check = useCallback(async () => {
    setChecking(true);
    try {
      const result = await validateDefinition(definition);
      setValidation(result);
    } catch (error) {
      flash('err', error instanceof ApiError ? error.message : (error as Error).message);
    } finally {
      setChecking(false);
    }
  }, [definition, flash]);

  const openRun = useCallback(async () => {
    if (dirty) {
      const saved = await save();
      if (saved === null) return;
    }
    setRunOpen(true);
  }, [dirty, save]);

  const onStarted = useCallback(
    (run: WorkflowRun) => {
      setRunOpen(false);
      navigate(`/workflows/runs/${run.run_uid}`);
    },
    [navigate],
  );

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
        event.preventDefault();
        void save();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [save]);

  const dotColor = resolved === 'light' ? '#cbd5e1' : '#27272a';

  if (loading) {
    return (
      <main className="flex flex-1 items-center justify-center text-[12px] text-zinc-500">{tr('加载工作流…', 'Loading workflow…')}</main>
    );
  }

  if (loadError !== null || detail === null) {
    return (
      <main className="flex flex-1 flex-col overflow-hidden">
        <PageHeader
          title={tr('工作流', 'Workflow')}
          leading={
            <button type="button" onClick={() => navigate('/workflows')} className="inline-flex items-center gap-1 text-[11px] text-zinc-500 hover:text-zinc-300">
              <ArrowLeft size={12} aria-hidden />
              {tr('返回工作流', 'Back to workflows')}
            </button>
          }
        />
        <div className="p-6">
          <div role="alert" className="rounded-xl border border-red-500/40 bg-red-500/5 px-4 py-3 text-[12px] text-red-300">
            {loadError ?? tr('工作流不存在', 'Workflow not found')}
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={
          // 名称与描述没有单独的「设置」入口：点标题就打开信息面板。
          <button
            type="button"
            onClick={() => {
              setSelectedId(null);
              setPanel('workflow');
            }}
            title={tr('编辑名称与描述', 'Edit name and description')}
            className="group inline-flex items-center gap-1.5 text-left"
          >
            {meta.name === '' ? tr('未命名工作流', 'Untitled workflow') : meta.name}
            <Pencil size={11} className="shrink-0 text-zinc-600 opacity-0 transition-opacity group-hover:opacity-100" aria-hidden />
          </button>
        }
        subtitle={
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className={cn('rounded-full border px-1.5 text-[10.5px] leading-4', meta.status === 'published' ? 'border-ok/30 bg-ok/10 text-ok' : 'border-zinc-700 bg-zinc-800/40 text-zinc-400')}>
              {meta.status === 'published' ? tr('已发布', 'Published') : tr('草稿', 'Draft')}
            </span>
            <span className="tabular-nums">v{detail.version}</span>
            <span className="text-zinc-700">·</span>
            <span className="tabular-nums">{tr(`${definition.nodes.length} 节点 / ${definition.edges.length} 连线`, `${definition.nodes.length} nodes / ${definition.edges.length} edges`)}</span>
            {issueCount > 0 && (
              <>
                <span className="text-zinc-700">·</span>
                <span className="text-warn">{tr(`${issueCount} 个节点未配置完整`, `${issueCount} node(s) incomplete`)}</span>
              </>
            )}
            {dirty && (
              <>
                <span className="text-zinc-700">·</span>
                {canAutoSave ? (
                  // 自动保存是静默的：正在存、已存都在这行给回执。
                  <span className={saving ? 'text-indigo-300' : 'text-text-faint'}>
                    {saving ? tr('正在自动保存…', 'Auto-saving…') : tr('待自动保存…', 'Pending auto-save…')}
                  </span>
                ) : (
                  <span className="text-warn">{tr('节点未填全，暂不自动保存', 'Auto-save paused: nodes incomplete')}</span>
                )}
              </>
            )}
            {!dirty && savedAt !== null && (
              <>
                <span className="text-zinc-700">·</span>
                <span className="text-text-faint tabular-nums">
                  {tr('草稿已自动保存', 'Draft auto-saved')} {savedAt.toLocaleTimeString(zh ? 'zh-CN' : 'en-US', { hour12: false })}
                </span>
              </>
            )}
          </span>
        }
        leading={
          <button type="button" onClick={() => navigate('/workflows')} className="inline-flex items-center gap-1 text-[11px] text-zinc-500 hover:text-zinc-300">
            <ArrowLeft size={12} aria-hidden />
            {tr('返回工作流', 'Back to workflows')}
          </button>
        }
        actions={
          <>
            <RowAction icon={ShieldCheck} onClick={() => void check()} disabled={checking || saving} iconClassName={cn(checking && 'animate-pulse')}>
              {tr('校验', 'Validate')}
            </RowAction>
            <RowAction icon={Play} onClick={() => void openRun()} disabled={saving}>
              {tr('运行', 'Run')}
            </RowAction>
            <RowAction icon={Trash2} tone="danger" onClick={() => setConfirmDelete(true)} disabled={saving}>
              {tr('删除', 'Delete')}
            </RowAction>
            {/* 自动保存兜住日常改动，手动保存只作为「立刻落盘」的兜底入口。 */}
            <RowAction icon={Save} onClick={() => void save()} disabled={saving || !dirty}>
              {saving ? tr('保存中…', 'Saving…') : tr('保存', 'Save')}
            </RowAction>
            <Button
              variant="primary"
              size="sm"
              className="h-7 px-3 text-[11.5px]"
              onClick={() => void publish()}
              disabled={publishing || saving}
            >
              <Rocket size={12} aria-hidden />
              {publishing ? tr('发布中…', 'Publishing…') : tr('发布', 'Publish')}
            </Button>
          </>
        }
      />

      <div className="flex min-h-0 flex-1">
        <WorkflowPalette
          groups={groups}
          kinds={kinds}
          disabledKinds={new Set(startNode === null ? [] : (['start'] as NodeKind[]))}
          onAdd={(kind) => addNode(kind)}
          onDragStart={(kind, event) => {
            event.dataTransfer.setData(KIND_MIME, kind);
            event.dataTransfer.effectAllowed = 'move';
          }}
        />

        <div ref={canvasRef} className="relative min-w-0 flex-1">
          <ReactFlow
            nodes={rfNodes}
            edges={rfEdges}
            nodeTypes={NODE_TYPES}
            edgeTypes={EDGE_TYPES}
            connectionLineComponent={WorkflowConnectionLine}
            onNodesChange={handleNodesChange}
            onEdgesChange={handleEdgesChange}
            onConnect={onConnect}
            onNodeClick={(_, node) => {
              setSelectedId(node.id);
              setPanel('node');
              setPanelTab('settings');
            }}
            onPaneClick={() => {
              setSelectedId(null);
              setPanel(null);
            }}
            onNodeDragStop={(_, node) => {
              patchNode(node.id, { x: Math.round(node.position.x), y: Math.round(node.position.y) });
            }}
            onDragOver={(event) => {
              event.preventDefault();
              event.dataTransfer.dropEffect = 'move';
            }}
            onDrop={(event) => {
              const kind = event.dataTransfer.getData(KIND_MIME);
              if (kind === '') return;
              event.preventDefault();
              event.stopPropagation();
              addNode(kind as NodeKind, screenToFlowPosition({ x: event.clientX, y: event.clientY }));
            }}
            fitView
            // 缩到 0.7 以下节点文字就看不清了：宁可让大图横向可拖，也不默认整图缩小。
            fitViewOptions={{ padding: 0.24, minZoom: 0.7, maxZoom: 1 }}
            minZoom={0.25}
            maxZoom={1.6}
            proOptions={{ hideAttribution: false }}
            className="bg-bg"
            defaultEdgeOptions={{ type: 'workflow' }}
            // 左键拖动平移画布（Dify 的手感）；框选改成按住 Shift 再拖，
            // 这样不需要记「右键拖」这种不直观的操作。
            panOnDrag
            selectionKeyCode="Shift"
          >
            <Background variant={BackgroundVariant.Dots} gap={16} size={1.5} color={dotColor} />

            {definition.nodes.length <= 1 && (
              <Panel position="top-left" className="!m-3">
                <div className="rounded-lg border border-dashed border-border/70 bg-card/70 px-3 py-2 text-[11px] leading-5 text-text-faint backdrop-blur">
                  {tr('把左侧节点拖到画布上，或点节点之间的 + 插入', 'Drag a node from the left, or use + on an edge to insert')}
                </div>
              </Panel>
            )}

            <MiniMap
              pannable
              zoomable
              nodeStrokeWidth={0}
              style={{ width: 132, height: 84 }}
              className="!bottom-16 !left-auto !right-3 !m-0 !rounded-xl !border !border-border/70 !bg-card/90 !shadow-lg"
              maskColor={resolved === 'light' ? 'rgba(244,245,248,0.72)' : 'rgba(11,11,17,0.72)'}
              nodeColor={(node) => {
                const kind = (node.data as { node?: WorkflowNode } | undefined)?.node?.kind;
                return kind === undefined ? '#71717a' : toneOf(kind).dot;
              }}
            />

            {/* 右下角：与 Dify 一致的缩放 / 适应 / 整理工具条 */}
            <Panel position="bottom-right" className="!m-3">
              <div className="flex items-center gap-0.5 rounded-xl border border-border/70 bg-card/95 px-1 py-0.5 shadow-lg backdrop-blur">
                <IconButton label={tr('缩小', 'Zoom out')} onClick={() => void zoomOut({ duration: 120 })}>
                  <Minus size={13} aria-hidden />
                </IconButton>
                <button
                  type="button"
                  onClick={() => void fitView({ padding: 0.22, minZoom: 0.7, duration: 240 })}
                  className="h-7 min-w-[44px] rounded-md px-1 text-[11px] tabular-nums text-text-muted transition-colors hover:bg-zinc-500/10 hover:text-text"
                  title={tr('适应画布', 'Fit view')}
                >
                  {Math.round(zoom * 100)}%
                </button>
                <IconButton label={tr('放大', 'Zoom in')} onClick={() => void zoomIn({ duration: 120 })}>
                  <Plus size={13} aria-hidden />
                </IconButton>
                <span className="mx-0.5 h-4 w-px bg-border/70" aria-hidden />
                <IconButton label={tr('适应画布', 'Fit view')} onClick={() => void fitView({ padding: 0.22, minZoom: 0.7, duration: 240 })}>
                  <RefreshCw size={12} aria-hidden />
                </IconButton>
                <span className="mx-0.5 h-4 w-px bg-border/70" aria-hidden />
                <button
                  type="button"
                  onClick={autoLayout}
                  className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-[11px] text-text-muted transition-colors hover:bg-zinc-500/10 hover:text-text"
                >
                  <LayoutTemplate size={12} aria-hidden />
                  {tr('整理布局', 'Auto layout')}
                </button>
              </div>
            </Panel>

            {validation !== null && (
              <Panel position="bottom-center" className="!mb-16 !max-w-[min(640px,90%)]">
                <ValidationBanner result={validation} onClose={() => setValidation(null)} />
              </Panel>
            )}
          </ReactFlow>
        </div>

        {panel !== null && (
        <WorkflowInspector
          workflowId={workflowId}
          catalog={catalog}
          kinds={kinds}
          definition={definition}
          node={panel === 'node' ? selected : null}
          meta={meta}
            onNodeChange={(patch) => {
              if (selected === null) return;
              patchNode(selected.id, patch);
            }}
            onDeleteNode={() => {
              if (selected !== null) removeNode(selected.id);
            }}
            onDuplicateNode={() => {
              if (selected !== null) duplicateNode(selected.id);
            }}
            onRunNode={() => {
              if (selected !== null) startNodeRun(selected.id);
            }}
            running={selected !== null && runningNodeId === selected.id}
            probe={probe}
            tab={panelTab}
            onTabChange={setPanelTab}
            onClose={() => {
              setPanel(null);
              setSelectedId(null);
            }}
            onMetaChange={(patch) => {
              setMeta((current) => ({ ...current, ...patch }));
              setDirty(true);
            }}
        />
        )}
      </div>

      {probeNodeId !== null && (
        <NodeRunDialog
          workflowId={workflowId}
          definition={definition}
          nodeId={probeNodeId}
          nodeTitle={definition.nodes.find((item) => item.id === probeNodeId)?.label || probeNodeId}
          onClose={() => setProbeNodeId(null)}
          onBusyChange={(busy) => setRunningNodeId(busy ? probeNodeId : null)}
          onDone={(result) => {
            setProbeNodeId(null);
            setProbe(result);
            setSelectedId(result.node_id);
            setPanel('node');
            setPanelTab('lastRun');
          }}
        />
      )}

      {runOpen && (
        <RunWorkflowDialog
          workflowId={workflowId}
          workflowName={meta.name}
          definition={definition}
          onClose={() => setRunOpen(false)}
          onStarted={onStarted}
        />
      )}

      <ConfirmDialog
        open={confirmDelete}
        title={tr('删除工作流', 'Delete workflow')}
        description={tr(
          `确定删除「${meta.name}」？画布定义与历史运行记录会一并删除，无法恢复。`,
          `Delete "${meta.name}"? Its definition and run history are removed permanently.`,
        )}
        confirmLabel={tr('删除', 'Delete')}
        onClose={() => setConfirmDelete(false)}
        onConfirm={async () => {
          await deleteWorkflow(workflowId);
          navigate('/workflows');
        }}
      />

      {toast !== null && <Toast tone={toast.tone} text={toast.text} />}
    </main>
  );
}

function IconButton({ label, onClick, children }: { label: string; onClick(): void; children: ReactNode }) {
  return (
    <Hint content={label}>
      <button
        type="button"
        aria-label={label}
        onClick={onClick}
        className="inline-flex h-7 w-7 items-center justify-center rounded-md text-zinc-400 transition-colors hover:bg-zinc-800/70 hover:text-zinc-200"
      >
        {children}
      </button>
    </Hint>
  );
}

function ValidationBanner({ result, onClose }: { result: ValidationResult; onClose(): void }) {
  const { tr } = useI18n();
  const ok = result.ok;
  return (
    <div
      role="status"
      className={cn(
        'w-full overflow-hidden rounded-xl border px-3 py-2.5 text-[11.5px] shadow-xl backdrop-blur',
        ok ? 'border-emerald-500/30 bg-emerald-500/[0.07]' : 'border-red-500/40 bg-red-500/[0.07]',
      )}
    >
      <div className="flex items-start gap-2">
        <span className={cn('mt-[1px]', ok ? 'text-emerald-400' : 'text-red-400')}>
          {ok ? <Check size={13} aria-hidden /> : <AlertTriangle size={13} aria-hidden />}
        </span>
        <div className="min-w-0 flex-1">
          <div className={cn('font-medium', ok ? 'text-emerald-200' : 'text-red-200')}>
            {ok ? tr('画布校验通过，可以运行', 'Canvas is valid and can run') : tr('画布校验不通过', 'Canvas validation failed')}
          </div>
          {!ok && <div className="mt-1 break-words font-mono text-[11px] leading-5 text-red-300">{result.error}</div>}
          {result.warnings.length > 0 && (
            <ul className="mt-1.5 space-y-0.5 border-t border-zinc-700/40 pt-1.5 text-[10.5px] leading-4 text-amber-200/90">
              {result.warnings.map((warning) => (
                <li key={warning}>· {warning}</li>
              ))}
            </ul>
          )}
        </div>
        <button
          type="button"
          aria-label={tr('关闭校验结果', 'Dismiss validation result')}
          onClick={onClose}
          className="shrink-0 rounded p-0.5 text-zinc-500 hover:bg-zinc-800/60 hover:text-zinc-300"
        >
          <X size={12} aria-hidden />
        </button>
      </div>
    </div>
  );
}

/** 视口中心对应的画布坐标；画布还没挂载时返回 null，由调用方给默认位置。 */
function viewportCenter(
  screenToFlowPosition: (position: { x: number; y: number }) => { x: number; y: number },
  element: HTMLDivElement | null,
): { x: number; y: number } | null {
  if (element === null) return null;
  const rect = element.getBoundingClientRect();
  if (rect.width === 0 || rect.height === 0) return null;
  return screenToFlowPosition({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
}
