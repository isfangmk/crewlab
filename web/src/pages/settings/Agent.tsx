import { useAssistantPage } from '@/lib/assistantPage';
import {
  ErrorBanner, Button, Input, Label, SectionCard } from '@/components/ui';
import { Select } from '@/components/ui/Select';
import { useCallback, useEffect, useState } from 'react';
import { Clock3, Gauge, Languages, Layers3, Loader2, Save } from 'lucide-react';
import { listSettings, setSetting } from '@/api/settings';
import { useI18n } from '@/i18n/locale';
import { errorText } from '@/lib/errors';

// 助理运行参数：超时 + 输出语言（去掉 ongrid 运行时版本 / 主机写权限等无关项）
const CATEGORY = 'agent';
const LLM_TIMEOUT_KEY = 'llm_timeout_seconds';
const OUTPUT_LOCALE_KEY = 'output_locale';
const MAX_TOOL_RESULT_KEY = 'max_tool_result_chars';
const CONTEXT_COMPACTION_KEY = 'context_compaction_threshold';
const DEFAULT_LLM_TIMEOUT_SECONDS = 120;
const MIN_LLM_TIMEOUT_SECONDS = 30;
const MAX_LLM_TIMEOUT_SECONDS = 900;
const DEFAULT_MAX_TOOL_RESULT_CHARS = 100000;
const MIN_MAX_TOOL_RESULT_CHARS = 1000;
const MAX_MAX_TOOL_RESULT_CHARS = 2000000;
const DEFAULT_CONTEXT_COMPACTION_THRESHOLD = 400000;
const MIN_CONTEXT_COMPACTION_THRESHOLD = 0;
const MAX_CONTEXT_COMPACTION_THRESHOLD = 2000000;

export default function SettingsAgent() {
  const { tr } = useI18n();
  const [llmTimeoutSeconds, setLLMTimeoutSeconds] = useState(String(DEFAULT_LLM_TIMEOUT_SECONDS));
  const [outputLocale, setOutputLocale] = useState<'' | 'zh' | 'en'>('');
  const [maxToolResultChars, setMaxToolResultChars] = useState(String(DEFAULT_MAX_TOOL_RESULT_CHARS));
  const [contextCompactionThreshold, setContextCompactionThreshold] = useState(String(DEFAULT_CONTEXT_COMPACTION_THRESHOLD));
  const [loading, setLoading] = useState(true);
  const [savingTimeout, setSavingTimeout] = useState(false);
  const [savingLocale, setSavingLocale] = useState(false);
  const [savingToolResult, setSavingToolResult] = useState(false);
  const [savingCompaction, setSavingCompaction] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await listSettings(CATEGORY);
      const timeoutRow = res.items.find((i) => i.key === LLM_TIMEOUT_KEY);
      const localeRow = res.items.find((i) => i.key === OUTPUT_LOCALE_KEY);
      const toolResultRow = res.items.find((i) => i.key === MAX_TOOL_RESULT_KEY);
      const compactionRow = res.items.find((i) => i.key === CONTEXT_COMPACTION_KEY);
      if (timeoutRow === undefined) {
        throw new Error(`缺少 ${CATEGORY}.${LLM_TIMEOUT_KEY}`);
      }
      if (localeRow === undefined) {
        throw new Error(`缺少 ${CATEGORY}.${OUTPUT_LOCALE_KEY}`);
      }
      setLLMTimeoutSeconds(timeoutRow.value);
      // 老库可能尚未种入这些键：回退前端默认，不阻断整页加载。
      setMaxToolResultChars(toolResultRow?.value ?? String(DEFAULT_MAX_TOOL_RESULT_CHARS));
      setContextCompactionThreshold(compactionRow?.value ?? String(DEFAULT_CONTEXT_COMPACTION_THRESHOLD));
      const localeValue = localeRow.value;
      if (localeValue !== '' && localeValue !== 'zh' && localeValue !== 'en') {
        throw new Error(`output_locale 非法: ${localeValue}`);
      }
      setOutputLocale(localeValue);
      setErr(null);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onSaveTimeout = useCallback(async () => {
    const seconds = Number(llmTimeoutSeconds);
    if (!Number.isInteger(seconds) || seconds < MIN_LLM_TIMEOUT_SECONDS || seconds > MAX_LLM_TIMEOUT_SECONDS) {
      setErr(
        tr(
          `超时时间必须是 ${MIN_LLM_TIMEOUT_SECONDS} 到 ${MAX_LLM_TIMEOUT_SECONDS} 的整数秒数。`,
          `Timeout must be a whole number between ${MIN_LLM_TIMEOUT_SECONDS} and ${MAX_LLM_TIMEOUT_SECONDS} seconds.`,
        ),
      );
      return;
    }
    setSavingTimeout(true);
    setErr(null);
    try {
      await setSetting(CATEGORY, LLM_TIMEOUT_KEY, String(seconds), false);
      setLLMTimeoutSeconds(String(seconds));
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setSavingTimeout(false);
    }
  }, [llmTimeoutSeconds, tr]);

  const onSaveLocale = useCallback(async () => {
    setSavingLocale(true);
    setErr(null);
    try {
      await setSetting(CATEGORY, OUTPUT_LOCALE_KEY, outputLocale, false);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setSavingLocale(false);
    }
  }, [outputLocale]);

  const onSaveToolResult = useCallback(async () => {
    const chars = Number(maxToolResultChars);
    if (!Number.isInteger(chars) || chars < MIN_MAX_TOOL_RESULT_CHARS || chars > MAX_MAX_TOOL_RESULT_CHARS) {
      setErr(
        tr(
          `工具结果上限必须是 ${MIN_MAX_TOOL_RESULT_CHARS} 到 ${MAX_MAX_TOOL_RESULT_CHARS} 的整数。`,
          `Tool result cap must be a whole number between ${MIN_MAX_TOOL_RESULT_CHARS} and ${MAX_MAX_TOOL_RESULT_CHARS}.`,
        ),
      );
      return;
    }
    setSavingToolResult(true);
    setErr(null);
    try {
      await setSetting(CATEGORY, MAX_TOOL_RESULT_KEY, String(chars), false);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setSavingToolResult(false);
    }
  }, [maxToolResultChars, tr]);

  const onSaveCompaction = useCallback(async () => {
    const threshold = Number(contextCompactionThreshold);
    if (
      !Number.isInteger(threshold) ||
      threshold < MIN_CONTEXT_COMPACTION_THRESHOLD ||
      threshold > MAX_CONTEXT_COMPACTION_THRESHOLD
    ) {
      setErr(
        tr(
          `上下文压缩阈值必须是 ${MIN_CONTEXT_COMPACTION_THRESHOLD} 到 ${MAX_CONTEXT_COMPACTION_THRESHOLD} 的整数（0 = 关闭）。`,
          `Context compaction threshold must be a whole number between ${MIN_CONTEXT_COMPACTION_THRESHOLD} and ${MAX_CONTEXT_COMPACTION_THRESHOLD} (0 = off).`,
        ),
      );
      return;
    }
    setSavingCompaction(true);
    setErr(null);
    try {
      await setSetting(CATEGORY, CONTEXT_COMPACTION_KEY, String(threshold), false);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setSavingCompaction(false);
    }
  }, [contextCompactionThreshold, tr]);

  useAssistantPage('Agent 设置', 'Agent settings', {
    llm_timeout: llmTimeoutSeconds,
    output_locale: outputLocale === '' ? 'context' : outputLocale,
    loading,
  });

  return (
    <div className="space-y-4">
      {err && <ErrorBanner>{err}</ErrorBanner>}

      <SectionCard
        title={<span className="inline-flex items-center gap-2"><Languages size={13} className="text-zinc-400" />{tr('Agent 输出语言', 'Agent output language')}</span>}
        description={tr(
          '约束对话助理的回答语言。留空则不强加语言指令。',
          'Constrains the chat assistant language. Leave empty to add no language instruction.',
        )}
        footer={!loading ? (
          <Button variant="primary" size="sm" disabled={savingLocale} onClick={() => void onSaveLocale()}>
            <Save size={12} />
            {savingLocale ? tr('保存中…', 'Saving…') : tr('保存语言', 'Save language')}
          </Button>
        ) : undefined}
      >
        {loading ? (
          <div className="flex h-7 items-center text-[11px] text-zinc-500">
            <Loader2 size={12} className="mr-2 animate-spin" /> {tr('加载中…', 'Loading…')}
          </div>
        ) : (
          <Label className="block max-w-sm" htmlFor="agent-output-locale">
            <span className="mb-1 block text-[11px] text-zinc-400">
              {tr('输出语言', 'Output language')}
            </span>
            <Select
              id="agent-output-locale"
              value={outputLocale}
              onValueChange={(selectedValue) => setOutputLocale(selectedValue as '' | 'zh' | 'en')}
              className="w-full"
            >
              <option value="">{tr('不强制', 'No forced language')}</option>
              <option value="zh">中文</option>
              <option value="en">English</option>
            </Select>
          </Label>
        )}
      </SectionCard>

      <SectionCard
        title={<span className="inline-flex items-center gap-2"><Clock3 size={13} className="text-zinc-400" />{tr('LLM 请求超时', 'LLM request timeout')}</span>}
        description={tr('单次模型请求最长等待时间。默认 120 秒。', 'Max wait for one model request. Default 120 seconds.')}
        footer={!loading ? (
          <Button variant="primary" size="sm" disabled={savingTimeout} onClick={() => void onSaveTimeout()}>
            <Save size={12} />
            {savingTimeout ? tr('保存中…', 'Saving…') : tr('保存', 'Save')}
          </Button>
        ) : undefined}
      >
        {loading ? (
          <div className="flex h-7 items-center text-[11px] text-zinc-500">
            <Loader2 size={12} className="mr-2 animate-spin" /> {tr('加载中…', 'Loading…')}
          </div>
        ) : (
          <div className="max-w-sm">
            <Label className="mb-1 block text-[11px] text-zinc-400" htmlFor="agent-llm-timeout-seconds">
              {tr('超时秒数', 'Timeout seconds')}
            </Label>
            <div className="flex items-center gap-2">
              <Input
                id="agent-llm-timeout-seconds"
                type="number"
                min={MIN_LLM_TIMEOUT_SECONDS}
                max={MAX_LLM_TIMEOUT_SECONDS}
                step={1}
                value={llmTimeoutSeconds}
                onChange={(event) => setLLMTimeoutSeconds(event.target.value)}
                className="w-full font-sans"
              />
              <span className="shrink-0 text-[11px] text-zinc-500">{tr('秒', 'seconds')}</span>
            </div>
            <p className="mt-2 text-[11px] leading-relaxed text-zinc-600">
              {tr(
                `允许范围 ${MIN_LLM_TIMEOUT_SECONDS}–${MAX_LLM_TIMEOUT_SECONDS} 秒。`,
                `Allowed range: ${MIN_LLM_TIMEOUT_SECONDS}–${MAX_LLM_TIMEOUT_SECONDS} seconds.`,
              )}
            </p>
          </div>
        )}
      </SectionCard>

      <SectionCard
        title={<span className="inline-flex items-center gap-2"><Gauge size={13} className="text-zinc-400" />{tr('工具结果大小上限', 'Tool result size cap')}</span>}
        description={tr(
          '单个工具结果回喂模型的最大字符数，超出自动截断。防止超大结果撑爆上下文导致对话中断。助理可单独覆盖此默认值。',
          'Max characters of a single tool result fed back to the model; oversized results are truncated. Prevents huge results from overflowing the context and breaking the conversation. Agents can override this default.',
        )}
        footer={!loading ? (
          <Button variant="primary" size="sm" disabled={savingToolResult} onClick={() => void onSaveToolResult()}>
            <Save size={12} />
            {savingToolResult ? tr('保存中…', 'Saving…') : tr('保存', 'Save')}
          </Button>
        ) : undefined}
      >
        {loading ? (
          <div className="flex h-7 items-center text-[11px] text-zinc-500">
            <Loader2 size={12} className="mr-2 animate-spin" /> {tr('加载中…', 'Loading…')}
          </div>
        ) : (
          <div className="max-w-sm">
            <Label className="mb-1 block text-[11px] text-zinc-400" htmlFor="agent-max-tool-result-chars">
              {tr('最大字符数', 'Max characters')}
            </Label>
            <div className="flex items-center gap-2">
              <Input
                id="agent-max-tool-result-chars"
                type="number"
                min={MIN_MAX_TOOL_RESULT_CHARS}
                max={MAX_MAX_TOOL_RESULT_CHARS}
                step={1000}
                value={maxToolResultChars}
                onChange={(event) => setMaxToolResultChars(event.target.value)}
                className="w-full font-sans"
              />
              <span className="shrink-0 text-[11px] text-zinc-500">{tr('字符', 'chars')}</span>
            </div>
            <p className="mt-2 text-[11px] leading-relaxed text-zinc-600">
              {tr(
                `允许范围 ${MIN_MAX_TOOL_RESULT_CHARS}–${MAX_MAX_TOOL_RESULT_CHARS}；默认 100000。`,
                `Allowed range: ${MIN_MAX_TOOL_RESULT_CHARS}–${MAX_MAX_TOOL_RESULT_CHARS}; default 100000.`,
              )}
            </p>
          </div>
        )}
      </SectionCard>

      <SectionCard
        title={<span className="inline-flex items-center gap-2"><Layers3 size={13} className="text-zinc-400" />{tr('上下文压缩阈值', 'Context compaction threshold')}</span>}
        description={tr(
          '历史消息累计达到该 token 数时，自动压缩为摘要，防止长对话或多轮工具结果撑爆模型上下文。填 0 关闭；助理可单独覆盖此默认值。',
          'When accumulated history reaches this token count, it is automatically compacted into a summary to prevent long chats or many tool results from overflowing the model context. 0 = off. Agents can override this default.',
        )}
        footer={!loading ? (
          <Button variant="primary" size="sm" disabled={savingCompaction} onClick={() => void onSaveCompaction()}>
            <Save size={12} />
            {savingCompaction ? tr('保存中…', 'Saving…') : tr('保存', 'Save')}
          </Button>
        ) : undefined}
      >
        {loading ? (
          <div className="flex h-7 items-center text-[11px] text-zinc-500">
            <Loader2 size={12} className="mr-2 animate-spin" /> {tr('加载中…', 'Loading…')}
          </div>
        ) : (
          <div className="max-w-sm">
            <Label className="mb-1 block text-[11px] text-zinc-400" htmlFor="agent-context-compaction-threshold">
              {tr('触发阈值（token）', 'Threshold (tokens)')}
            </Label>
            <div className="flex items-center gap-2">
              <Input
                id="agent-context-compaction-threshold"
                type="number"
                min={MIN_CONTEXT_COMPACTION_THRESHOLD}
                max={MAX_CONTEXT_COMPACTION_THRESHOLD}
                step={10000}
                value={contextCompactionThreshold}
                onChange={(event) => setContextCompactionThreshold(event.target.value)}
                className="w-full font-sans"
              />
              <span className="shrink-0 text-[11px] text-zinc-500">token</span>
            </div>
            <p className="mt-2 text-[11px] leading-relaxed text-zinc-600">
              {tr(
                `允许范围 ${MIN_CONTEXT_COMPACTION_THRESHOLD}–${MAX_CONTEXT_COMPACTION_THRESHOLD}；默认 400000，0 = 关闭。`,
                `Allowed range: ${MIN_CONTEXT_COMPACTION_THRESHOLD}–${MAX_CONTEXT_COMPACTION_THRESHOLD}; default 400000, 0 = off.`,
              )}
            </p>
          </div>
        )}
      </SectionCard>
    </div>
  );
}
