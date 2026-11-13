// Toast — 统一的轻提示：右下角浮层，成功/失败两种语气。
//
// 用于「操作已生效」这类不需要用户决策的反馈，替代 window.alert；
// 需要用户确认的破坏性操作请用 ConfirmDialog。
//
// 视觉走全站中性浮层体系（bg-card + border-border + 语义色图标），
// 不再是独立的彩色色块：图标承担语气，文字保持主文字色，双主题都可读。

import { AlertTriangle, CheckCircle2 } from 'lucide-react';
import { cn } from '@/lib/cn';

export type ToastTone = 'ok' | 'err';

const TONE_ICON = {
  ok: { Icon: CheckCircle2, cls: 'text-ok' },
  err: { Icon: AlertTriangle, cls: 'text-danger' },
} as const;

export function Toast({ tone, text }: { tone: ToastTone; text: string }) {
  const { Icon, cls } = TONE_ICON[tone];
  return (
    <div
      role={tone === 'ok' ? 'status' : 'alert'}
      className="anim-rise fixed bottom-6 right-6 z-50 flex max-w-sm items-start gap-2.5 rounded-xl border border-border bg-card px-3.5 py-3 shadow-2xl"
    >
      <Icon size={15} className={cn('mt-0.5 shrink-0', cls)} aria-hidden />
      <span className="text-[12.5px] leading-5 text-text">{text}</span>
    </div>
  );
}
