import { useAssistantPage } from '@/lib/assistantPage';
import { Button, Input, Label, SectionCard } from '@/components/ui';
import { Select } from '@/components/ui/Select';
import { useCallback, useEffect, useState } from 'react';
import { Clock3, Languages, Loader2, Save } from 'lucide-react';
import { listSettings, setSetting } from '@/api/settings';
import { useI18n } from '@/i18n/locale';

// 助理运行参数：超时 + 输出语言（去掉 ongrid 运行时版本 / 主机写权限等无关项）
const CATEGORY = 'agent';
const LLM_TIMEOUT_KEY = 'llm_timeout_seconds';
const OUTPUT_LOCALE_KEY = 'output_locale';
const DEFAULT_LLM_TIMEOUT_SECONDS = 120;
const MIN_LLM_TIMEOUT_SECONDS = 30;
const MAX_LLM_TIMEOUT_SECONDS = 900;

export default function SettingsAgent() {
  const { tr } = useI18n();
  const [llmTimeoutSeconds, setLLMTimeoutSeconds] = useState(String(DEFAULT_LLM_TIMEOUT_SECONDS));
  const [outputLocale, setOutputLocale] = useState<'' | 'zh' | 'en'>('');
  const [loading, setLoading] = useState(true);
  const [savingTimeout, setSavingTimeout] = useState(false);
  const [savingLocale, setSavingLocale] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await listSettings(CATEGORY);
      const timeoutRow = res.items.find((i) => i.key === LLM_TIMEOUT_KEY);
      const localeRow = res.items.find((i) => i.key === OUTPUT_LOCALE_KEY);
      if (timeoutRow === undefined) {
        throw new Error(`缺少 ${CATEGORY}.${LLM_TIMEOUT_KEY}`);
      }
      if (localeRow === undefined) {
        throw new Error(`缺少 ${CATEGORY}.${OUTPUT_LOCALE_KEY}`);
      }
      setLLMTimeoutSeconds(timeoutRow.value);
      const localeValue = localeRow.value;
      if (localeValue !== '' && localeValue !== 'zh' && localeValue !== 'en') {
        throw new Error(`output_locale 非法: ${localeValue}`);
      }
      setOutputLocale(localeValue);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
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
      setErr(e instanceof Error ? e.message : String(e));
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
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSavingLocale(false);
    }
  }, [outputLocale]);

  useAssistantPage('Agent 设置', 'Agent settings', {
    llm_timeout: llmTimeoutSeconds,
    output_locale: outputLocale === '' ? 'context' : outputLocale,
    loading,
  });

  return (
    <div className="space-y-4">
      {err && <div className="rounded-md border border-red-500/40 bg-red-500/5 px-3 py-2 text-[11px] text-red-300">{err}</div>}

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
                className="w-full font-mono"
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
    </div>
  );
}
