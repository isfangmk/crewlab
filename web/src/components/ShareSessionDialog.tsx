import { useEffect, useState } from 'react';
import { Check, Copy, Link2, Loader2 } from 'lucide-react';
import { createSessionShare } from '@/api/chat';
import { ApiError } from '@/api/client';
import { Modal } from '@/components/Modal';
import { Button, Input, Label } from '@/components/ui';
import { useI18n } from '@/i18n/locale';
import { fullDateTime } from '@/lib/format';
import { cn } from '@/lib/cn';

const PRESETS = [
  { hours: 24, zh: '24 小时', en: '24 hours' },
  { hours: 72, zh: '3 天', en: '3 days' },
  { hours: 168, zh: '7 天', en: '7 days' },
  { hours: 720, zh: '30 天', en: '30 days' },
] as const;

/** 会话只读分享：选择有效期、生成链接并复制。 */
export function ShareSessionDialog({
  open,
  sessionId,
  onClose,
}: {
  open: boolean;
  sessionId: string;
  onClose: () => void;
}) {
  const { tr } = useI18n();
  const [hours, setHours] = useState(24);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<{ link: string; expiresAt: string } | null>(null);
  const [copied, setCopied] = useState(false);

  // 每次打开弹窗回到初始态，避免上次生成结果残留。
  useEffect(() => {
    if (!open) return;
    setHours(24);
    setBusy(false);
    setErr(null);
    setResult(null);
    setCopied(false);
  }, [open, sessionId]);

  const resetAndClose = () => {
    onClose();
  };

  const create = async () => {
    if (!Number.isInteger(hours) || hours < 1 || hours > 24 * 30) {
      setErr(tr('有效期须为 1～720 小时的整数', 'Expiry must be an integer from 1 to 720 hours'));
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const share = await createSessionShare(sessionId, hours);
      if (typeof share.token !== 'string' || share.token === '') {
        throw new Error('分享响应缺少 token');
      }
      if (typeof share.expires_at !== 'string' || share.expires_at === '') {
        throw new Error('分享响应缺少 expires_at');
      }
      const link = `${window.location.origin}/share/${share.token}`;
      await navigator.clipboard.writeText(link);
      setResult({ link, expiresAt: share.expires_at });
      setCopied(true);
    } catch (e) {
      setResult(null);
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const copyAgain = async () => {
    if (result === null) {
      throw new Error('尚未生成分享链接');
    }
    await navigator.clipboard.writeText(result.link);
    setCopied(true);
  };

  if (!open) return null;

  return (
    <Modal
      open
      onClose={resetAndClose}
      size="md"
      title={tr('分享会话', 'Share session')}
      footer={
        result === null ? (
          <>
            <Button type="button" variant="ghost" onClick={resetAndClose} disabled={busy}>
              {tr('取消', 'Cancel')}
            </Button>
            <Button type="button" variant="primary" onClick={() => void create()} disabled={busy}>
              {busy ? <Loader2 size={13} className="animate-spin" /> : <Link2 size={13} />}
              {busy ? tr('生成中…', 'Creating…') : tr('生成并复制链接', 'Create & copy link')}
            </Button>
          </>
        ) : (
          <>
            <Button type="button" variant="outline" onClick={() => void copyAgain()}>
              {copied ? <Check size={13} /> : <Copy size={13} />}
              {copied ? tr('已复制', 'Copied') : tr('再复制', 'Copy again')}
            </Button>
            <Button type="button" variant="primary" onClick={resetAndClose}>
              {tr('完成', 'Done')}
            </Button>
          </>
        )
      }
    >
      <div className="space-y-4">
        <p className="text-sm text-zinc-400">
          {tr(
            '生成只读链接，对方打开即可查看本会话，无需登录。',
            'Create a read-only link anyone can open without signing in.',
          )}
        </p>

        {result === null ? (
          <>
            <div>
              <div className="mb-2 text-[12px] text-zinc-500">{tr('有效期', 'Expires in')}</div>
              <div className="flex flex-wrap gap-2">
                {PRESETS.map((p) => (
                  <button
                    key={p.hours}
                    type="button"
                    disabled={busy}
                    onClick={() => setHours(p.hours)}
                    className={cn(
                      'rounded-md border px-3 py-1.5 text-[12px] transition-colors',
                      hours === p.hours
                        ? 'border-indigo-500/60 bg-indigo-500/15 text-indigo-200'
                        : 'border-zinc-700/80 bg-transparent text-zinc-400 hover:border-zinc-600 hover:text-zinc-200',
                    )}
                  >
                    {tr(p.zh, p.en)}
                  </button>
                ))}
              </div>
            </div>
            <Label className="block">
              <span className="mb-1 block text-[12px] text-zinc-500">
                {tr('自定义小时数（1–720）', 'Custom hours (1–720)')}
              </span>
              <Input
                type="number"
                min={1}
                max={720}
                step={1}
                value={String(hours)}
                disabled={busy}
                onChange={(e) => {
                  const n = Number.parseInt(e.target.value, 10);
                  if (Number.isFinite(n)) setHours(n);
                }}
                className="w-36"
              />
            </Label>
          </>
        ) : (
          <div className="space-y-3">
            <Label className="block">
              <span className="mb-1 block text-[12px] text-zinc-500">{tr('分享链接', 'Share link')}</span>
              <Input readOnly value={result.link} className="w-full font-mono text-[12px]" />
            </Label>
            <p className="text-[12px] text-zinc-500">
              {tr('过期时间：', 'Expires: ')}
              {fullDateTime(result.expiresAt)}
            </p>
          </div>
        )}

        {err ? (
          <div className="rounded-lg border border-red-500/40 bg-red-500/5 px-3 py-2 text-sm text-red-300">
            {err}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
