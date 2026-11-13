import { create } from 'zustand';
import { listSettings } from '@/api/settings';
import { errorText } from '@/lib/errors';

export const DEFAULT_SYSTEM_TIMEZONE = 'UTC+08:00';

const TIMEZONE_IANA: Record<string, string> = {
  UTC: 'UTC',
  'UTC+00:00': 'UTC',
  // Etc/GMT 符号与常规偏移相反：GMT-8 = UTC+8
  'UTC+05:30': 'Asia/Kolkata',
  'UTC+08:00': 'Etc/GMT-8',
  'UTC+09:00': 'Etc/GMT-9',
  'UTC-05:00': 'Etc/GMT+5',
  'UTC-08:00': 'Etc/GMT+8',
};

type SystemTimezoneState = {
  label: string | null;
  loaded: boolean;
  error: string | null;
  setLabel: (label: string) => void;
  refresh: () => Promise<void>;
};

function requireLoadedLabel(): string {
  const { label, loaded } = useSystemTimezone.getState();
  if (!loaded || label === null) {
    throw new Error('系统时区尚未加载');
  }
  return label;
}

export const useSystemTimezone = create<SystemTimezoneState>((set) => ({
  label: null,
  loaded: false,
  error: null,
  setLabel: (label) => {
    if (!(label in TIMEZONE_IANA)) {
      throw new Error(`非法系统时区: ${label}`);
    }
    set({ label, loaded: true, error: null });
  },
  refresh: async () => {
    try {
      const res = await listSettings('observability');
      const row = res.items.find((i) => i.key === 'timezone');
      if (row === undefined) {
        throw new Error('缺少系统时区配置 observability.timezone');
      }
      const label = row.value;
      if (!(label in TIMEZONE_IANA)) {
        throw new Error(`非法系统时区配置: ${label}`);
      }
      set({ label, loaded: true, error: null });
    } catch (e) {
      const message = errorText(e);
      set({ loaded: false, error: message });
      throw e instanceof Error ? e : new Error(message);
    }
  },
}));

export function getSystemTimezoneLabel(): string {
  return requireLoadedLabel();
}

export function getSystemTimeZoneId(): string {
  const label = requireLoadedLabel();
  const id = TIMEZONE_IANA[label];
  if (id === undefined) {
    throw new Error(`非法系统时区: ${label}`);
  }
  return id;
}

/** 把系统时区标签转成 ISO 偏移后缀，如 +08:00。 */
export function getSystemOffsetSuffix(): string {
  const label = requireLoadedLabel();
  if (label === 'UTC' || label === 'UTC+00:00') return '+00:00';
  const m = /^UTC([+-]\d{2}:\d{2})$/.exec(label);
  if (m === null) {
    throw new Error(`非法系统时区: ${label}`);
  }
  return m[1];
}

/** 将系统时区墙钟 YYYY-MM-DDTHH:mm:ss 转为 UTC ISO。 */
export function wallTimeInSystemTzToIso(wall: string): string {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(wall)) {
    throw new Error(`非法墙钟时间: ${wall}`);
  }
  const d = new Date(`${wall}${getSystemOffsetSuffix()}`);
  if (Number.isNaN(d.getTime())) {
    throw new Error(`非法墙钟时间: ${wall}`);
  }
  return d.toISOString();
}

/** 把 UTC/ISO 时刻格式化为系统时区墙钟，供 datetime-local 使用。 */
export function isoToWallDateTimeInSystemTz(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) {
    throw new Error(`非法时间: ${iso}`);
  }
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: getSystemTimeZoneId(),
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(d);
  const get = (type: Intl.DateTimeFormatPartTypes): string => {
    const hit = parts.find((p) => p.type === type);
    if (hit === undefined) {
      throw new Error(`格式化系统时区缺少部件: ${type}`);
    }
    return hit.value;
  };
  return `${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}:${get('second')}`;
}

/** 系统时区下的日历日 YYYY-MM-DD。 */
export function ymdInSystemTz(input: Date = new Date()): string {
  return isoToWallDateTimeInSystemTz(input.toISOString()).slice(0, 10);
}
