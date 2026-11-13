// 单节点试跑弹窗：收集开始节点的输入，然后让后端真实执行到该节点为止。
//
// 与整条运行的区别只是「停在哪个节点」，所以输入控件、类型转换与整条运行共用
// RunInputs 那一份实现；不落运行记录，结果只回填到节点面板的「上次运行」页签。

import { useState } from 'react';
import { runNode, type NodeProbeResult, type WorkflowDefinition } from '@/api/workflows';
import { Modal } from '@/components/Modal';
import { Button } from '@/components/ui';
import { buildPayload, initialValues, VariableFields } from '@/components/workflow/RunInputs';
import { useI18n } from '@/i18n/locale';
import { errorText } from '@/lib/errors';

type Props = {
  workflowId: number;
  definition: WorkflowDefinition;
  nodeId: string;
  nodeTitle: string;
  onClose(): void;
  onDone(result: NodeProbeResult): void;
  /** 请求期间把节点卡片切到「运行中」状态。 */
  onBusyChange?(busy: boolean): void;
};

export function NodeRunDialog({ workflowId, definition, nodeId, nodeTitle, onClose, onDone, onBusyChange }: Props) {
  const { tr } = useI18n();
  const start = definition.nodes.find((node) => node.kind === 'start') ?? null;
  const variables = start?.variables ?? [];
  const [values, setValues] = useState<Record<string, string>>(() => initialValues(variables));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const missing = variables.filter((variable) => variable.required && (values[variable.name] ?? '').trim() === '');

  const submit = async () => {
    setBusy(true);
    onBusyChange?.(true);
    setErr(null);
    try {
      const result = await runNode(workflowId, nodeId, buildPayload(variables, values, tr));
      onDone(result);
    } catch (error) {
      setErr(errorText(error));
    } finally {
      setBusy(false);
      onBusyChange?.(false);
    }
  };

  return (
    <Modal
      open
      onClose={() => {
        if (!busy) onClose();
      }}
      title={tr(`试跑「${nodeTitle}」`, `Test run "${nodeTitle}"`)}
      footer={
        <>
          <span className="mr-auto text-[11px] text-text-faint">
            {tr('从开始节点真实执行到这一步，下游不会运行', 'Runs real upstream nodes up to this one; downstream is skipped')}
          </span>
          <Button variant="outline" size="sm" className="h-7 px-3 text-[11.5px]" onClick={onClose} disabled={busy}>
            {tr('取消', 'Cancel')}
          </Button>
          <Button
            variant="primary"
            size="sm"
            className="h-7 px-3 text-[11.5px]"
            onClick={() => void submit()}
            disabled={busy || missing.length > 0}
          >
            {busy ? tr('运行中…', 'Running…') : tr('运行此步', 'Run this step')}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {err !== null && (
          <div role="alert" className="rounded-md border border-danger/40 bg-danger/5 px-3 py-2 text-[11.5px] leading-5 text-danger">
            {err}
          </div>
        )}

        {start === null ? (
          <div className="rounded-md border border-warn/30 bg-warn/5 px-3 py-2 text-[11.5px] text-warn">
            {tr('画布缺少开始节点，无法试跑。', 'The canvas has no start node.')}
          </div>
        ) : (
          <VariableFields
            variables={variables}
            values={values}
            onChange={setValues}
            emptyText={tr('开始节点没有声明输入变量，直接试跑即可。', 'The start node declares no inputs; just run it.')}
          />
        )}

        {missing.length > 0 && (
          <div className="text-[10.5px] text-warn">
            {tr(`还需填写：${missing.map((variable) => variable.name).join('、')}`, `Still required: ${missing.map((v) => v.name).join(', ')}`)}
          </div>
        )}
      </div>
    </Modal>
  );
}
