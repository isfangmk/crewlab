// 运行工作流弹窗：按开始节点声明的变量收集输入，先校验再执行。
//
// 校验走 POST /workflows/validate（与保存同一套规则），校验不过就不发运行请求，
// 避免出现一条「failure: 画布非法」的运行记录。

import { useEffect, useState } from 'react';
import { validateDefinition, type WorkflowDefinition } from '@/api/workflows';
import { Modal } from '@/components/Modal';
import { Button } from '@/components/ui';
import { buildPayload, initialValues, VariableFields } from '@/components/workflow/RunInputs';
import { useI18n } from '@/i18n/locale';
import { errorText } from '@/lib/errors';

type Props = {
  workflowId: number;
  workflowName: string;
  definition: WorkflowDefinition;
  onClose(): void;
  /** 校验通过后把输入交给调用方：真正的执行走流式，由编辑器接管画布。 */
  onStarted(values: Record<string, unknown>): void;
};

export function RunWorkflowDialog({ workflowId, workflowName, definition, onClose, onStarted }: Props) {
  const { tr } = useI18n();
  const start = definition.nodes.find((node) => node.kind === 'start') ?? null;
  const variables = start?.variables ?? [];

  // 初始值：默认值优先，必填项留空等用户填。
  const [values, setValues] = useState<Record<string, string>>(() => initialValues(variables));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setValues(initialValues(variables));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId]);

  const missing = variables.filter((variable) => variable.required && (values[variable.name] ?? '').trim() === '');

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const validation = await validateDefinition(definition);
      if (!validation.ok) {
        setErr(tr(`画布校验不通过：${validation.error}`, `Canvas validation failed: ${validation.error}`));
        return;
      }
      onStarted(buildPayload(variables, values, tr));
    } catch (error) {
      setErr(errorText(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={() => {
        if (!busy) onClose();
      }}
      title={tr(`运行「${workflowName}」`, `Run "${workflowName}"`)}
      footer={
        <>
          <span className="mr-auto text-[11px] text-text-faint">
            {tr('执行由 ADK 图驱动，节点按连线顺序运行', 'Execution is driven by the ADK graph')}
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
            {busy ? tr('运行中…', 'Running…') : tr('开始运行', 'Run')}
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
            {tr('画布缺少开始节点，无法运行。', 'The canvas has no start node.')}
          </div>
        ) : (
          <VariableFields
            variables={variables}
            values={values}
            onChange={setValues}
            emptyText={tr('开始节点没有声明输入变量，直接运行即可。', 'The start node declares no inputs; just run it.')}
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
