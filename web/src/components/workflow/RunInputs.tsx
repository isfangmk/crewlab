// 运行输入控件：整条工作流运行（RunWorkflowDialog）与单节点试跑（NodeRunDialog）
// 收集的都是开始节点声明的变量，所以输入控件、默认值与类型转换必须共用一份，
// 否则同一个变量在两条入口里会出现两种校验口径。

import { useState } from 'react';
import type { WorkflowVariable } from '@/api/workflows';
import { Input, Label, Switch, Textarea } from '@/components/ui';
import { cn } from '@/lib/cn';
import { useI18n } from '@/i18n/locale';

export function VariableInput({ variable, value, onChange }: { variable: WorkflowVariable; value: string; onChange(next: string): void }) {
  const { tr } = useI18n();
  const [jsonError, setJsonError] = useState<string | null>(null);
  if (variable.type === 'boolean') {
    return (
      <label className="flex h-8 items-center justify-between rounded-md border border-border bg-bg/40 px-2.5 text-[11.5px] text-text-muted">
        {value === 'true' ? tr('是', 'True') : tr('否', 'False')}
        <Switch
          checked={value === 'true'}
          onCheckedChange={(checked) => onChange(checked ? 'true' : 'false')}
          aria-label={variable.name}
        />
      </label>
    );
  }
  if (variable.type === 'json') {
    return (
      <div className="space-y-0.5">
        <Textarea
          value={value}
          rows={2}
          aria-label={variable.name}
          onChange={(event) => {
            onChange(event.target.value);
            setJsonError(null);
          }}
          onBlur={() => {
            const text = value.trim();
            if (text === '') return;
            try {
              JSON.parse(text);
              setJsonError(null);
            } catch {
              setJsonError(tr('不是合法 JSON', 'Not valid JSON'));
            }
          }}
          className={cn('font-mono text-[11px]', jsonError !== null && 'border-danger/60')}
          placeholder='{"key": "value"}'
        />
        {jsonError !== null && <div className="text-[10px] text-danger">{jsonError}</div>}
      </div>
    );
  }
  return (
    <Input
      type={variable.type === 'number' ? 'number' : 'text'}
      value={value}
      aria-label={variable.name}
      onChange={(event) => onChange(event.target.value)}
      className="h-8 text-[11.5px]"
      placeholder={variable.type === 'number' ? '0' : tr('输入值', 'Value')}
    />
  );
}

/** 变量表单：开始节点没有变量时给出「直接运行」的说明。 */
export function VariableFields({
  variables,
  values,
  onChange,
  emptyText,
}: {
  variables: WorkflowVariable[];
  values: Record<string, string>;
  onChange(next: Record<string, string>): void;
  emptyText: string;
}) {
  const { tr } = useI18n();
  if (variables.length === 0) {
    return <div className="rounded-md border border-border bg-bg/30 px-3 py-2 text-[11.5px] text-text-muted">{emptyText}</div>;
  }
  return (
    <div className="space-y-2.5">
      {variables.map((variable) => (
        <div key={variable.name} className="space-y-1">
          <div className="flex items-baseline gap-1.5">
            <Label className="font-mono text-[11.5px] text-text">{variable.name}</Label>
            <span className="rounded bg-zinc-500/10 px-1 text-[9.5px] leading-4 text-text-faint">{variable.type}</span>
            {variable.required && <span className="text-[10px] text-danger">{tr('必填', 'required')}</span>}
            {variable.description !== '' && <span className="truncate text-[10.5px] text-text-faint">· {variable.description}</span>}
          </div>
          <VariableInput
            variable={variable}
            value={values[variable.name] ?? ''}
            onChange={(next) => onChange({ ...values, [variable.name]: next })}
          />
        </div>
      ))}
    </div>
  );
}

export function defaultText(variable: WorkflowVariable): string {
  if (variable.default === null || variable.default === undefined) return '';
  return variable.type === 'string' ? String(variable.default) : JSON.stringify(variable.default);
}

export function initialValues(variables: WorkflowVariable[]): Record<string, string> {
  const init: Record<string, string> = {};
  for (const variable of variables) init[variable.name] = defaultText(variable);
  return init;
}

export function coerce(raw: string, type: WorkflowVariable['type'], tr: (zh: string, en: string) => string): unknown {
  if (type === 'string') return raw;
  if (type === 'number') {
    const value = Number(raw);
    if (!Number.isFinite(value)) throw new Error(tr(`需要数字，收到: ${raw}`, `Expected a number, got: ${raw}`));
    return value;
  }
  if (type === 'boolean') {
    if (raw === 'true') return true;
    if (raw === 'false') return false;
    throw new Error(tr(`需要布尔值，收到: ${raw}`, `Expected a boolean, got: ${raw}`));
  }
  try {
    return JSON.parse(raw);
  } catch {
    throw new Error(tr(`需要 JSON，收到: ${raw}`, `Expected JSON, got: ${raw}`));
  }
}

/** 把表单值转成后端要的 payload；非必填留空直接跳过（后端用默认值/缺省）。 */
export function buildPayload(
  variables: WorkflowVariable[],
  values: Record<string, string>,
  tr: (zh: string, en: string) => string,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  for (const variable of variables) {
    const raw = values[variable.name] ?? '';
    if (raw.trim() === '' && !variable.required) continue;
    payload[variable.name] = coerce(raw, variable.type, tr);
  }
  return payload;
}
