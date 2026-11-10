// 助理人设编辑：模板 + 系统提示 + 关键提醒 + 输出格式 + 少样本示例。
//
// 这四块在运行时全部由 compose_persona 拼进人设；编辑时也必须一起给，
// 否则用户填了提醒/示例却以为没生效。
import { Plus, Trash2 } from 'lucide-react';

import type { AgentExample, OutputFormat } from '@/api/agents';
import { OUTPUT_FORMATS } from '@/api/agents';
import { Button, Input, Label, Select, Textarea } from '@/components/ui';
import { useI18n } from '@/i18n/locale';
import { PERSONA_TEMPLATES, type PersonaTemplate } from '@/lib/personaTemplates';

export const MAX_EXAMPLES = 3;
export const MAX_EXAMPLE_CHARS = 2000;

/** 示例非法（空值或超长）时禁止保存：后端会 400，前端先拦住。 */
export function examplesInvalid(examples: AgentExample[]): boolean {
  return examples.some(
    (item) =>
      item.input.trim() === '' ||
      item.output.trim() === '' ||
      item.input.length > MAX_EXAMPLE_CHARS ||
      item.output.length > MAX_EXAMPLE_CHARS,
  );
}

export type PersonaValue = {
  systemPrompt: string;
  criticalReminder: string;
  outputFormat: OutputFormat;
  examples: AgentExample[];
};

export const OUTPUT_FORMAT_LABELS: Record<OutputFormat, { zh: string; en: string; hint: { zh: string; en: string } }> = {
  free: {
    zh: '自由文本',
    en: 'Free text',
    hint: { zh: '不额外约束格式', en: 'No extra format constraint' },
  },
  json: {
    zh: 'JSON 对象',
    en: 'JSON object',
    hint: { zh: '只输出一个 JSON 对象，不加代码块与解释', en: 'One JSON object, no code fence or commentary' },
  },
  checklist: {
    zh: '清单',
    en: 'Checklist',
    hint: { zh: 'Markdown 清单，每行一条可执行结论', en: 'Markdown checklist, one actionable item per line' },
  },
};

export function PersonaFields({
  value,
  onChange,
  error,
}: {
  value: PersonaValue;
  onChange: (patch: Partial<PersonaValue>) => void;
  error?: string | null;
}) {
  const { tr } = useI18n();

  const applyTemplate = (tpl: PersonaTemplate) => {
    onChange({
      systemPrompt: tpl.prompt,
      criticalReminder: tpl.criticalReminder,
      outputFormat: tpl.outputFormat,
      examples: tpl.examples.map((item) => ({ ...item })),
    });
  };

  const updateExample = (index: number, patch: Partial<AgentExample>) => {
    const next = value.examples.map((item, i) => (i === index ? { ...item, ...patch } : item));
    onChange({ examples: next });
  };

  const addExample = () => {
    onChange({ examples: [...value.examples, { input: '', output: '' }] });
  };

  const removeExample = (index: number) => {
    onChange({ examples: value.examples.filter((_, i) => i !== index) });
  };

  const exampleInvalid = examplesInvalid(value.examples);

  return (
    <div className="space-y-4">
      <div>
        <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-zinc-400">
          {tr('人设模板', 'Persona template')}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {PERSONA_TEMPLATES.map((tpl) => (
            <Button
              key={tpl.id}
              type="button"
              variant="outline"
              size="sm"
              className="px-2 py-0.5 text-[11px]"
              onClick={() => applyTemplate(tpl)}
            >
              {tr(tpl.zh, tpl.en)}
            </Button>
          ))}
        </div>
        <div className="mt-1 text-[11px] text-zinc-500">
          {tr(
            '套用模板会同时覆盖正文、关键提醒、输出格式与示例。',
            'Applying a template overwrites the prompt, reminder, output format and examples.',
          )}
        </div>
      </div>

      <div>
        <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-zinc-400">
          {tr('系统提示', 'System prompt')} <span className="text-red-400">*</span>
        </div>
        <Textarea
          value={value.systemPrompt}
          onChange={(e) => onChange({ systemPrompt: e.target.value })}
          placeholder={tr(
            '你是 X 专家，遇到 Y 类问题先做 Z...',
            'You are an X expert; when seeing a Y problem, first do Z...',
          )}
          className="h-32 w-full resize-y font-mono"
        />
      </div>

      <div>
        <Label className="block">
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-zinc-400">
            {tr('关键提醒', 'Critical reminder')}
          </div>
          <Textarea
            value={value.criticalReminder}
            onChange={(e) => onChange({ criticalReminder: e.target.value })}
            placeholder={tr(
              '写进「必须遵守」段落：禁止臆测、写操作先给影响面等',
              'Injected under "Must follow": no guessing, state impact before writes, etc.',
            )}
            className="h-20 w-full resize-y"
          />
        </Label>
        <div className="mt-1 text-[11px] text-zinc-500">
          {tr(
            '运行时拼在系统提示之后，优先级高于正文。',
            'Appended after the prompt at runtime, with higher priority.',
          )}
        </div>
      </div>

      <div>
        <Label className="block">
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-zinc-400">
            {tr('输出格式', 'Output format')}
          </div>
          <Select
            label={tr('输出格式', 'Output format')}
            value={value.outputFormat}
            onValueChange={(v) => onChange({ outputFormat: v as OutputFormat })}
            className="w-full"
          >
            {OUTPUT_FORMATS.map((fmt) => (
              <option key={fmt} value={fmt}>
                {tr(OUTPUT_FORMAT_LABELS[fmt].zh, OUTPUT_FORMAT_LABELS[fmt].en)}
              </option>
            ))}
          </Select>
        </Label>
        <div className="mt-1 text-[11px] text-zinc-500">
          {tr(
            OUTPUT_FORMAT_LABELS[value.outputFormat].hint.zh,
            OUTPUT_FORMAT_LABELS[value.outputFormat].hint.en,
          )}
        </div>
      </div>

      <div>
        <div className="mb-1 flex items-center justify-between">
          <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-400">
            {tr('示例', 'Examples')}
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="px-2 py-0.5 text-[11px]"
            onClick={addExample}
            disabled={value.examples.length >= MAX_EXAMPLES}
          >
            <Plus size={11} /> {tr('添加示例', 'Add example')}
          </Button>
        </div>
        {value.examples.length === 0 ? (
          <p className="text-[11px] text-zinc-500">
            {tr('暂无示例；最多 3 条，输入与输出都必填。', 'No examples yet; up to 3, both sides required.')}
          </p>
        ) : (
          <div className="space-y-2">
            {value.examples.map((item, index) => (
              <div key={index} className="rounded-md border border-zinc-800/60 p-2">
                <div className="mb-1 flex items-center justify-between text-[11px] text-zinc-400">
                  <span>{tr(`示例 ${index + 1}`, `Example ${index + 1}`)}</span>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="px-1.5 py-0.5 text-[11px]"
                    onClick={() => removeExample(index)}
                    aria-label={tr(`删除示例 ${index + 1}`, `Delete example ${index + 1}`)}
                  >
                    <Trash2 size={11} />
                  </Button>
                </div>
                <Input
                  type="text"
                  value={item.input}
                  onChange={(e) => updateExample(index, { input: e.target.value })}
                  placeholder={tr('用户会怎么问', 'What the user asks')}
                  className="mb-1.5 w-full"
                  maxLength={MAX_EXAMPLE_CHARS}
                />
                <Textarea
                  value={item.output}
                  onChange={(e) => updateExample(index, { output: e.target.value })}
                  placeholder={tr('期望的回答', 'Expected answer')}
                  className="h-20 w-full resize-y"
                  maxLength={MAX_EXAMPLE_CHARS}
                />
              </div>
            ))}
          </div>
        )}
        {exampleInvalid && (
          <div className="mt-1 text-[11px] text-red-300">
            {tr('示例的输入与输出都不能为空。', 'Both sides of every example are required.')}
          </div>
        )}
      </div>

      {error && (
        <div className="rounded-md border border-red-500/40 bg-red-500/5 px-3 py-2 text-red-300">
          {error}
        </div>
      )}
    </div>
  );
}
