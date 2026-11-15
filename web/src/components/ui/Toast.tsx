// Toast — 统一的轻提示：右上角浮层，成功/失败两种语气（sonner 承载）。
//
// 用于「操作已生效」这类不需要用户决策的反馈，替代 window.alert；
// 需要用户确认的破坏性操作请用 ConfirmDialog。
//
// 视觉走全站中性浮层体系（bg-card + border-border + 语义色图标），
// 不再是独立的彩色色块：图标承担语气，文字保持主文字色，双主题都可读。
// 容器与动画由 sonner 提供；各页面只调 flash(tone, text)。

import { AlertTriangle, CheckCircle2 } from 'lucide-react';
import { toast, Toaster } from 'sonner';
import { useThemeMode } from '@/store/mode';

export type ToastTone = 'ok' | 'err';

const TONE_ICON = {
  ok: { Icon: CheckCircle2, cls: 'text-ok' },
  err: { Icon: AlertTriangle, cls: 'text-danger' },
} as const;

/** 操作反馈轻提示：ok=成功、err=失败，4 秒自动消失。 */
export function flash(tone: ToastTone, text: string): void {
  const { Icon, cls } = TONE_ICON[tone];
  toast(text, {
    duration: 4000,
    icon: <Icon size={15} className={cls} aria-hidden />,
  });
}

/** 全局挂载一次（应用根部）：sonner 容器套全站中性浮层视觉。 */
export function AppToaster() {
  const { resolved } = useThemeMode();
  return (
    <Toaster
      position="top-right"
      offset="4.5rem"
      theme={resolved}
      toastOptions={{
        classNames: {
          toast:
            'w-auto max-w-sm rounded-xl border border-border bg-card px-3.5 py-3 shadow-2xl',
          title: 'text-[12.5px] leading-5 text-text',
        },
      }}
    />
  );
}
