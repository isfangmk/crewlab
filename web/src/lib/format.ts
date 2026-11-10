import { getLocale } from '@/i18n/locale';
import { getSystemTimeZoneId } from '@/store/systemTimezone';

// Map our app-locale tag onto the Intl BCP-47 locale used by the
// Date / Number formatters. We use the APP locale (operator's
// explicit pick) instead of the browser locale because the operator
// may run a Chinese OS but want English dates in the product.
function intlLocale(): string {
  return getLocale() === 'en-US' ? 'en-US' : 'zh-CN';
}

function asDate(input: string | number | Date): Date {
  const t = typeof input === 'string' || typeof input === 'number' ? new Date(input) : input;
  if (Number.isNaN(t.getTime())) {
    throw new Error(`非法时间: ${String(input)}`);
  }
  return t;
}

export function formatBytes(b: number): string {
  if (Number.isNaN(b)) {
    throw new Error('非法字节数');
  }
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = Math.abs(b);
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 ? 2 : 1)} ${units[i]}`;
}

export function clockTime(input: string | number | Date): string {
  const t = asDate(input);
  return t.toLocaleTimeString(intlLocale(), {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZone: getSystemTimeZoneId(),
  });
}

/** 格式化为系统时区下的本地日期时间。 */
export function fullDateTime(input: string | number | Date): string {
  const t = asDate(input);
  return t.toLocaleString(intlLocale(), { timeZone: getSystemTimeZoneId() });
}

export function formatNumber(n: number): string {
  if (Number.isNaN(n)) {
    throw new Error('非法数字');
  }
  return new Intl.NumberFormat(intlLocale()).format(n);
}

const MINUTE_MS = 60 * 1000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

/**
 * 相对时间：刚刚 / N 分钟前 / N 小时前 / N 天前，超过 7 天回落到本地日期。
 * 只用于列表扫读；需要精确值时用 title 挂 fullDateTime。
 */
export function relativeTime(input: string | number | Date, now: Date = new Date()): string {
  const t = asDate(input);
  if (Number.isNaN(now.getTime())) {
    throw new Error('非法当前时间');
  }
  const zh = intlLocale() !== 'en-US';
  // 未来时间（时钟漂移）按「刚刚」处理，不显示负数
  const diff = Math.max(0, now.getTime() - t.getTime());
  if (diff < MINUTE_MS) return zh ? '刚刚' : 'just now';
  if (diff < HOUR_MS) {
    const n = Math.floor(diff / MINUTE_MS);
    return zh ? `${n} 分钟前` : `${n} min ago`;
  }
  if (diff < DAY_MS) {
    const n = Math.floor(diff / HOUR_MS);
    return zh ? `${n} 小时前` : `${n} h ago`;
  }
  if (diff < 7 * DAY_MS) {
    const n = Math.floor(diff / DAY_MS);
    return zh ? `${n} 天前` : `${n} d ago`;
  }
  return fullDateTime(t);
}
