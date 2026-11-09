/** 列表页通用时间筛选：预设或自定义范围，统一转成 ISO 供 *_from / *_to 查询。 */
export type TimePreset = 'all' | 'today' | '7d' | '30d' | 'custom';

function toIso(date: Date): string {
  return date.toISOString();
}

function startOfLocalDay(d = new Date()): Date {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  return x;
}

/** 预设区间；`custom` 需由调用方用起止输入框解析。 */
export function rangeForPreset(preset: TimePreset): { from?: string; to?: string } {
  if (preset === 'all') return {};
  const now = new Date();
  if (preset === 'today') {
    return { from: toIso(startOfLocalDay(now)), to: toIso(now) };
  }
  if (preset === '7d') {
    return { from: toIso(new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000)), to: toIso(now) };
  }
  if (preset === '30d') {
    return { from: toIso(new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000)), to: toIso(now) };
  }
  return {};
}

/** datetime-local 是无时区的本地时间，转成带时区的 ISO 再交给后端。 */
export function parseLocalDateTime(value: string): string {
  if (value === '') {
    throw new Error('时间不能为空');
  }
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) {
    throw new Error(`非法时间: ${value}`);
  }
  return d.toISOString();
}
