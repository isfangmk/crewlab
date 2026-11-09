import { useEffect, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { Modal } from '@/components/Modal';
import { Button, Label, Textarea } from '@/components/ui';
import { useI18n } from '@/i18n/locale';

/** 拒绝工具审批：必填原因。 */
export function RejectApprovalDialog({
  open,
  subject,
  onClose,
  onConfirm,
}: {
  open: boolean;
  /** 展示给用户的审批对象摘要，例如工具名或标题。 */
  subject?: string;
  onClose: () => void;
  onConfirm: (reason: string) => Promise<void>;
}) {
  const { tr } = useI18n();
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!open) return;
    setReason('');
    setBusy(false);
    setErr(null);
    const t = window.setTimeout(() => textareaRef.current?.focus(), 0);
    return () => window.clearTimeout(t);
  }, [open]);

  const submit = async () => {
    const trimmed = reason.trim();
    if (trimmed === '') {
      setErr(tr('拒绝原因必填', 'Reject reason is required'));
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await onConfirm(trimmed);
      onClose();
    } catch (e) {
      setErr((e as Error).message);
      setBusy(false);
    }
  };

  if (!open) return null;

  return (
    <Modal
      open
      onClose={() => {
        if (busy) return;
        onClose();
      }}
      size="sm"
      title={tr('拒绝执行', 'Reject execution')}
      footer={
        <>
          <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>
            {tr('取消', 'Cancel')}
          </Button>
          <Button type="button" variant="danger" onClick={() => void submit()} disabled={busy}>
            {busy ? <Loader2 size={13} className="animate-spin" /> : null}
            {busy ? tr('提交中…', 'Submitting…') : tr('确认拒绝', 'Confirm reject')}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-sm text-zinc-400">
          {subject
            ? tr(`将拒绝「${subject}」，不会执行该操作。`, `Reject “${subject}”. It will not run.`)
            : tr('拒绝后不会执行该操作，请说明原因。', 'This action will not run. Please provide a reason.')}
        </p>
        <Label className="block">
          <span className="mb-1.5 block text-[12px] text-zinc-500">
            {tr('拒绝原因（必填）', 'Reject reason (required)')}
          </span>
          <Textarea
            ref={textareaRef}
            rows={4}
            value={reason}
            disabled={busy}
            placeholder={tr('例如：参数有误 / 不在维护窗口…', 'e.g. wrong args / outside maintenance window…')}
            onChange={(e) => {
              setReason(e.target.value);
              if (err) setErr(null);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                void submit();
              }
            }}
            className="w-full resize-y"
          />
        </Label>
        {err ? (
          <div className="rounded-lg border border-red-500/40 bg-red-500/5 px-3 py-2 text-sm text-red-300">
            {err}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
