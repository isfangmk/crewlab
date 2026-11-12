// 右栏：运行上下文。
//
// 画布缩略图 + 运行输入 + 运行输出，一屏看完这次运行用了什么、产出了什么。

import { useState, type ReactNode } from 'react';
import { ChevronDown, ChevronRight, Maximize2 } from 'lucide-react';
import type { NodeRunStatus, WaitingGate, WorkflowRunDetail } from '@/api/workflows';
import { Modal } from '@/components/Modal';
import { CodeBlock } from '@/components/ui';
import { RunInputRows } from '@/components/workflow/RunInputRows';
import { WorkflowGraphThumb } from '@/components/workflow/WorkflowGraphThumb';
import { parseJsonText } from '@/api/workflows';
import { cn } from '@/lib/cn';
import { useI18n } from '@/i18n/locale';

type Props = {
  run: WorkflowRunDetail;
  /** node_id → 执行结果，给画布节点染色。 */
  statuses: Record<string, NodeRunStatus | 'running'>;
  gate: WaitingGate | null;
  selectedId: string | null;
  onSelect(nodeId: string): void;
};

export function RunContextPanel({ run, statuses, gate, selectedId, onSelect }: Props) {
  const { tr } = useI18n();
  // 画布缩略图折叠：排查字段时把宽度让给中栏
  const [canvasOpen, setCanvasOpen] = useState(true);
  // 画布放大弹层：窄栏只放得下结构，看节点名和分支关系要整宽摊开
  const [canvasZoom, setCanvasZoom] = useState(false);

  const graph = {
    nodes: run.definition.nodes.map((node) => ({
      id: node.id,
      kind: node.kind,
      label: node.label,
      x: node.x,
      y: node.y,
    })),
    edges: run.definition.edges,
  };

  return (
    <>
      <aside className="flex min-h-0 min-w-0 flex-col gap-3 overflow-y-auto border-t border-zinc-800/60 p-3 xl:border-l xl:border-t-0">
        <ContextCard
          title={tr('画布', 'Canvas')}
          action={
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setCanvasZoom(true)}
                className="rounded px-1 text-text-faint transition-colors hover:bg-zinc-500/10 hover:text-text-muted"
                aria-label={tr('放大画布', 'Enlarge canvas')}
                title={tr('放大画布', 'Enlarge canvas')}
              >
                <Maximize2 size={11} aria-hidden />
              </button>
              <button
                type="button"
                onClick={() => setCanvasOpen((current) => !current)}
                className="rounded px-1 text-text-faint transition-colors hover:bg-zinc-500/10 hover:text-text-muted"
                aria-expanded={canvasOpen}
                aria-label={canvasOpen ? tr('收起画布', 'Hide canvas') : tr('展开画布', 'Show canvas')}
              >
                {canvasOpen ? <ChevronDown size={11} aria-hidden /> : <ChevronRight size={11} aria-hidden />}
              </button>
            </div>
          }
        >
          {canvasOpen ? (
            <>
              {/* 画布要看得清结构：高度给足，节点多时也不会挤成一条线 */}
              <div className="h-48 px-1 py-1">
                <WorkflowGraphThumb
                  graph={graph}
                  statuses={statuses}
                  gateId={gate === null ? null : gate.gate}
                  selectedId={selectedId}
                  onSelect={onSelect}
                />
              </div>
              <div className="border-t border-zinc-800/50 px-3 py-1.5">
                <CanvasLegend />
              </div>
            </>
          ) : (
            <div className="px-3 py-3 text-[10.5px] text-text-faint">{tr('画布已收起', 'Canvas hidden')}</div>
          )}
        </ContextCard>

        <ContextCard title={tr('运行输入', 'Run input')}>
          <div className="px-2.5 py-2">
            <RunInputRows input={parseJsonText(run.input)} />
          </div>
        </ContextCard>

        <ContextCard title={tr('运行输出', 'Run output')}>
          <div className="px-2.5 py-2">
            <OutputBody value={run.output} />
          </div>
        </ContextCard>
      </aside>

      {/* 画布放大：看节点名与分支关系；点节点直接定位到中栏详情 */}
      {/* 弹层只在打开时挂载：常驻挂一个隐藏的画布会白跑一套 React Flow */}
      {canvasZoom && (
        <Modal open onClose={() => setCanvasZoom(false)} title={tr('画布', 'Canvas')} size="xl">
          <div className="h-[60vh]">
            <WorkflowGraphThumb
              graph={graph}
              statuses={statuses}
              gateId={gate === null ? null : gate.gate}
              selectedId={selectedId}
              mode="overview"
              onSelect={(nodeId) => {
                onSelect(nodeId);
                setCanvasZoom(false);
              }}
            />
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-text-faint">
            <CanvasLegend />
            <span className="ml-auto">{tr('点节点定位到中栏详情', 'Click a node to inspect it')}</span>
          </div>
        </Modal>
      )}
    </>
  );
}

function CanvasLegend() {
  const { tr } = useI18n();
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[9.5px] text-text-faint">
      <LegendDot className="bg-ok" label={tr('成功', 'ok')} />
      <LegendDot className="bg-danger" label={tr('失败', 'failed')} />
      <LegendDot className="bg-info" label={tr('运行中', 'running')} />
      <LegendDot className="bg-warn" label={tr('等待人工', 'waiting')} />
      <LegendDot className="bg-zinc-600" label={tr('未执行', 'not run')} />
    </div>
  );
}

function ContextCard({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-lg border border-zinc-800/60 bg-zinc-900/30">
      <header className="flex items-center justify-between gap-2 border-b border-zinc-800/50 px-3 py-1.5">
        <h2 className="text-[11px] font-medium text-text-muted">{title}</h2>
        {action}
      </header>
      {children}
    </section>
  );
}

function LegendDot({ className, label }: { className: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className={cn('h-1.5 w-1.5 rounded-full', className)} aria-hidden />
      {label}
    </span>
  );
}

function OutputBody({ value }: { value: string }) {
  const { tr } = useI18n();
  const parsed = parseJsonText(value);
  if (parsed === null) return <div className="px-0.5 text-[11px] text-text-faint">{tr('空', 'Empty')}</div>;
  return (
    <CodeBlock maxHeightClassName="max-h-40">
      {typeof parsed === 'string' ? parsed : JSON.stringify(parsed, null, 2)}
    </CodeBlock>
  );
}
