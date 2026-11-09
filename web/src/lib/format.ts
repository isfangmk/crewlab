import { getLocale, tr as trInline } from '@/i18n/locale';
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

export function relativeTime(input: string | number | Date): string {
  const t = asDate(input);
  const ms = Date.now() - t.getTime();
  const sec = Math.round(ms / 1000);
  if (sec < 5) return trInline('刚刚', 'just now');
  if (sec < 60) return trInline(`${sec} 秒前`, `${sec}s ago`);
  const min = Math.round(sec / 60);
  if (min < 60) return trInline(`${min} 分钟前`, `${min}m ago`);
  const hr = Math.round(min / 60);
  if (hr < 24) return trInline(`${hr} 小时前`, `${hr}h ago`);
  const day = Math.round(hr / 24);
  if (day < 30) return trInline(`${day} 天前`, `${day}d ago`);
  return t.toLocaleDateString(intlLocale(), { timeZone: getSystemTimeZoneId() });
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
