import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, KeyRound, Loader2, ShieldCheck } from 'lucide-react';
import { Button, Chip, CodeBlock, Input, Label, StatusPill } from '@/components/ui';
import { Modal } from '@/components/Modal';
import {
  getSecretUsage,
  rotateSecret,
  verifySecretUsage,
  type CredType,
  type SecretHealthItem,
  type SecretUsageItem,
  type SecretVerifyItem,
  type SecretView,
} from '@/api/secrets';
import { useI18n } from '@/i18n/locale';
import { fullDateTime } from '@/lib/format';
import { cn } from '@/lib/cn';
import { errorText } from '@/lib/errors';

type Props = {
  /** null = 关闭；每次换对象都从第一步重新开始。 */
  secret: SecretView | null;
  types: CredType[];
  health?: SecretHealthItem;
  onClose(): void;
  onRotated(updated: SecretView): void;
};

const STEPS = ['现状', '新值', '确认'] as const;
const DONE_STEP = 3;

// 凭证轮换向导：先看清影响面，再填新值，最后确认；旧值全程不回显。
export function SecretRotateDialog({ secret, types, health, onClose, onRotated }: Props) {
  const { tr } = useI18n();
  const [step, setStep] = useState(0);
  const [usage, setUsage] = useState<SecretUsageItem[]>([]);
  const [loadingUsage, setLoadingUsage] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [rotated, setRotated] = useState<SecretView | null>(null);
  const [verifyItems, setVerifyItems] = useState<SecretVerifyItem[] | null>(null);
  const [verifying, setVerifying] = useState(false);

  const secretId = secret?.id ?? null;

  useEffect(() => {
    if (secretId === null) return;
    setStep(0);
    setValues({});
    setRotated(null);
    setVerifyItems(null);
    setErr(null);
    setUsage([]);
    setLoadingUsage(true);
    let cancelled = false;
    void getSecretUsage(secretId)
      .then((res) => {
        if (!cancelled) setUsage(res.items ?? []);
      })
      .catch((e) => {
        if (!cancelled) {
          setErr(errorText(e));
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingUsage(false);
      });
    return () => {
      cancelled = true;
    };
  }, [secretId]);

  const typeLabel = (typeName: string) =>
    types.find((t) => t.name === typeName)?.label ?? typeName;

  const fieldLabel = (key: string) => {
    const spec = types.find((t) => t.name === secret?.type);
    return spec?.fields.find((f) => f.key === key)?.label ?? key;
  };

  const broken = health !== undefined && !health.ok;
  const filled = Object.entries(values).filter(([, v]) => v !== '');
  const missingWhenBroken = broken
    ? (secret?.field_keys ?? []).filter((k) => (values[k] ?? '') === '')
    : [];

  const canAdvanceFromValues = broken
    ? missingWhenBroken.length === 0
    : filled.length > 0;

  const submit = useCallback(async () => {
    if (secret === null) return;
    setBusy(true);
    setErr(null);
    try {
      const payload: Record<string, string> = {};
      for (const [key, value] of Object.entries(values)) {
        if (value !== '') payload[key] = value;
      }
      const updated = await rotateSecret(secret.id, payload);
      setRotated(updated);
      onRotated(updated);
      setStep(DONE_STEP);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  }, [onRotated, secret, values]);

  const runVerify = useCallback(async () => {
    if (secret === null) return;
    setVerifying(true);
    setErr(null);
    try {
      const res = await verifySecretUsage(secret.id);
      setVerifyItems(res.items ?? []);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setVerifying(false);
    }
  }, [secret]);

  if (secret === null) return null;

  const footer =
    step === DONE_STEP ? (
      <>
        {usage.length > 0 ? (
          <Button
            variant="ghost"
            size="sm"
            type="button"
            disabled={verifying}
            onClick={() => void runVerify()}
          >
            {verifying ? <Loader2 size={12} className="animate-spin" /> : <ShieldCheck size={12} />}
            {tr('验证引用连接', 'Verify references')}
          </Button>
        ) : null}
        <Button variant="primary" size="sm" type="button" onClick={onClose}>
          {tr('完成', 'Done')}
        </Button>
      </>
    ) : (
      <>
        <Button variant="ghost" size="sm" type="button" onClick={onClose} disabled={busy}>
          {tr('取消', 'Cancel')}
        </Button>
        {step > 0 ? (
          <Button
            variant="ghost"
            size="sm"
            type="button"
            disabled={busy}
            onClick={() => setStep((s) => s - 1)}
          >
            {tr('上一步', 'Back')}
          </Button>
        ) : null}
        {step < 2 ? (
          <Button
            variant="primary"
            size="sm"
            type="button"
            disabled={step === 1 && !canAdvanceFromValues}
            onClick={() => setStep((s) => s + 1)}
          >
            {tr('下一步', 'Next')}
          </Button>
        ) : (
          <Button
            variant="primary"
            size="sm"
            type="button"
            disabled={busy}
            onClick={() => void submit()}
          >
            {busy ? <Loader2 size={12} className="animate-spin" /> : <KeyRound size={12} />}
            {busy ? tr('轮换中…', 'Rotating…') : tr('执行轮换', 'Rotate now')}
          </Button>
        )}
      </>
    );

  return (
    <Modal
      open
      onClose={onClose}
      title={tr(`轮换凭证 · ${secret.name}`, `Rotate credential · ${secret.name}`)}
      size="lg"
      footer={footer}
    >
      <div className="space-y-4 text-[12px]">
        <div className="flex flex-wrap items-center gap-1.5">
          {STEPS.map((label, index) => {
            const active = step === index;
            const finished = step > index || step === DONE_STEP;
            return (
              <span
                key={label}
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-full border px-2 py-[2px] text-[11px]',
                  active
                    ? 'border-indigo-500/40 bg-indigo-500/10 text-zinc-100'
                    : finished
                      ? 'border-ok/30 bg-ok/10 text-ok'
                      : 'border-zinc-800 bg-zinc-900/40 text-zinc-500',
                )}
              >
                <span className="tabular-nums">{finished && !active ? '✓' : index + 1}</span>
                {tr(label, ['Overview', 'New values', 'Confirm'][index])}
              </span>
            );
          })}
          {step === DONE_STEP ? (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-ok/30 bg-ok/10 px-2 py-[2px] text-[11px] text-ok">
              <CheckCircle2 size={11} aria-hidden />
              {tr('已轮换', 'Rotated')}
            </span>
          ) : null}
        </div>

        {err ? (
          <div className="rounded-lg border border-red-500/40 bg-red-500/5 px-3 py-2 text-[11px] text-red-300">
            {err}
          </div>
        ) : null}

        {step === 0 ? (
          <div className="space-y-3">
            <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1.5">
              <dt className="text-zinc-500">{tr('名称', 'Name')}</dt>
              <dd className="font-mono text-zinc-200">{secret.name}</dd>
              <dt className="text-zinc-500">{tr('类型', 'Type')}</dt>
              <dd className="text-zinc-200">{typeLabel(secret.type)}</dd>
              <dt className="text-zinc-500">{tr('字段', 'Fields')}</dt>
              <dd className="flex flex-wrap gap-1">
                {secret.field_keys.length === 0 ? (
                  <span className="text-zinc-600">—</span>
                ) : (
                  secret.field_keys.map((key) => (
                    <Chip key={key} dense className="font-mono">
                      {key}
                    </Chip>
                  ))
                )}
              </dd>
              <dt className="text-zinc-500">{tr('最近更新', 'Updated')}</dt>
              <dd className="tabular-nums text-zinc-300">{fullDateTime(secret.updated_at)}</dd>
              <dt className="text-zinc-500">{tr('加密自检', 'Decrypt check')}</dt>
              <dd>
                {health === undefined ? (
                  <span className="text-zinc-500">{tr('未检查', 'Not checked')}</span>
                ) : health.ok ? (
                  <StatusPill tone="ok" label={tr('当前密钥可解密', 'Decryptable')} />
                ) : (
                  <StatusPill tone="danger" label={tr('当前密钥无法解密', 'Undecryptable')} />
                )}
              </dd>
            </dl>

            {broken ? (
              <div className="flex items-start gap-2 rounded-lg border border-red-500/40 bg-red-500/5 px-3 py-2 text-[11px] text-red-300">
                <AlertTriangle size={13} className="mt-[1px] shrink-0" aria-hidden />
                <span>
                  {tr(
                    '这条密文与当前 CREDENTIAL_ENCRYPTION_KEY 不匹配。轮换是唯一修复路径，必须补全全部字段的新值。',
                    'This ciphertext does not match the current CREDENTIAL_ENCRYPTION_KEY. Rotation is the only repair path and requires new values for every field.',
                  )}
                </span>
              </div>
            ) : null}

            <div>
              <div className="mb-1.5 text-[11px] uppercase tracking-wide text-zinc-500">
                {tr('影响面', 'Impact')}
              </div>
              {loadingUsage ? (
                <div className="flex items-center gap-2 text-[11px] text-zinc-500">
                  <Loader2 size={12} className="animate-spin" aria-hidden />
                  {tr('加载引用…', 'Loading references…')}
                </div>
              ) : usage.length === 0 ? (
                <div className="text-[11px] text-zinc-500">
                  {tr(
                    '当前没有 MCP Server 引用这条凭证。',
                    'No MCP server references this credential yet.',
                  )}
                </div>
              ) : (
                <div className="overflow-hidden rounded-lg border border-zinc-800/60">
                  <table className="og-action-table w-full text-left text-[12px]">
                    <thead className="border-b border-zinc-800/60 bg-zinc-900/60 text-[11px] text-zinc-500">
                      <tr>
                        <th className="px-3 py-1.5 font-medium">{tr('MCP Server', 'MCP server')}</th>
                        <th className="px-3 py-1.5 font-medium">{tr('状态', 'Status')}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-zinc-800/50">
                      {usage.map((item) => (
                        <tr key={item.id}>
                          <td className="px-3 py-1.5 font-mono text-[12px] text-zinc-200">
                            {item.name}
                          </td>
                          <td className="px-3 py-1.5">
                            <StatusPill
                              tone={item.enabled ? 'ok' : 'neutral'}
                              label={item.enabled ? tr('启用', 'Enabled') : tr('已停用', 'Disabled')}
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="mt-1.5 text-[11px] leading-relaxed text-zinc-600">
                {tr(
                  '轮换后，引用的 MCP Server 在下次连接时使用新值；已在运行的会话不受影响。',
                  'After rotation, referencing MCP servers pick up the new value on their next connection; running sessions are unaffected.',
                )}
              </p>
            </div>
          </div>
        ) : null}

        {step === 1 ? (
          <div className="space-y-3">
            <p className="text-[11px] leading-relaxed text-zinc-500">
              {broken
                ? tr(
                    '旧值不可解密，以下字段全部必填。',
                    'Old values cannot be decrypted, so every field below is required.',
                  )
                : tr(
                    '只为要更换的字段填新值；留空的字段保持原值不变。旧值不会回显。',
                    'Fill only the fields you are rotating; leave the rest empty to keep the current value. Old values are never echoed.',
                  )}
            </p>
            {secret.field_keys.map((key) => (
              <Label key={key} className="block">
                <span className="mb-1 block text-[11px] text-zinc-400">
                  {fieldLabel(key)}
                  {fieldLabel(key) !== key ? (
                    <span className="ml-1 font-mono text-[10px] text-zinc-600">{key}</span>
                  ) : null}
                </span>
                <Input
                  type="password"
                  autoComplete="new-password"
                  aria-label={fieldLabel(key)}
                  value={values[key] ?? ''}
                  onChange={(e) => {
                    const next = e.target.value;
                    setValues((prev) => ({ ...prev, [key]: next }));
                  }}
                  placeholder={tr('新值（留空表示不修改）', 'New value (empty keeps current)')}
                  className="w-full font-mono"
                />
              </Label>
            ))}
            {broken && missingWhenBroken.length > 0 ? (
              <div className="text-[11px] text-amber-400">
                {tr(
                  `还需填写：${missingWhenBroken.join('、')}`,
                  `Still required: ${missingWhenBroken.join(', ')}`,
                )}
              </div>
            ) : null}
          </div>
        ) : null}

        {step === 2 ? (
          <div className="space-y-3">
            <p className="text-[11px] leading-relaxed text-zinc-400">
              {tr(
                '将写入以下字段的新值，随后立即生效并写入审计日志。',
                'The new values below will be written, take effect immediately, and be audited.',
              )}
            </p>
            <div className="flex flex-wrap gap-1">
              {filled.map(([key]) => (
                <Chip key={key} dense className="font-mono">
                  {key}
                </Chip>
              ))}
            </div>
            <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1.5">
              <dt className="text-zinc-500">{tr('未改动字段', 'Unchanged')}</dt>
              <dd className="flex flex-wrap gap-1">
                {secret.field_keys.filter((key) => (values[key] ?? '') === '').length === 0 ? (
                  <span className="text-zinc-600">—</span>
                ) : (
                  secret.field_keys
                    .filter((key) => (values[key] ?? '') === '')
                    .map((key) => (
                      <Chip key={key} dense className="font-mono">
                        {key}
                      </Chip>
                    ))
                )}
              </dd>
              <dt className="text-zinc-500">{tr('影响 MCP', 'MCP impact')}</dt>
              <dd className="text-zinc-300">
                {tr(`${usage.length} 个 Server`, `${usage.length} server(s)`)}
              </dd>
            </dl>
            <CodeBlock>{tr(
              '旧值不可恢复；如需回退只能在对方平台重新签发后再轮换一次。',
              'Old values are unrecoverable; to roll back, re-issue on the provider side and rotate again.',
            )}</CodeBlock>
          </div>
        ) : null}

        {step === DONE_STEP && rotated ? (
          <div className="space-y-3">
            <div className="flex items-start gap-2 rounded-lg border border-ok/30 bg-ok/5 px-3 py-2 text-[11px] text-ok">
              <CheckCircle2 size={13} className="mt-[1px] shrink-0" aria-hidden />
              <span>
                {tr(
                  `已轮换 ${filled.length} 个字段，密文已用当前加密密钥重写。`,
                  `Rotated ${filled.length} field(s); ciphertext rewritten with the current encryption key.`,
                )}
              </span>
            </div>
            <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1.5">
              <dt className="text-zinc-500">{tr('轮换时间', 'Rotated at')}</dt>
              <dd className="tabular-nums text-zinc-200">{fullDateTime(rotated.updated_at)}</dd>
              <dt className="text-zinc-500">{tr('字段', 'Fields')}</dt>
              <dd className="flex flex-wrap gap-1">
                {rotated.field_keys.map((key) => (
                  <Chip key={key} dense className="font-mono">
                    {key}
                  </Chip>
                ))}
              </dd>
            </dl>

            {verifyItems !== null ? (
              <div className="overflow-hidden rounded-lg border border-zinc-800/60">
                <table className="og-action-table w-full text-left text-[12px]">
                  <thead className="border-b border-zinc-800/60 bg-zinc-900/60 text-[11px] text-zinc-500">
                    <tr>
                      <th className="px-3 py-1.5 font-medium">{tr('MCP Server', 'MCP server')}</th>
                      <th className="px-3 py-1.5 font-medium">{tr('探测结果', 'Probe result')}</th>
                      <th className="px-3 py-1.5 font-medium">{tr('工具数', 'Tools')}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/50">
                    {verifyItems.length === 0 ? (
                      <tr>
                        <td colSpan={3} className="px-3 py-2 text-[11px] text-zinc-500">
                          {tr('没有需要验证的引用。', 'No references to verify.')}
                        </td>
                      </tr>
                    ) : (
                      verifyItems.map((item) => (
                        <tr key={item.id}>
                          <td className="px-3 py-1.5 font-mono text-[12px] text-zinc-200">
                            {item.name}
                          </td>
                          <td className="px-3 py-1.5">
                            {item.skipped ? (
                              <span className="text-[11px] text-zinc-500">{item.error}</span>
                            ) : item.ok ? (
                              <StatusPill tone="ok" label={tr('连接成功', 'Connected')} />
                            ) : (
                              <span className="text-[11px] text-red-300" title={item.error ?? ''}>
                                {item.error}
                              </span>
                            )}
                          </td>
                          <td className="px-3 py-1.5 tabular-nums text-zinc-400">
                            {item.skipped ? '—' : item.tool_count}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
