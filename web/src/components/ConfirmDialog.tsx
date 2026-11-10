// ConfirmDialog — 统一的二次确认弹窗。
//
// 页面不要用 window.confirm：原生确认框跟随不了主题、展示不了接口错误，
// 也绕开了全局的焦点管理。这里统一走 Modal + 危险/主按钮，与助理、技能、
// 知识库的删除弹窗同一套版式。

import { useState, type ReactNode } from 'react';

import { Modal } from '@/components/Modal';
import { Button } from '@/components/ui';
import { ApiError } from '@/api/client';
import { useI18n } from '@/i18n/locale';

type Props = {
  open: boolean;
  title: string;
  /** 主体说明：可以带受影响的资源名、后续影响等 */
  description: ReactNode;
  /** 确认按钮文案，默认「删除」 */
  confirmLabel?: string;
  /** 确认按钮是否走危险色；非删除类确认（如归档）传 false */
  danger?: boolean;
  /** 抛错时自动展示在弹窗内，失败不关闭 */
  onConfirm(): Promise<void>;
  onClose(): void;
};

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  danger = true,
  onConfirm,
  onClose,
}: Props) {
  const { tr } = useI18n();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // 执行中不允许点关闭：请求已经发出，关掉弹窗只会让用户以为没生效
  const close = () => {
    if (busy) return;
    setErr(null);
    onClose();
  };

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      await onConfirm();
      onClose();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={close}
      title={title}
      size="sm"
      footer={
        <>
          <Button variant="outline" size="sm" type="button" onClick={close} disabled={busy} className="px-3 py-1.5">
            {tr('取消', 'Cancel')}
          </Button>
          <Button
            variant={danger ? 'danger' : 'primary'}
            size="sm"
            type="button"
            onClick={() => void submit()}
            disabled={busy}
            className="px-3 py-1.5 font-medium"
          >
            {busy
              ? tr('处理中…', 'Working…')
              : (confirmLabel ?? tr('删除', 'Delete'))}
          </Button>
        </>
      }
    >
      <div className="text-xs leading-5 text-zinc-300">
        {err !== null && (
          <div className="mb-3 rounded-md border border-red-500/40 bg-red-500/5 px-3 py-2 text-red-300">
            {err}
          </div>
        )}
        {description}
      </div>
    </Modal>
  );
}
