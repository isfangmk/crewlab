// 工作流右侧面板：未选中节点时是「工作流信息」，选中节点后是「节点面板」。
//
// 版式对齐 Dify 的 workflow-panel：
//   1. 头部：实色图标块 + 可就地改名的标题 + 试跑 + 更多操作 + 关闭；
//   2. 页签：设置（节点参数 / 结果变量）与上次运行（该节点的输出）；
//   3. 面板浮在画布右侧而不是挤压画布，左侧有拖拽条可以改宽度（360–560）。
//
// 顺序上先给「这个节点要什么」，再给「它产出什么」，最后才是重试这类少改的项。

import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react';
import {
  Check,
  ClipboardCopy,
  Copy,
  Loader2,
  MoreHorizontal,
  Play,
  Plus,
  RefreshCw,
  Settings2,
  Trash2,
  X,
} from 'lucide-react';
import {
  getRun,
  listRuns,
  parseJsonText,
  type CatalogKind,
  type NodeProbeResult,
  type NodeRunStatus,
  type VariableType,
  type WorkflowCatalog,
  type WorkflowDefinition,
  type WorkflowNode,
  type WorkflowStatus,
  type WorkflowVariable,
} from '@/api/workflows';
import { Checkbox, Input, Select, Textarea } from '@/components/ui';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/DropdownMenu';
import { ApiError } from '@/api/client';
import { cn } from '@/lib/cn';
import { fullDateTime } from '@/lib/format';
import { availableVariables, iconOf, kindTitle, nodeIssue, toneOf, VAR_PATTERN } from '@/lib/workflowMeta';
import { useI18n } from '@/i18n/locale';

export type WorkflowMeta = { name: string; description: string; status: WorkflowStatus };

export type PanelTabKey = 'settings' | 'lastRun';

type Props = {
  workflowId: number;
  catalog: WorkflowCatalog | null;
  kinds: CatalogKind[];
  definition: WorkflowDefinition;
  node: WorkflowNode | null;
  meta: WorkflowMeta;
  onNodeChange(patch: Partial<WorkflowNode>): void;
  onDeleteNode(): void;
  onDuplicateNode(): void;
  /** 试跑当前节点：从开始节点真实执行到这一步。 */
  onRunNode(): void;
  running: boolean;
  /** 最近一次试跑结果，只对当前节点生效；关掉面板即丢弃。 */
  probe: NodeProbeResult | null;
  /** 受控页签：试跑完成后自动切到「上次运行」。 */
  tab: PanelTabKey;
  onTabChange(tab: PanelTabKey): void;
  onMetaChange(patch: Partial<WorkflowMeta>): void;
  onClose(): void;
};

const MIN_WIDTH = 360;
const MAX_WIDTH = 560;

export function WorkflowInspector(props: Props) {
  const { tr } = useI18n();
  const [width, setWidth] = useState(396);
  const panelRef = useRef<HTMLElement | null>(null);
  const { onClose } = props;

  // 与 Dify 一致：Esc 收起面板。
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      const target = event.target as HTMLElement | null;
      // 输入框里的 Esc 只还原输入，不关面板。
      if (target !== null && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return;
      onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  // 左侧拖拽条：与 Dify 一样，往左拖变宽、往右拖变窄。
  const startResize = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const originX = event.clientX;
    const originWidth = panelRef.current?.getBoundingClientRect().width ?? 396;
    const onMove = (moveEvent: PointerEvent) => {
      const next = originWidth + (originX - moveEvent.clientX);
      setWidth(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(next))));
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }, []);

  return (
    <aside
      ref={panelRef}
      aria-label={props.node === null ? tr('工作流信息', 'Workflow info') : tr('节点面板', 'Node panel')}
      style={{ width }}
      className="relative z-20 my-2 ml-2 mr-2 flex shrink-0 flex-col overflow-hidden rounded-2xl border border-border/70 bg-card shadow-[0_20px_60px_-24px_rgba(0,0,0,0.55)]"
    >
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label={tr('拖动调整面板宽度', 'Drag to resize the panel')}
        onPointerDown={startResize}
        className="group absolute inset-y-0 left-0 z-10 w-1.5 cursor-col-resize"
      >
        <span className="absolute left-0 top-0 h-full w-px bg-transparent transition-colors group-hover:bg-indigo-500/60" aria-hidden />
      </div>

      {props.node === null ? <WorkflowPanel {...props} /> : <NodePanel {...props} node={props.node} />}
    </aside>
  );
}

// ---------------------------------------------------------------------------
// 节点面板
// ---------------------------------------------------------------------------

function NodePanel({
  node,
  kinds,
  catalog,
  definition,
  onNodeChange,
  onDeleteNode,
  onDuplicateNode,
  onRunNode,
  running,
  probe,
  tab,
  onTabChange,
  onClose,
  workflowId,
}: Props & { node: WorkflowNode }) {
  const { tr, locale } = useI18n();
  const isZh = locale === 'zh-CN';
  const tone = toneOf(node.kind);
  const Icon = iconOf(node.kind);
  const title = kindTitle(node.kind, kinds, isZh);
  const issue = nodeIssue(node, isZh);
  // 人工节点在完整运行里要等提交，试跑会直接卡住，所以不给入口。
  const canRun = node.kind !== 'start' && node.kind !== 'approval' && node.kind !== 'input';
  const fresh = probe !== null && probe.node_id === node.id ? probe : null;

  return (
    <>
      <div className="shrink-0 border-b border-border/60">
        <div className="flex items-center gap-2 px-3.5 pb-1 pt-3">
          <span className={cn('flex h-6 w-6 shrink-0 items-center justify-center rounded-lg shadow-sm', tone.iconBox)}>
            <Icon size={13} strokeWidth={2.2} className={tone.icon} aria-hidden />
          </span>
          <InlineText
            value={node.label}
            placeholder={title}
            ariaLabel={tr('节点名称', 'Node name')}
            className="text-[13px] font-semibold"
            onCommit={(next) => onNodeChange({ label: next })}
          />
          {canRun && (
            <PanelIconButton
              label={running ? tr('试跑中…', 'Running…') : tr('运行此步', 'Run this step')}
              onClick={onRunNode}
              active={running}
            >
              {running ? <Loader2 size={14} className="animate-spin" aria-hidden /> : <Play size={14} aria-hidden />}
            </PanelIconButton>
          )}
          <NodeMenu
            node={node}
            canDelete={node.kind !== 'start'}
            onDuplicate={onDuplicateNode}
            onDelete={onDeleteNode}
            onClose={onClose}
          />
          <PanelIconButton label={tr('关闭面板', 'Close panel')} onClick={onClose}>
            <X size={14} aria-hidden />
          </PanelIconButton>
        </div>

        <div className="flex items-center gap-3 px-3.5 pb-1">
          <PanelTab active={tab === 'settings'} onClick={() => onTabChange('settings')}>
            {tr('设置', 'Settings')}
          </PanelTab>
          <PanelTab active={tab === 'lastRun'} onClick={() => onTabChange('lastRun')}>
            {tr('上次运行', 'Last run')}
          </PanelTab>
          {fresh !== null && (
            <span
              className={cn(
                'rounded-full px-1.5 text-[9.5px] leading-4',
                fresh.status === 'succeeded' ? 'bg-ok/10 text-ok' : 'bg-danger/10 text-danger',
              )}
            >
              {tr('已试跑', 'Tested')}
            </span>
          )}
          {issue !== null && (
            <span className="ml-auto truncate rounded border border-warn/30 bg-warn/10 px-1.5 text-[9.5px] leading-4 text-warn" title={issue}>
              {issue}
            </span>
          )}
        </div>
      </div>

      {tab === 'settings' ? (
        <div className="min-h-0 flex-1 overflow-y-auto px-3.5 py-3">
          <div className="space-y-3">
            <KindFields node={node} catalog={catalog} onNodeChange={onNodeChange} />
            <ResultVarRow node={node} definition={definition} onNodeChange={onNodeChange} />
          </div>
        </div>
      ) : (
        <LastRunBody workflowId={workflowId} nodeId={node.id} probe={fresh} />
      )}
    </>
  );
}

/**
 * 结果变量：按节点 ID 自动生成，不让用户自己起名。
 * 这里只做只读展示 + 复制，下游填 {{变量}} 时照抄即可。
 */
function ResultVarRow({
  node,
  definition,
  onNodeChange,
}: {
  node: WorkflowNode;
  definition: WorkflowDefinition;
  onNodeChange(patch: Partial<WorkflowNode>): void;
}) {
  const { tr } = useI18n();
  const [copied, setCopied] = useState(false);
  if (node.kind === 'start') return null;

  if (node.kind === 'end') {
    // 结束节点决定整次运行的输出：从已有变量里选，而不是让用户手写变量名。
    const candidates = availableVariables(definition, node.id);
    return (
      <Field
        label={tr('运行输出', 'Run output')}
        hint={tr('整次运行的输出取这个变量；不选则输出整份状态', 'The run output reads this variable; empty means the whole state')}
      >
        <Select
          value={node.output_key}
          onValueChange={(next) => onNodeChange({ output_key: next })}
          aria-label={tr('运行输出', 'Run output')}
          className="h-8 text-[11.5px]"
          options={[
            { value: '', label: tr('整份状态', 'Whole state') },
            ...candidates.map((name) => ({ value: name, label: `{{${name}}}` })),
          ]}
        />
      </Field>
    );
  }

  // 分支 / 循环 / 人工节点不写回状态，没有结果变量可说。
  if (node.output_key === '') return null;

  const token = `{{${node.output_key}}}`;
  return (
    <div className="rounded-lg border border-border/60 bg-bg/40 px-2.5 py-2">
      <div className="flex items-center gap-2">
        <span className="shrink-0 text-[10px] font-medium uppercase tracking-wider text-text-faint">{tr('结果变量', 'Result variable')}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-text">{token}</span>
        <button
          type="button"
          aria-label={tr('复制结果变量', 'Copy result variable')}
          title={tr('复制结果变量', 'Copy result variable')}
          onClick={() => {
            void navigator.clipboard?.writeText(token);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1200);
          }}
          className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-text-faint transition-colors hover:bg-zinc-500/10 hover:text-text"
        >
          {copied ? <Check size={11} aria-hidden /> : <Copy size={11} aria-hidden />}
        </button>
      </div>
      <div className="mt-0.5 text-[10px] leading-4 text-text-faint">
        {tr('自动按节点生成，下游直接用它取值', 'Generated from the node; downstream reads it as-is')}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 各类型节点的参数
// ---------------------------------------------------------------------------

function KindFields({ node, catalog, onNodeChange }: { node: WorkflowNode; catalog: WorkflowCatalog | null; onNodeChange(patch: Partial<WorkflowNode>): void }) {
  const { tr } = useI18n();
  switch (node.kind) {
    case 'start':
      return <StartNodeFields node={node} onNodeChange={onNodeChange} />;
    case 'agent':
      return <AgentFields node={node} catalog={catalog} onNodeChange={onNodeChange} />;
    case 'knowledge':
      return <KnowledgeFields node={node} onNodeChange={onNodeChange} />;
    case 'search':
      return <SearchFields node={node} onNodeChange={onNodeChange} />;
    case 'mcp':
      return <McpFields node={node} catalog={catalog} onNodeChange={onNodeChange} />;
    case 'http':
      return <HttpFields node={node} onNodeChange={onNodeChange} />;
    case 'code':
      return <CodeFields node={node} onNodeChange={onNodeChange} />;
    case 'assign':
      return <AssignFields node={node} onNodeChange={onNodeChange} />;
    case 'switch':
      return <SwitchFields node={node} onNodeChange={onNodeChange} />;
    case 'loop':
      return <LoopFields node={node} onNodeChange={onNodeChange} />;
    case 'approval':
    case 'input':
      return <PromptFields node={node} onNodeChange={onNodeChange} />;
    case 'join':
      return (
        <InfoBox tone="muted">
          {tr('汇合节点聚合所有前驱写回的状态；入边不能带分支名。', 'Join merges upstream state; incoming edges must not carry a route.')}
        </InfoBox>
      );
    case 'end':
      return (
        <InfoBox tone="muted">
          {tr(
            '结束节点决定运行输出：选了变量就只输出该变量，否则输出整份状态。',
            'The end node defines run output: the chosen variable, or the whole state.',
          )}
        </InfoBox>
      );
    default:
      return null;
  }
}

function StartNodeFields({ node, onNodeChange }: { node: WorkflowNode; onNodeChange(patch: Partial<WorkflowNode>): void }) {
  const { tr } = useI18n();
  return (
    <>
      <VariablesEditor
        variables={node.variables}
        onChange={(variables) => onNodeChange({ variables })}
      />
      <InfoBox tone="muted">
        {tr(
          '这些变量就是整次运行的输入；下游任意节点都可以用 {{变量名}} 引用。',
          'These variables are the run inputs; any downstream node can read them as {{var}}.',
        )}
      </InfoBox>
    </>
  );
}

function AgentFields({ node, catalog, onNodeChange }: { node: WorkflowNode; catalog: WorkflowCatalog | null; onNodeChange(patch: Partial<WorkflowNode>): void }) {
  const { tr } = useI18n();
  const models = catalog?.models ?? [];
  const modelValue = node.provider !== '' && node.model !== '' ? `${node.provider}::${node.model}` : '';

  return (
    <>
      <Field
        label={tr('模型', 'Model')}
        required
        hint={models.length === 0 ? tr('还没有启用任何模型，请先到「设置 → 模型」配置', 'No model is enabled; configure one under Settings → Models') : undefined}
      >
        <Select
          value={modelValue}
          onValueChange={(value) => {
            const index = value.indexOf('::');
            onNodeChange({ provider: value.slice(0, index), model: value.slice(index + 2) });
          }}
          options={models.map((model) => ({ value: `${model.provider}::${model.model}`, label: model.title }))}
          aria-label={tr('模型', 'Model')}
          className="h-8 text-[11.5px]"
        />
      </Field>
      <Field label={tr('指令', 'Instruction')} required hint={tr('节点级系统指令，支持 {{变量}}', 'Node-level instruction; supports {{vars}}')}>
        <Textarea value={node.instruction} onChange={(event) => onNodeChange({ instruction: event.target.value })} rows={5} className="text-[11.5px]" />
      </Field>
      <InfoBox tone="muted">
        {tr(
          '这个节点只做一次模型调用，不挂工具。需要检索或调用外部工具，就在它后面接知识检索 / 联网搜索 / MCP 节点。',
          'This node is a single model call with no tools. Add a knowledge / search / MCP node after it when you need retrieval or tool calls.',
        )}
      </InfoBox>
    </>
  );
}

function KnowledgeFields({ node, onNodeChange }: { node: WorkflowNode; onNodeChange(patch: Partial<WorkflowNode>): void }) {
  const { tr } = useI18n();
  return (
    <>
      <Field label={tr('检索语句', 'Query')} required hint={tr('支持 {{变量}}，例如：{{topic}} 的处置流程', 'Supports {{vars}}, e.g. {{topic}} runbook')}>
        <Textarea value={node.query} onChange={(event) => onNodeChange({ query: event.target.value })} rows={2} className="text-[11.5px]" />
      </Field>
      <Field label={tr('返回条数', 'Top K')}>
        <Input
          type="number"
          min={1}
          max={50}
          value={node.limit}
          onChange={(event) => {
            const next = Number.parseInt(event.target.value, 10);
            onNodeChange({ limit: Number.isNaN(next) ? 5 : Math.min(50, Math.max(1, next)) });
          }}
          className="h-8 text-[11.5px]"
        />
      </Field>
      <Field label={tr('限定目录', 'Restrict to paths')} hint={tr('一行一个目录，留空表示检索全部知识库', 'One path per line; empty searches the whole knowledge base')}>
        <Textarea
          value={node.paths.join('\n')}
          onChange={(event) => onNodeChange({ paths: event.target.value.split('\n').map((line) => line.trim()).filter((line) => line !== '') })}
          rows={2}
          className="font-mono text-[11px]"
          placeholder="/产品文档"
        />
      </Field>
    </>
  );
}

function SearchFields({ node, onNodeChange }: { node: WorkflowNode; onNodeChange(patch: Partial<WorkflowNode>): void }) {
  const { tr } = useI18n();
  return (
    <>
      <Field label={tr('搜索语句', 'Query')} required hint={tr('支持 {{变量}}', 'Supports {{vars}}')}>
        <Textarea value={node.query} onChange={(event) => onNodeChange({ query: event.target.value })} rows={2} className="text-[11.5px]" />
      </Field>
      <Field label={tr('返回条数', 'Result count')}>
        <Input
          type="number"
          min={1}
          max={20}
          value={node.limit}
          onChange={(event) => {
            const next = Number.parseInt(event.target.value, 10);
            onNodeChange({ limit: Number.isNaN(next) ? 5 : Math.min(20, Math.max(1, next)) });
          }}
          className="h-8 text-[11.5px]"
        />
      </Field>
    </>
  );
}

function McpFields({ node, catalog, onNodeChange }: { node: WorkflowNode; catalog: WorkflowCatalog | null; onNodeChange(patch: Partial<WorkflowNode>): void }) {
  const { tr } = useI18n();
  const tools = useMemo(() => catalog?.mcp_tools ?? [], [catalog]);
  const servers = useMemo(() => Array.from(new Set(tools.map((tool) => tool.server))), [tools]);
  const serverTools = tools.filter((tool) => tool.server === node.mcp_server);
  return (
    <>
      <Field label={tr('MCP Server', 'MCP server')} required>
        <Select
          value={node.mcp_server}
          onValueChange={(value) => {
            const nextTool = tools.find((tool) => tool.server === value)?.tool ?? '';
            onNodeChange({ mcp_server: value, mcp_tool: nextTool });
          }}
          options={servers.map((server) => ({ value: server, label: server }))}
          aria-label={tr('MCP Server', 'MCP server')}
          className="h-8 text-[11.5px]"
        />
      </Field>
      <Field label={tr('工具', 'Tool')} required>
        <Select
          value={node.mcp_tool}
          onValueChange={(value) => onNodeChange({ mcp_tool: value })}
          options={serverTools.map((tool) => ({ value: tool.tool, label: tool.tool }))}
          aria-label={tr('工具', 'Tool')}
          className="h-8 font-mono text-[11.5px]"
          disabled={node.mcp_server === ''}
        />
      </Field>
      <Field label={tr('参数', 'Arguments')} hint={tr('值支持 {{变量}}；参数名按工具 schema 填写', 'Values support {{vars}}; key names follow the tool schema')}>
        <KeyValueEditor
          value={node.arguments}
          onChange={(args) => onNodeChange({ arguments: args })}
          keyPlaceholder="arg"
          valuePlaceholder="{{input}}"
        />
      </Field>
    </>
  );
}

function HttpFields({ node, onNodeChange }: { node: WorkflowNode; onNodeChange(patch: Partial<WorkflowNode>): void }) {
  const { tr } = useI18n();
  return (
    <>
      <div className="grid grid-cols-[92px_minmax(0,1fr)] gap-2">
        <Field label={tr('方法', 'Method')}>
          <Select
            value={node.method}
            onValueChange={(method) => onNodeChange({ method })}
            options={['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD'].map((method) => ({ value: method, label: method }))}
            aria-label={tr('方法', 'Method')}
            className="h-8 text-[11.5px]"
          />
        </Field>
        <Field label={tr('URL', 'URL')} required hint={tr('支持 {{变量}}', 'Supports {{vars}}')}>
          <Input value={node.url} onChange={(event) => onNodeChange({ url: event.target.value })} placeholder="https://example.com/api" className="h-8 text-[11.5px]" />
        </Field>
      </div>
      <Field label={tr('请求头', 'Headers')}>
        <KeyValueEditor value={node.headers} onChange={(headers) => onNodeChange({ headers })} keyPlaceholder="Content-Type" valuePlaceholder="application/json" />
      </Field>
      <Field label={tr('请求体', 'Body')} hint={tr('支持 {{变量}}；留空表示不带 body', 'Supports {{vars}}; empty means no body')}>
        <Textarea value={node.body} onChange={(event) => onNodeChange({ body: event.target.value })} rows={4} className="font-mono text-[11px]" placeholder='{"q": "{{input}}"}' />
      </Field>
    </>
  );
}

function CodeFields({ node, onNodeChange }: { node: WorkflowNode; onNodeChange(patch: Partial<WorkflowNode>): void }) {
  const { tr } = useI18n();
  return (
    <>
      <Field label={tr('代码', 'Code')} required hint={tr('定义 run(state) -> Any，返回值会写入结果变量', 'Define run(state) -> Any; the return value becomes the result variable')}>
        <Textarea
          value={node.code}
          onChange={(event) => onNodeChange({ code: event.target.value })}
          rows={10}
          spellCheck={false}
          className="font-mono text-[11px] leading-5"
        />
      </Field>
    </>
  );
}

function AssignFields({ node, onNodeChange }: { node: WorkflowNode; onNodeChange(patch: Partial<WorkflowNode>): void }) {
  const { tr } = useI18n();
  return (
    <Field label={tr('赋值', 'Assignments')} required hint={tr('变量名 → 值，值支持 {{变量}} 插值', 'Name → value; values support {{vars}}')}>
      <KeyValueEditor value={node.assignments} onChange={(assignments) => onNodeChange({ assignments })} keyPlaceholder="summary" valuePlaceholder="关于 {{topic}} 的结论" />
    </Field>
  );
}

function SwitchFields({ node, onNodeChange }: { node: WorkflowNode; onNodeChange(patch: Partial<WorkflowNode>): void }) {
  const { tr } = useI18n();
  return (
    <>
      <Field label={tr('判断方式', 'Decision')}>
        <Select
          value={node.branch_mode}
          onValueChange={(value) => onNodeChange({ branch_mode: value === 'model' ? 'model' : 'expr' })}
          options={[
            { value: 'expr', label: tr('按表达式判断', 'Expression') },
            { value: 'model', label: tr('交给模型判断', 'Model decides') },
          ]}
          aria-label={tr('判断方式', 'Decision')}
          className="h-8 text-[11.5px]"
        />
      </Field>
      {node.branch_mode === 'expr' ? (
        <Field label={tr('表达式', 'Expression')} required hint={tr('形如 {{amount}} > 1000，结果决定走哪条出边', 'e.g. {{amount}} > 1000 picks the outgoing route')}>
          <Input
            value={node.expression}
            onChange={(event) => onNodeChange({ expression: event.target.value })}
            placeholder="{{status}} == ok"
            className="h-8 font-mono text-[11.5px]"
          />
        </Field>
      ) : (
        <InfoBox tone="muted">
          {tr('由默认模型从下面这些分支里选一个，适合规则说不清的判断。', 'The default model picks one of the branches below.')}
        </InfoBox>
      )}
      <Field label={tr('分支', 'Branches')} required hint={tr('分支名会出现在连线上，出边必须落到已有分支', 'Branch names label the edges; every outgoing edge must use one')}>
        <StringListEditor
          items={node.branches}
          onChange={(branches) => onNodeChange({ branches })}
          placeholder="yes"
          addLabel={tr('添加分支', 'Add branch')}
        />
      </Field>
    </>
  );
}

function LoopFields({ node, onNodeChange }: { node: WorkflowNode; onNodeChange(patch: Partial<WorkflowNode>): void }) {
  const { tr } = useI18n();
  return (
    <>
      <Field label={tr('继续条件', 'Continue while')} hint={tr('条件成立时走 loop 出边回到上游；不成立走 exit', 'True loops back through the loop edge; false takes exit')}>
        <Input
          value={node.expression}
          onChange={(event) => onNodeChange({ expression: event.target.value })}
          placeholder="{{count}} < 3"
          className="h-8 font-mono text-[11.5px]"
        />
      </Field>
      <Field label={tr('最多轮次', 'Max rounds')}>
        <Input
          type="number"
          min={1}
          max={100}
          value={node.max_iterations}
          onChange={(event) => {
            const next = Number.parseInt(event.target.value, 10);
            onNodeChange({ max_iterations: Number.isNaN(next) ? 5 : Math.min(100, Math.max(1, next)) });
          }}
          className="h-8 text-[11.5px]"
        />
      </Field>
    </>
  );
}

function PromptFields({ node, onNodeChange }: { node: WorkflowNode; onNodeChange(patch: Partial<WorkflowNode>): void }) {
  const { tr } = useI18n();
  return (
    <Field
      label={node.kind === 'approval' ? tr('审批说明', 'Approval prompt') : tr('提示文案', 'Prompt')}
      required
      hint={tr('支持 {{变量}}，运行会停在这里等人处理', 'Supports {{vars}}; the run pauses here for a human')}
    >
      <Textarea value={node.prompt} onChange={(event) => onNodeChange({ prompt: event.target.value })} rows={3} className="text-[11.5px]" />
    </Field>
  );
}

// ---------------------------------------------------------------------------
// 通用编辑控件
// ---------------------------------------------------------------------------

function Field({ label, hint, required, children }: { label: string; hint?: ReactNode; required?: boolean; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <div className="flex items-baseline gap-1">
        <span className="text-[11px] font-medium text-text-muted">{label}</span>
        {required === true && <span className="text-[10px] text-danger" aria-hidden>*</span>}
      </div>
      {children}
      {hint !== undefined && <div className="text-[10px] leading-4 text-text-faint">{hint}</div>}
    </div>
  );
}

function InfoBox({ tone, children }: { tone: 'muted' | 'warn'; children: ReactNode }) {
  return (
    <div
      className={cn(
        'rounded-lg border px-2.5 py-2 text-[10.5px] leading-4',
        tone === 'warn' ? 'border-warn/30 bg-warn/5 text-warn' : 'border-border/60 bg-bg/30 text-text-faint',
      )}
    >
      {children}
    </div>
  );
}

function InlineText({
  value,
  placeholder,
  ariaLabel,
  className,
  onCommit,
}: {
  value: string;
  placeholder: string;
  ariaLabel: string;
  className?: string;
  onCommit(next: string): void;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const commit = () => {
    const next = draft.trim();
    if (next === value) return;
    // 空标题没有意义（画布上会变成一片没有名字的卡片），还原成上一个有效值。
    if (next === '') {
      setDraft(value);
      return;
    }
    onCommit(next);
  };
  return (
    <input
      value={draft}
      aria-label={ariaLabel}
      placeholder={placeholder}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === 'Enter') {
          event.currentTarget.blur();
        }
        if (event.key === 'Escape') setDraft(value);
      }}
      className={cn(
        'h-7 min-w-0 flex-1 rounded-md border border-transparent bg-transparent px-1 text-text outline-none transition-colors placeholder:text-text-faint hover:border-border/60 focus:border-indigo-500 focus:bg-bg/60',
        className,
      )}
    />
  );
}

function PanelTab({ active, onClick, children }: { active: boolean; onClick(): void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        'relative h-8 text-[11.5px] transition-colors',
        active ? 'font-medium text-text' : 'text-text-faint hover:text-text-muted',
      )}
    >
      {children}
      {active && <span className="absolute inset-x-0 bottom-0 h-0.5 rounded-full bg-indigo-500" aria-hidden />}
    </button>
  );
}

function PanelIconButton({
  label,
  onClick,
  children,
  active,
}: {
  label: string;
  onClick(): void;
  children: ReactNode;
  active?: boolean;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className={cn(
        'flex h-7 w-7 shrink-0 items-center justify-center rounded-md transition-colors',
        active === true ? 'text-indigo-400' : 'text-text-faint hover:bg-zinc-500/10 hover:text-text',
      )}
    >
      {children}
    </button>
  );
}

function NodeMenu({
  node,
  canDelete,
  onDuplicate,
  onDelete,
  onClose,
}: {
  node: WorkflowNode;
  canDelete: boolean;
  onDuplicate(): void;
  onDelete(): void;
  onClose(): void;
}) {
  const { tr } = useI18n();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={tr('更多操作', 'More actions')}
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-text-faint transition-colors hover:bg-zinc-500/10 hover:text-text"
      >
        <MoreHorizontal size={14} aria-hidden />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-44">
        <DropdownMenuItem className="gap-2 text-[11.5px]" onClick={() => void navigator.clipboard?.writeText(node.id)}>
          <ClipboardCopy size={12} aria-hidden />
          {tr('复制节点 ID', 'Copy node ID')}
        </DropdownMenuItem>
        <DropdownMenuItem className="gap-2 text-[11.5px]" onClick={onDuplicate}>
          <Copy size={12} aria-hidden />
          {tr('复制节点', 'Duplicate node')}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        {canDelete && (
          <DropdownMenuItem className="gap-2 text-[11.5px] text-danger" onClick={onDelete}>
            <Trash2 size={12} aria-hidden />
            {tr('删除节点', 'Delete node')}
          </DropdownMenuItem>
        )}
        <DropdownMenuItem className="gap-2 text-[11.5px]" onClick={onClose}>
          <X size={12} aria-hidden />
          {tr('关闭面板', 'Close panel')}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** 键值对编辑器：MCP 参数、HTTP 请求头、变量赋值共用。 */
function KeyValueEditor({
  value,
  onChange,
  keyPlaceholder,
  valuePlaceholder,
}: {
  value: Record<string, string>;
  onChange(next: Record<string, string>): void;
  keyPlaceholder: string;
  valuePlaceholder: string;
}) {
  const { tr } = useI18n();
  const [rows, setRows] = useState<{ key: string; value: string }[]>(() => toRows(value));
  // 自己发出的变更会带着新对象回来，此时不能重置行，否则正在输入的空键会被抹掉。
  const lastEmitted = useRef<Record<string, string> | null>(null);
  useEffect(() => {
    if (lastEmitted.current === value) return;
    setRows(toRows(value));
  }, [value]);

  const emit = (next: { key: string; value: string }[]) => {
    const built: Record<string, string> = {};
    for (const row of next) {
      const key = row.key.trim();
      if (key === '') continue;
      built[key] = row.value;
    }
    lastEmitted.current = built;
    setRows(next);
    onChange(built);
  };

  return (
    <div className="space-y-1.5">
      {rows.length === 0 && <div className="text-[10.5px] text-text-faint">{tr('还没有参数', 'No entries yet')}</div>}
      {rows.map((row, index) => (
        <div key={index} className="flex items-center gap-1.5">
          <Input
            value={row.key}
            aria-label={tr('参数名', 'Key')}
            placeholder={keyPlaceholder}
            onChange={(event) => emit(rows.map((item, i) => (i === index ? { ...item, key: event.target.value } : item)))}
            className="h-7 min-w-0 flex-1 font-mono text-[11px]"
          />
          <Input
            value={row.value}
            aria-label={tr('参数值', 'Value')}
            placeholder={valuePlaceholder}
            onChange={(event) => emit(rows.map((item, i) => (i === index ? { ...item, value: event.target.value } : item)))}
            className="h-7 min-w-0 flex-[1.4] font-mono text-[11px]"
          />
          <button
            type="button"
            aria-label={tr('删除该行', 'Remove row')}
            onClick={() => emit(rows.filter((_, i) => i !== index))}
            className="flex h-7 w-6 shrink-0 items-center justify-center rounded text-text-faint transition-colors hover:bg-danger/10 hover:text-danger"
          >
            <X size={11} aria-hidden />
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => setRows([...rows, { key: '', value: '' }])}
        className="inline-flex items-center gap-1 rounded-md px-1 py-0.5 text-[10.5px] text-text-faint transition-colors hover:text-text"
      >
        <Plus size={10} aria-hidden />
        {tr('添加一行', 'Add row')}
      </button>
    </div>
  );
}

/** 字符串列表编辑：条件分支名这类「只能填标识符」的有序列表。 */
function StringListEditor({
  items,
  onChange,
  placeholder,
  addLabel,
}: {
  items: string[];
  onChange(next: string[]): void;
  placeholder: string;
  addLabel: string;
}) {
  const { tr } = useI18n();
  // 输入中间态需要留在本地，否则每敲一个字符都会被合法性校验挡住。
  const [drafts, setDrafts] = useState<string[]>(items);
  const lastEmitted = useRef<string[] | null>(null);
  useEffect(() => {
    if (lastEmitted.current === items) return;
    setDrafts(items);
  }, [items]);

  const emit = (next: string[]) => {
    lastEmitted.current = next;
    setDrafts(next);
    onChange(next);
  };

  return (
    <div className="space-y-1.5">
      {drafts.map((draft, index) => (
        <div key={index} className="flex items-center gap-1.5">
          <Input
            value={draft}
            aria-label={tr('分支名', 'Branch name')}
            placeholder={placeholder}
            onChange={(event) => emit(drafts.map((item, i) => (i === index ? event.target.value : item)))}
            className={cn('h-7 min-w-0 flex-1 font-mono text-[11px]', draft !== '' && !VAR_PATTERN.test(draft) && 'border-danger/60')}
          />
          <button
            type="button"
            aria-label={tr('删除分支', 'Remove branch')}
            onClick={() => emit(drafts.filter((_, i) => i !== index))}
            className="flex h-7 w-6 shrink-0 items-center justify-center rounded text-text-faint transition-colors hover:bg-danger/10 hover:text-danger"
          >
            <X size={11} aria-hidden />
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => setDrafts([...drafts, ''])}
        className="inline-flex items-center gap-1 rounded-md px-1 py-0.5 text-[10.5px] text-text-faint transition-colors hover:text-text"
      >
        <Plus size={10} aria-hidden />
        {addLabel}
      </button>
    </div>
  );
}

/** 开始节点的输入变量表：变量名 / 类型 / 是否必填 / 说明 / 默认值。 */
function VariablesEditor({ variables, onChange }: { variables: WorkflowVariable[]; onChange(next: WorkflowVariable[]): void }) {
  const { tr } = useI18n();
  const update = (index: number, patch: Partial<WorkflowVariable>) => {
    onChange(variables.map((variable, i) => (i === index ? { ...variable, ...patch } : variable)));
  };
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between">
        <span className="text-[11px] font-medium text-text-muted">{tr('输入变量', 'Input variables')}</span>
        <span className="text-[10px] text-text-faint">{tr(`共 ${variables.length} 个`, `${variables.length} total`)}</span>
      </div>

      {variables.length === 0 && (
        <div className="rounded-lg border border-dashed border-border/70 px-2.5 py-3 text-center text-[10.5px] text-text-faint">
          {tr('还没有输入变量，运行时不需要任何参数', 'No inputs yet; the run needs no parameters')}
        </div>
      )}

      {variables.map((variable, index) => {
        const invalidName = variable.name !== '' && !VAR_PATTERN.test(variable.name);
        return (
          <div key={index} className="space-y-1.5 rounded-lg border border-border/60 bg-bg/30 p-2">
            <div className="flex items-center gap-1.5">
              <Input
                value={variable.name}
                aria-label={tr('变量名', 'Variable name')}
                placeholder="topic"
                onChange={(event) => update(index, { name: event.target.value })}
                className={cn('h-7 min-w-0 flex-1 font-mono text-[11px]', invalidName && 'border-danger/60')}
              />
              <Select
                value={variable.type}
                onValueChange={(type) => update(index, { type: type as VariableType })}
                options={[
                  { value: 'string', label: tr('文本', 'String') },
                  { value: 'number', label: tr('数字', 'Number') },
                  { value: 'boolean', label: tr('布尔', 'Boolean') },
                  { value: 'json', label: tr('JSON', 'JSON') },
                ]}
                aria-label={tr('变量类型', 'Variable type')}
                className="h-7 w-[72px] text-[10.5px]"
              />
              <button
                type="button"
                aria-label={tr('删除变量', 'Remove variable')}
                onClick={() => onChange(variables.filter((_, i) => i !== index))}
                className="flex h-7 w-6 shrink-0 items-center justify-center rounded text-text-faint transition-colors hover:bg-danger/10 hover:text-danger"
              >
                <X size={11} aria-hidden />
              </button>
            </div>
            <Input
              value={variable.description}
              aria-label={tr('变量说明', 'Variable description')}
              placeholder={tr('这个输入是干什么用的', 'What this input is for')}
              onChange={(event) => update(index, { description: event.target.value })}
              className="h-7 text-[11px]"
            />
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-1.5 text-[10.5px] text-text-faint">
                <Checkbox
                  checked={variable.required}
                  // 必填与默认值互斥（后端会拒绝），打开必填时顺手清掉默认值。
                  onCheckedChange={(checked) => update(index, checked === true ? { required: true, default: null } : { required: false })}
                  aria-label={tr('必填', 'Required')}
                />
                {tr('必填', 'Required')}
              </label>
              <div className="ml-auto flex items-center gap-1.5">
                <span className="text-[10px] text-text-faint">{tr('默认值', 'Default')}</span>
                <Input
                  value={defaultText(variable.default)}
                  aria-label={tr('默认值', 'Default value')}
                  // 必填变量与默认值互斥（后端会拒绝），必填时不展示可编辑的默认值。
                  disabled={variable.required}
                  onChange={(event) => update(index, { default: event.target.value === '' ? null : event.target.value })}
                  placeholder="—"
                  className="h-7 w-28 font-mono text-[11px]"
                />
              </div>
            </div>
          </div>
        );
      })}

      <button
        type="button"
        onClick={() => onChange([...variables, { name: '', type: 'string', description: '', required: true, default: null }])}
        className="inline-flex items-center gap-1 rounded-md px-1 py-0.5 text-[10.5px] text-text-faint transition-colors hover:text-text"
      >
        <Plus size={10} aria-hidden />
        {tr('添加变量', 'Add variable')}
      </button>
    </div>
  );
}

function toRows(value: Record<string, string>): { key: string; value: string }[] {
  return Object.entries(value).map(([key, item]) => ({ key, value: item }));
}

/** 变量默认值在输入框里的样子：字符串原样，其它类型按 JSON 展示。 */
function defaultText(value: unknown): string {
  if (value === null || value === undefined) return '';
  return typeof value === 'string' ? value : JSON.stringify(value);
}

// ---------------------------------------------------------------------------
// 上次运行
// ---------------------------------------------------------------------------

type NodeRunView = {
  status: NodeRunStatus;
  output: unknown;
  error: string;
  durationMs: number;
  createdAt: string;
  attempt: number;
} | null;

function LastRunBody({ workflowId, nodeId, probe }: { workflowId: number; nodeId: string; probe: NodeProbeResult | null }) {
  const { tr } = useI18n();
  const [state, setState] = useState<{ loading: boolean; error: string | null; run: NodeRunView }>({
    loading: true,
    error: null,
    run: null,
  });

  const load = useCallback(async () => {
    setState({ loading: true, error: null, run: null });
    try {
      // 试跑只跑上游，最近那次运行里未必有这个节点：往前翻几次，找到真正执行过
      // 这个节点的最近一条记录，否则点上游节点会误显示成「还没有运行记录」。
      const { items } = await listRuns({ workflowId, limit: 10 });
      for (const item of items) {
        const detail = await getRun(item.run_uid);
        const record = detail.nodes.filter((entry) => entry.node_id === nodeId).at(-1);
        if (record === undefined) continue;
        setState({
          loading: false,
          error: null,
          run: {
            status: record.status,
            output: parseJsonText(record.output),
            error: record.error,
            durationMs: record.duration_ms,
            createdAt: record.created_at,
            attempt: record.attempt,
          },
        });
        return;
      }
      setState({ loading: false, error: null, run: null });
    } catch (error) {
      setState({ loading: false, error: error instanceof ApiError ? error.message : (error as Error).message, run: null });
    }
  }, [nodeId, workflowId]);

  useEffect(() => {
    // 已经有本次试跑结果时不再去翻历史，避免面板里同时出现新旧两份输出。
    if (probe !== null) return;
    void load();
  }, [load, probe]);

  // 试跑结果优先：它是刚发生的，比库里的历史更贴近用户刚才的动作。
  const view: NodeRunView = probe === null
    ? state.run
    : {
        status: probe.status,
        output: parseJsonText(probe.output),
        error: probe.error,
        durationMs: probe.duration_ms,
        createdAt: '',
        attempt: probe.attempt,
      };
  const loading = probe === null && state.loading;
  const error = probe === null ? state.error : null;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-3.5 py-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[10px] font-medium uppercase tracking-wider text-text-faint">
          {probe === null ? tr('最近一次运行', 'Most recent run') : tr('本次试跑', 'This test run')}
        </span>
        <button
          type="button"
          onClick={() => void load()}
          disabled={probe !== null}
          aria-label={tr('刷新运行结果', 'Refresh run result')}
          className={cn(
            'flex h-6 w-6 items-center justify-center rounded-md text-text-faint transition-colors hover:bg-zinc-500/10 hover:text-text',
            probe !== null && 'invisible',
          )}
        >
          <RefreshCw size={11} className={cn(loading && 'animate-spin')} aria-hidden />
        </button>
      </div>

      {loading && <div className="py-8 text-center text-[11px] text-text-faint">{tr('读取运行结果…', 'Loading run result…')}</div>}

      {!loading && error !== null && (
        <div role="alert" className="rounded-lg border border-danger/40 bg-danger/5 px-2.5 py-2 text-[11px] text-danger">
          {error}
        </div>
      )}

      {!loading && error === null && view === null && (
        <div className="rounded-lg border border-dashed border-border/70 px-3 py-6 text-center text-[11px] leading-5 text-text-faint">
          {tr('这个节点还没有运行记录。点节点上方的 ▶ 试跑，或运行一次工作流。', 'No run yet. Use ▶ above the node to test it, or run the workflow.')}
        </div>
      )}

      {view !== null && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-1.5 text-[10.5px]">
            <span
              className={cn(
                'inline-flex items-center gap-1 rounded-full px-1.5 leading-4',
                view.status === 'succeeded' ? 'bg-ok/10 text-ok' : 'bg-danger/10 text-danger',
              )}
            >
              {view.status === 'succeeded' ? <Check size={9} aria-hidden /> : <X size={9} aria-hidden />}
              {view.status === 'succeeded' ? tr('成功', 'Succeeded') : tr('失败', 'Failed')}
            </span>
            <span className="tabular-nums text-text-faint">{view.durationMs} ms</span>
            {view.attempt > 1 && <span className="text-text-faint">{tr(`第 ${view.attempt} 次尝试`, `attempt ${view.attempt}`)}</span>}
            {view.createdAt !== '' && <span className="ml-auto tabular-nums text-text-faint">{fullDateTime(view.createdAt)}</span>}
          </div>

          {view.error !== '' && (
            <pre className="max-h-40 overflow-auto rounded-lg border border-danger/30 bg-danger/5 px-2.5 py-2 font-mono text-[10.5px] leading-4 text-danger">{view.error}</pre>
          )}

          <div className="text-[10px] font-medium uppercase tracking-wider text-text-faint">{tr('输出', 'Output')}</div>
          <pre className="max-h-[46vh] overflow-auto rounded-lg border border-border/60 bg-bg/60 px-2.5 py-2 font-mono text-[10.5px] leading-4 text-text">
            {view.output === null ? '—' : typeof view.output === 'string' ? view.output : JSON.stringify(view.output, null, 2)}
          </pre>
          {probe !== null && (
            <div className="text-[10px] leading-4 text-text-faint">
              {tr('试跑不写入运行记录，只用于确认这一步的输入输出。', 'A test run is not stored in run history; it only shows this step’s result.')}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 工作流信息面板（点标题打开：只放名称与描述，运行输入在开始节点上配置）
// ---------------------------------------------------------------------------

function WorkflowPanel({ meta, definition, onMetaChange, onClose }: Props) {
  const { tr } = useI18n();

  return (
    <>
      <div className="flex shrink-0 items-center gap-2 border-b border-border/60 px-3.5 pb-2.5 pt-3">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-indigo-500 shadow-sm">
          <Settings2 size={13} className="text-white" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-semibold text-text">{tr('工作流信息', 'Workflow info')}</div>
          <div className="truncate text-[10px] text-text-faint">
            {tr('运行输入在开始节点上配置', 'Run inputs are configured on the start node')}
          </div>
        </div>
        <PanelIconButton label={tr('关闭面板', 'Close panel')} onClick={onClose}>
          <X size={14} aria-hidden />
        </PanelIconButton>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3.5 py-3">
        <div className="space-y-3">
          <Field label={tr('名称', 'Name')} required>
            <Input
              value={meta.name}
              aria-label={tr('工作流名称', 'Workflow name')}
              onChange={(event) => onMetaChange({ name: event.target.value })}
              className="h-8 text-[12px]"
            />
          </Field>

          <Field label={tr('描述', 'Description')}>
            <Textarea
              value={meta.description}
              aria-label={tr('工作流描述', 'Workflow description')}
              onChange={(event) => onMetaChange({ description: event.target.value })}
              rows={2}
              className="text-[11.5px]"
              placeholder={tr('这个工作流解决什么问题', 'What this workflow solves')}
            />
          </Field>

          {/* 状态不在这里配：画布改动自动存草稿，点顶部「发布」才升版本。 */}
          <InfoBox tone="muted">
            {tr(
              '画布改动会自动保存为草稿；点右上角「发布」校验通过后才生成新版本。',
              'Canvas edits auto-save as a draft; a new version is created only when you hit Publish.',
            )}
          </InfoBox>

          <div className="border-t border-border/60 pt-2.5 text-[10px] tabular-nums text-text-faint">
            {tr(`画布上共 ${definition.nodes.length} 个节点、${definition.edges.length} 条连线`, `${definition.nodes.length} nodes, ${definition.edges.length} edges`)}
          </div>
        </div>
      </div>
    </>
  );
}
