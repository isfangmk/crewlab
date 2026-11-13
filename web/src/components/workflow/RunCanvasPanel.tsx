// 画布 tab：整宽摊开的运行画布。
//
// 用 overview 视口一次装下整张图，看得到分支与节点状态染色。
// 点节点切回「执行明细」并定位到该节点，画布与时间线因此是同一份状态的两个视角。
//
// 顶部工具条给「去编排页」的入口：运行详情是只读诊断，要改画布得回到编辑器。

import type { NodeRunStatus, WaitingGate, WorkflowRunDetail } from '@/api/workflows';
import { WorkflowGraphThumb } from '@/components/workflow/WorkflowGraphThumb';
import { RowAction } from '@/components/ui';
import { Pencil } from 'lucide-react';
import { cn } from '@/lib/cn';
import { useI18n } from '@/i18n/locale';

type Props = {
  run: WorkflowRunDetail;
  /** node_id → 执行结果，给画布节点染色。 */
  statuses: Record<string, NodeRunStatus | 'running'>;
  gate: WaitingGate | null;
  selectedId: string | null;
  onSelect(nodeId: string): void;
  /** 跳转到工作流编排页。 */
  onEdit(): void;
};

export function RunCanvasPanel({ run, statuses, gate, selectedId, onSelect, onEdit }: Props) {
  const { tr } = useI18n();

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
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden px-6 pb-5 pt-3">
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-zinc-800/60 bg-zinc-900/30">
        <div className="flex shrink-0 items-center gap-2 border-b border-zinc-800/50 px-3 py-1.5">
          <span className="text-[11px] font-medium text-text-muted">
            {run.workflow_name === '' ? tr('画布', 'Canvas') : run.workflow_name}
          </span>
          <span className="font-mono text-[9.5px] text-text-faint">v{run.workflow_version}</span>
          <div className="ml-auto">
            <RowAction icon={Pencil} onClick={onEdit}>
              {tr('去编排', 'Edit workflow')}
            </RowAction>
          </div>
        </div>
        <div className="min-h-0 flex-1">
          <WorkflowGraphThumb
            graph={graph}
            statuses={statuses}
            gateId={gate === null ? null : gate.gate}
            selectedId={selectedId}
            mode="overview"
            // 节点卡片按可读尺寸铺开，不要为了塞下整图把字缩到看不清
            overviewMaxZoom={1.4}
            onSelect={onSelect}
          />
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-zinc-800/50 px-3 py-1.5 text-[9.5px] text-text-faint">
          <CanvasLegend />
          <span className="ml-auto">{tr('点节点切到执行明细查看该步骤', 'Click a node to inspect it')}</span>
        </div>
      </div>
    </div>
  );
}

function CanvasLegend() {
  const { tr } = useI18n();
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <LegendDot className="bg-ok" label={tr('成功', 'ok')} />
      <LegendDot className="bg-danger" label={tr('失败', 'failed')} />
      <LegendDot className="bg-info" label={tr('运行中', 'running')} />
      <LegendDot className="bg-warn" label={tr('等待人工', 'waiting')} />
      <LegendDot className="bg-zinc-600" label={tr('未执行', 'not run')} />
    </div>
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
