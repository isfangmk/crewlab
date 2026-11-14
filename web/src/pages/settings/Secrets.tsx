import { useAssistantPage } from '@/lib/assistantPage';
import { Button, Chip, EmptyState, ErrorBanner, Input, Label, RowAction, RowActionIcon, SectionCard, StatusPill, SuccessBanner, TableCard, TableHead } from '@/components/ui';
import { Hint } from '@/components/ui/Tooltip';
import { Select } from '@/components/ui/Select';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Download, KeyRound, Lock, Plus, Trash2, RefreshCw, X } from 'lucide-react';
import {
  listSecrets,
  listCredentialTypes,
  createSecret,
  deleteSecret,
  exportSecrets,
  getSecretsHealth,
  type SecretHealthItem,
  type SecretView,
  type CredType,
} from '@/api/secrets';
import { SecretRotateDialog } from '@/components/SecretRotateDialog';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { saveBlob } from '@/lib/download';
import { fullDateTime } from '@/lib/format';
import { useI18n } from '@/i18n/locale';
import { errorText } from '@/lib/errors';

// 凭据管理：按类型渲染字段；custom 为自由键值。明文只写不读，落库加密。

type FieldRow = { key: string; value: string };
const CUSTOM = 'custom';

export default function SecretsPage() {
  const { tr } = useI18n();
  const [items, setItems] = useState<SecretView[]>([]);
  const [types, setTypes] = useState<CredType[]>([]);
  const [health, setHealth] = useState<Record<number, SecretHealthItem>>({});
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [rotating, setRotating] = useState<SecretView | null>(null);
  const [deleting, setDeleting] = useState<SecretView | null>(null);

  // add-form state
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [typeName, setTypeName] = useState(CUSTOM);
  const [typedVals, setTypedVals] = useState<Record<string, string>>({}); // for typed creds
  const [rows, setRows] = useState<FieldRow[]>([{ key: '', value: '' }]); // for custom

  const selectedType = useMemo(() => types.find((t) => t.name === typeName), [types, typeName]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, t, h] = await Promise.all([
        listSecrets(),
        listCredentialTypes(),
        getSecretsHealth(),
      ]);
      setItems(s.items ?? []);
      setTypes(t.items ?? []);
      setHealth(Object.fromEntries((h.items ?? []).map((i) => [i.id, i])));
      setErr(null);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const setRow = (i: number, patch: Partial<FieldRow>) =>
    setRows((rs) => rs.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  const addRow = () => setRows((rs) => [...rs, { key: '', value: '' }]);
  const removeRow = (i: number) => setRows((rs) => (rs.length > 1 ? rs.filter((_, idx) => idx !== i) : rs));

  const resetForm = () => {
    setName('');
    setDesc('');
    setTypedVals({});
    setRows([{ key: '', value: '' }]);
  };

  const onAdd = async () => {
    const fields: Record<string, string> = {};
    if (typeName !== CUSTOM && selectedType) {
      for (const f of selectedType.fields) {
        const v = typedVals[f.key];
        if (v) fields[f.key] = v;
      }
    } else {
      for (const r of rows) {
        const k = r.key.trim();
        if (k && r.value) fields[k] = r.value;
      }
    }
    if (!name.trim() || Object.keys(fields).length === 0) return;
    setBusy(true);
    setErr(null);
    try {
      await createSecret({ name: name.trim(), type: typeName, description: desc.trim(), fields });
      resetForm();
      await load();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const runDelete = async () => {
    if (deleting === null) throw new Error(tr('没有待删除的凭据', 'No credential selected'));
    await deleteSecret(deleting.id);
    await load();
  };

  const onExport = async () => {
    setExporting(true);
    try {
      const { blob, filename } = await exportSecrets();
      saveBlob(blob, filename);
      setErr(null);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setExporting(false);
    }
  };

  const onRotated = (updated: SecretView) => {
    setItems((rows) => rows.map((row) => (row.id === updated.id ? updated : row)));
    setNote(
      tr(
        `凭证 ${updated.name} 已轮换，新值立即生效并已写入审计日志。`,
        `Credential ${updated.name} rotated; the new value is live and audited.`,
      ),
    );
    // 轮换会改 updated_at 与密文：重查健康状态，避免页面停留在旧的「不可解密」提示。
    void getSecretsHealth()
      .then((h) => setHealth(Object.fromEntries((h.items ?? []).map((i) => [i.id, i]))))
      .catch((e) => setErr(errorText(e)));
  };

  const brokenSecrets = items.filter((item) => health[item.id] !== undefined && !health[item.id].ok);

  const isCustom = typeName === CUSTOM;

  useAssistantPage('凭据管理', 'Credentials', {
    credential_count: items.length,
    credential_type_count: types.length,
    loading,
  });

  return (
    <div className="anim-fade space-y-4">
      <SectionCard
        title={<span className="inline-flex items-center gap-2"><Lock size={13} className="text-zinc-400" />{tr('凭据库', 'Credential vault')}</span>}
        description={tr(
          '选一个类型（腾讯云 / AWS / GitHub …）会自动列出该填的字段，并自带“注入到哪些环境变量”的规则；技能 / 外部 MCP 用上这份凭据时按类型规则注入。类型选“自定义”则自由填字段，按同名环境变量注入。字段值只写不读，使用现有系统密钥进行 AES 加密后落库。',
          'Pick a type (Tencent Cloud / AWS / GitHub …) and it lists the right fields plus a built-in "which env vars to inject" rule; skills / external MCP that use this credential inject by that rule. Pick "Custom" to enter free-form fields injected as same-named env vars. Values are write-only and AES-encrypted at rest with the existing system secret.',
        )}
        actions={
          <div className="flex flex-wrap items-center gap-1.5">
            <Hint content={tr('导出凭证清单（仅元数据，不含密钥）', 'Export credential inventory (metadata only)')}>
              <RowAction
                icon={Download}
                iconClassName={exporting ? 'animate-pulse' : undefined}
                disabled={loading || exporting || items.length === 0}
                onClick={() => void onExport()}
              >
                {tr('导出清单', 'Export list')}
              </RowAction>
            </Hint>
            <Hint content={tr('刷新', 'Refresh')}>
              <RowAction icon={RefreshCw} onClick={() => void load()} disabled={loading}>
                {tr('刷新', 'Refresh')}
              </RowAction>
            </Hint>
          </div>
        }
      />

      {err && <ErrorBanner>{err}</ErrorBanner>}

      {note && <SuccessBanner>{note}</SuccessBanner>}

      {brokenSecrets.length > 0 && (
        <ErrorBanner>
          {tr(
            `${brokenSecrets.length} 条凭证无法用当前加密密钥解密（${brokenSecrets
              .map((s) => s.name)
              .join('、')}）。轮换并补全全部字段即可修复。`,
            `${brokenSecrets.length} credential(s) cannot be decrypted with the current key (${brokenSecrets
              .map((s) => s.name)
              .join(', ')}). Rotate and fill every field to repair.`,
          )}
        </ErrorBanner>
      )}

      <SectionCard
        title={tr('新增凭据', 'Add credential')}
        footer={
          <Button variant="primary" size="sm"
            type="button"
            onClick={() => void onAdd()}
            disabled={busy || !name.trim()}
          >
            <Plus size={12} />
            {tr('保存凭据', 'Save credential')}
          </Button>
        }
      >
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={tr('名称（如 tencent-prod）', 'Name (e.g. tencent-prod)')}
            className="font-mono"
          />
          <Select label={tr('凭证类型', 'Credential type')}
            value={typeName}
            onValueChange={(selectedValue) => {
              setTypeName(selectedValue);
              setTypedVals({});
            }}

          >
            {types.map((t) => (
              <option key={t.name} value={t.name}>
                {t.label}
              </option>
            ))}
          </Select>
          <Input
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            placeholder={tr('备注（可选）', 'Description (optional)')}
          />
        </div>

        {/* typed fields */}
        {!isCustom && selectedType && (
          <div className="mt-3 space-y-2">
            {selectedType.fields.map((f) => (
              <div key={f.key} className="flex items-center gap-2">
                <Label className="w-40 shrink-0 text-[11px] text-zinc-400">{f.label}</Label>
                <Input
                  value={typedVals[f.key] ?? ''}
                  onChange={(e) => setTypedVals((v) => ({ ...v, [f.key]: e.target.value }))}
                  type={f.secret ? 'password' : 'text'}
                  autoComplete="new-password"
                  placeholder={f.key}
                  className="flex-1"
                />
              </div>
            ))}
            {selectedType.inject_env && Object.keys(selectedType.inject_env).length > 0 && (
              <div className="pt-1 text-[10px] leading-relaxed text-zinc-600">
                {tr('将注入为环境变量：', 'Injects as env vars: ')}
                <span className="font-mono text-zinc-500">{Object.keys(selectedType.inject_env).join(', ')}</span>
              </div>
            )}
          </div>
        )}

        {/* custom free-form fields */}
        {isCustom && (
          <div className="mt-3 space-y-2">
            <div className="text-[11px] text-zinc-500">{tr('字段（键 = 环境变量名 / 值）', 'Fields (key = env var name / value)')}</div>
            {rows.map((row, i) => (
              <div key={i} className="flex items-center gap-2">
                <Input
                  value={row.key}
                  onChange={(e) => setRow(i, { key: e.target.value })}
                  placeholder={tr('字段名（如 GITHUB_TOKEN）', 'field key (e.g. GITHUB_TOKEN)')}
                  className="w-48 font-mono"
                />
                <Input
                  value={row.value}
                  onChange={(e) => setRow(i, { value: e.target.value })}
                  type="password"
                  autoComplete="new-password"
                  placeholder={tr('值', 'value')}
                  className="flex-1"
                />
                <Hint content={tr('删除字段', 'Remove field')}>
                  <RowActionIcon
                    icon={X}
                    label={tr('删除字段', 'Remove field')}
                    onClick={() => removeRow(i)}
                  />
                </Hint>
              </div>
            ))}
            <Button variant="subtle" size="sm" type="button" onClick={addRow} className="gap-1 text-indigo-400">
              <Plus size={12} />
              {tr('加字段', 'Add field')}
            </Button>
          </div>
        )}
      </SectionCard>

      <TableCard>
        <table className="og-action-table w-full text-left">
          <TableHead>
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">{tr('名称', 'Name')}</th>
              <th scope="col" className="px-3 py-2 font-medium">{tr('类型', 'Type')}</th>
              <th scope="col" className="px-3 py-2 font-medium">{tr('字段', 'Fields')}</th>
              <th scope="col" className="px-3 py-2 font-medium">{tr('最近更新', 'Updated')}</th>
              <th scope="col" className="px-3 py-2" />
            </tr>
          </TableHead>
          <tbody className="divide-y divide-zinc-800/50">
            {loading ? (
              <tr>
                <td colSpan={5} className="px-3 py-6 text-center text-[11px] text-zinc-500">
                  {tr('加载中…', 'Loading…')}
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td colSpan={5}>
                  <EmptyState
                    icon={KeyRound}
                    title={tr('还没有凭据', 'No credentials yet')}
                    hint={tr('在上方选择类型并保存第一个凭据。', 'Pick a type above and save your first credential.')}
                    className="flex h-40 flex-col items-center justify-center gap-2 text-center"
                  />
                </td>
              </tr>
            ) : (
              items.map((s) => {
                const healthItem = health[s.id];
                return (
                  <tr key={s.id} className="transition-colors hover:bg-zinc-800/30">
                    <td className="px-3 py-2 font-mono text-[12.5px] text-zinc-100">
                      <span className="inline-flex items-center gap-1.5">
                        {s.name}
                        {healthItem !== undefined && !healthItem.ok ? (
                          <StatusPill tone="danger" label={tr('不可解密', 'Undecryptable')} />
                        ) : null}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <Chip>{types.find((t) => t.name === s.type)?.label ?? s.type ?? CUSTOM}</Chip>
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex flex-wrap gap-1">
                        {s.field_keys.length === 0 ? (
                          <span className="text-[11px] text-zinc-600">—</span>
                        ) : (
                          s.field_keys.map((k) => (
                            <Chip key={k} dense className="font-mono">{k}</Chip>
                          ))
                        )}
                      </div>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-[11px] tabular-nums text-zinc-500">
                      {fullDateTime(s.updated_at)}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <div className="flex items-center justify-end gap-1.5">
                        <RowAction
                          icon={KeyRound}
                          disabled={busy}
                          onClick={() => {
                            setNote(null);
                            setRotating(s);
                          }}
                        >
                          {tr('轮换', 'Rotate')}
                        </RowAction>
                        <Hint content={tr('删除', 'Delete')}>
                          <RowActionIcon
                            icon={Trash2}
                            label={tr('删除', 'Delete')}
                            tone="danger"
                            disabled={busy}
                            onClick={() => setDeleting(s)}
                          />
                        </Hint>
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </TableCard>

      <SecretRotateDialog
        secret={rotating}
        types={types}
        health={rotating === null ? undefined : health[rotating.id]}
        onClose={() => setRotating(null)}
        onRotated={onRotated}
      />

      <ConfirmDialog
        open={deleting !== null}
        title={deleting ? tr(`删除 ${deleting.name}?`, `Delete ${deleting.name}?`) : ''}
        description={tr(
          '删除后使用该凭据的服务会立即失效，且无法恢复。',
          'Services using this credential stop working immediately; this cannot be undone.',
        )}
        onConfirm={runDelete}
        onClose={() => setDeleting(null)}
      />
    </div>
  );
}
