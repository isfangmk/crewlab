// 列表时间排序：空值始终排在最后，供客户端分页页面复用。
export type TimeSortOrder = 'asc' | 'desc';

function timeValue(value: string | null | undefined): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

/** 比较两个时间字符串；空值不参与方向切换，永远排最后。 */
export function compareNullableTime(
  a: string | null | undefined,
  b: string | null | undefined,
  order: TimeSortOrder,
): number {
  const left = timeValue(a);
  const right = timeValue(b);
  if (left === null && right === null) return 0;
  if (left === null) return 1;
  if (right === null) return -1;
  return order === 'asc' ? left - right : right - left;
}
