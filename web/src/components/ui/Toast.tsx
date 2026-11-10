// Toast — 统一的轻提示：右下角浮层，成功/失败两种语气。
//
// 用于「操作已生效」这类不需要用户决策的反馈，替代 window.alert；
// 需要用户确认的破坏性操作请用 ConfirmDialog。

import { cn } from '@/lib/cn';

export type ToastTone = 'ok' | 'err';

export function Toast({ tone, text }: { tone: ToastTone; text: string }) {
  return (
    <div
      role="status"
      className={cn(
        'fixed bottom-6 right-6 z-50 max-w-sm rounded-lg px-4 py-2.5 text-sm shadow-2xl ring-1 ring-inset',
        tone === 'ok'
          ? 'bg-emerald-500/15 text-emerald-200 ring-emerald-500/40'
          : 'bg-red-500/15 text-red-200 ring-red-500/40',
      )}
    >
      {text}
    </div>
  );
}
