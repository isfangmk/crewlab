// 错误信息提取：把各种异常统一成一句能直接展示给用户的文字。
//
// 全站各页面的 catch 分支都在做同一件事：
//   errorText(e)
// 散落几十处后容易写漏写错，收在这里一次。

import { ApiError } from '@/api/client';

/** 异常转展示文案；未知异常兜底成通用提示，不把内部细节抛给用户。 */
export function errorText(e: unknown, fallback = '操作失败'): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error && e.message !== '') return e.message;
  return fallback;
}
