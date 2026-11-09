import { useAssistantPage } from '@/lib/assistantPage';
import { Button, DetailSection, Label, SectionCard } from '@/components/ui';
import { Hint } from '@/components/ui/Tooltip';
import { Select } from '@/components/ui/Select';
import { useCallback, useEffect, useState } from 'react';
import { Check, Globe, Globe2, Loader2, Monitor, Moon, Palette, Save, Sun } from 'lucide-react';
import { setSetting } from '@/api/settings';
import { ACCENT_PRESETS, useTheme } from '@/store/theme';
import { useI18n } from '@/i18n/locale';
import { useThemeMode, type ThemePreference } from '@/store/mode';
import { getSystemTimezoneLabel, useSystemTimezone } from '@/store/systemTimezone';
import { cn } from '@/lib/cn';

const THEME_OPTIONS: { id: ThemePreference; icon: typeof Sun }[] = [
  { id: 'system', icon: Monitor },
  { id: 'light', icon: Sun },
  { id: 'dark', icon: Moon },
];

const OBS_CATEGORY = 'observability';
const TIMEZONE_KEY = 'timezone';
const TIMEZONE_OPTIONS = [
  'UTC',
  'UTC+00:00',
  'UTC+05:30',
  'UTC+08:00',
  'UTC+09:00',
  'UTC-05:00',
  'UTC-08:00',
] as const;

export default function SettingsPreferences() {
  const accentId = useTheme((s) => s.accentId);
  const setAccent = useTheme((s) => s.setAccent);
  const { tr, locale, setLocale } = useI18n();
  const { preference: themePref, setPreference: setTheme } = useThemeMode();
  const setSystemTzLabel = useSystemTimezone((s) => s.setLabel);
  const refreshSystemTz = useSystemTimezone((s) => s.refresh);
  const [timezone, setTimezone] = useState('');
  const [tzLoading, setTzLoading] = useState(true);
  const [tzSaving, setTzSaving] = useState(false);
  const [tzErr, setTzErr] = useState<string | null>(null);

  const loadTimezone = useCallback(async () => {
    try {
      await refreshSystemTz();
      // 时区标签未加载时 getSystemTimezoneLabel 显式抛错
      setTimezone(getSystemTimezoneLabel());
      setTzErr(null);
    } catch (e) {
      setTzErr(e instanceof Error ? e.message : String(e));
    } finally {
      setTzLoading(false);
    }
  }, [refreshSystemTz]);

  useEffect(() => {
    void loadTimezone();
  }, [loadTimezone]);

  const onSaveTimezone = useCallback(async () => {
    if (!(TIMEZONE_OPTIONS as readonly string[]).includes(timezone)) {
      setTzErr(tr('请选择合法时区', 'Pick a valid timezone'));
      return;
    }
    setTzSaving(true);
    setTzErr(null);
    try {
      await setSetting(OBS_CATEGORY, TIMEZONE_KEY, timezone, false);
      setSystemTzLabel(timezone);
    } catch (e) {
      setTzErr(e instanceof Error ? e.message : String(e));
    } finally {
      setTzSaving(false);
    }
  }, [timezone, tr, setSystemTzLabel]);

  useAssistantPage('偏好设置', 'Preferences', {
    locale,
    theme: themePref,
    accent: accentId,
    timezone,
  });

  return (
    <div className="space-y-4">
      <SectionCard
        title={<span className="inline-flex items-center gap-2"><Globe size={13} className="text-zinc-400" />{tr('语言', 'Language')}</span>}
        description={tr(
          '切换界面语言。当前以高频路径覆盖为主，其余页面持续中文。',
          'Switch the UI language. High-traffic surfaces are translated first; the rest of the app stays in Chinese for now.',
        )}
      >
        <div className="flex flex-wrap gap-2">
          {(['zh-CN', 'en-US'] as const).map((l) => {
            const active = l === locale;
            return (
              <Button
                variant="outline"
                size="sm"
                key={l}
                type="button"
                onClick={() => setLocale(l)}
                className={cn(
                  'gap-2 px-2.5 text-[11px]',
                  active
                    ? 'border-indigo-500/40 bg-indigo-500/10 text-zinc-100'
                    : 'border-zinc-800 text-zinc-400 hover:bg-zinc-800/40 hover:text-zinc-200',
                )}
              >
                <span className="font-medium">{l === 'zh-CN' ? '中文' : 'English'}</span>
                {active && <Check size={12} className="text-indigo-300" />}
              </Button>
            );
          })}
        </div>
      </SectionCard>

      <SectionCard
        title={<span className="inline-flex items-center gap-2"><Globe2 size={13} className="text-zinc-400" />{tr('系统时区', 'System timezone')}</span>}
        description={tr('系统统一使用的时区。保存后立即生效。', 'The timezone used across the system. Applies right after you save.')}
        footer={!tzLoading ? (
          <Button variant="primary" size="sm" disabled={tzSaving} onClick={() => void onSaveTimezone()}>
            <Save size={12} />
            {tzSaving ? tr('保存中…', 'Saving…') : tr('保存', 'Save')}
          </Button>
        ) : undefined}
      >
        {tzErr && <div className="mb-3 text-[11px] text-red-400">{tzErr}</div>}
        {tzLoading ? (
          <div className="flex h-7 items-center text-[11px] text-zinc-500">
            <Loader2 size={12} className="mr-2 animate-spin" /> {tr('加载中…', 'Loading…')}
          </div>
        ) : (
          <Label className="block max-w-sm" htmlFor="pref-timezone">
            <span className="mb-1 block text-[11px] text-zinc-400">{tr('时区', 'Timezone')}</span>
            <Select
              id="pref-timezone"
              value={timezone}
              onValueChange={setTimezone}
              className="w-full"
            >
              {TIMEZONE_OPTIONS.map((tz) => (
                <option key={tz} value={tz}>
                  {tz}
                </option>
              ))}
            </Select>
          </Label>
        )}
      </SectionCard>

      <SectionCard
        title={<span className="inline-flex items-center gap-2"><Sun size={13} className="text-zinc-400" />{tr('主题', 'Theme')}</span>}
        description={tr(
          '跟随系统、浅色、深色三档。浅色支持仍在迁移中，部分页面会看到深色 token 残留。',
          'System, light, dark. Light mode is in progress; some pages still show dark tokens.',
        )}
      >
        <div className="flex flex-wrap gap-2">
          {THEME_OPTIONS.map(({ id, icon: Icon }) => {
            const active = id === themePref;
            const label =
              id === 'system'
                ? tr('跟随系统', 'System')
                : id === 'light'
                  ? tr('浅色', 'Light')
                  : tr('深色', 'Dark');
            return (
              <Button
                variant="outline"
                size="sm"
                key={id}
                type="button"
                onClick={() => setTheme(id)}
                className={cn(
                  'gap-2 px-2.5 text-[11px]',
                  active
                    ? 'border-indigo-500/40 bg-indigo-500/10 text-zinc-100'
                    : 'border-zinc-800 text-zinc-400 hover:bg-zinc-800/40 hover:text-zinc-200',
                )}
              >
                <Icon size={12} />
                <span className="font-medium">{label}</span>
                {active && <Check size={12} className="text-indigo-300" />}
              </Button>
            );
          })}
        </div>
      </SectionCard>

      <SectionCard
        title={<span className="inline-flex items-center gap-2"><Palette size={13} className="text-zinc-400" />{tr('主题色', 'Accent color')}</span>}
        description={tr(
          '选择一种品牌色作为高亮 / CTA / 当前选中态。来源自 Ongrid logo 的两束渐变。',
          'Pick a brand color for highlights, primary CTAs, and active state. Drawn from the two gradients in the Ongrid logo.',
        )}
      >
        <div className="flex flex-wrap gap-2">
          {ACCENT_PRESETS.map((p) => {
            const active = p.id === accentId;
            return (
              <Hint key={p.id} content={`${p.label} · ${p.hex}`}>
                <button
                  type="button"
                  onClick={() => setAccent(p.id)}
                  className={cn(
                    'group flex h-7 items-center gap-2 rounded-md border px-2.5 text-[11px] transition',
                    active
                      ? 'border-indigo-500/40 bg-indigo-500/10 text-zinc-100'
                      : 'border-zinc-800 text-zinc-400 hover:bg-zinc-800/40 hover:text-zinc-200',
                  )}
                >
                  <span
                    aria-hidden
                    className="inline-block h-3.5 w-3.5 shrink-0 rounded-full ring-1 ring-zinc-700"
                    style={{ backgroundColor: p.hex }}
                  />
                  <span className="font-medium">{p.label}</span>
                  {active && <Check size={12} className="text-indigo-300" />}
                </button>
              </Hint>
            );
          })}
        </div>
        <DetailSection title={tr('预览', 'Preview')} className="mt-4">
          <div className="flex flex-wrap items-center gap-3 rounded-md border border-zinc-800/60 bg-zinc-950/40 px-3 py-2.5">
            <Button variant="primary" size="sm" type="button">
              {tr('主要按钮', 'Primary button')}
            </Button>
            <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[11px] text-accent ring-1 ring-accent/30">
              {tr('已选中', 'Selected')}
            </span>
            <span className="text-[11px] text-accent">{tr('链接文字', 'Link text')}</span>
            <span className="h-2 w-24 rounded-full bg-zinc-800">
              <span className="block h-full w-2/3 rounded-full bg-accent" />
            </span>
          </div>
        </DetailSection>
      </SectionCard>
    </div>
  );
}
