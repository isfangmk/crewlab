// 对外发布：把助理发布为 OpenAI 兼容 API；每渠道一把 Key、一个持久会话。
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Check,
  Copy,
  Globe,
  KeyRound,
  Loader2,
  Plus,
  Power,
  RefreshCw,
  Trash2,
} from 'lucide-react';
import {
  Button,
  CodeBlock,
  EmptyStateCard,
  ErrorBanner,
  FilterBar,
  Input,
  Label,
  PageHeader,
  RowAction,
  RowActionIcon,
  SearchInput,
  Select,
  StatusPill,
  TableCard,
  TableHead,
  TablePager,
  Toast,
  type ToastTone,
  COL,
  tableLayout,
} from '@/components/ui';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { Modal } from '@/components/Modal';
import { Hint } from '@/components/ui/Tooltip';
import { errorText } from '@/lib/errors';
import { fullDateTime } from '@/lib/format';
import { useTimeout } from '@/lib/useTimeout';
import { useI18n } from '@/i18n/locale';
import { usePermissions } from '@/store/me';
import { listAgents, type AgentSummary } from '@/api/agents';
import { listModels, type ModelCatalog } from '@/api/chat';
import {
  createChannel,
  deleteChannel,
  listChannels,
  rotateChannelKey,
  setChannelEnabled,
  updateChannel,
  type ChannelUpdateInput,
  type PublishedChannel,
} from '@/api/publish';

const DEFAULT_PAGE_SIZE = 20;
const PAGE_SIZE_OPTIONS = [20, 50, 100];

function isExpired(channel: PublishedChannel): boolean {
  return (
    channel.expires_at !== null && new Date(channel.expires_at).getTime() < Date.now()
  );
}

export default function PublishPage() {
  const { tr } = useI18n();
  const { isAdmin } = usePermissions();
  const [items, setItems] = useState<PublishedChannel[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [editor, setEditor] = useState<null | { channel: PublishedChannel | null }>(null);
  const [revealed, setRevealed] = useState<{ name: string; key: string } | null>(null);
  const [rotating, setRotating] = useState<PublishedChannel | null>(null);
  const [deleting, setDeleting] = useState<PublishedChannel | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [toast, setToast] = useState<{ tone: ToastTone; text: string } | null>(null);

  const flashTimer = useTimeout();
  const flash = useCallback(
    (tone: ToastTone, text: string) => {
      setToast({ tone, text });
      flashTimer(() => setToast(null), 4000);
    },
    [flashTimer],
  );

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const r = await listChannels();
      setItems(r.items);
      setErr(null);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isAdmin) void load();
    else setLoading(false);
  }, [isAdmin, load]);

  const filtered = useMemo(() => {
    const rows = items ?? [];
    const needle = query.trim().toLowerCase();
    if (needle === '') return rows;
    return rows.filter((row) =>
      [row.name, row.agent_id, row.model].some((field) =>
        field.toLowerCase().includes(needle),
      ),
    );
  }, [items, query]);
  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, pageCount - 1);
  const paged = filtered.slice(safePage * pageSize, (safePage + 1) * pageSize);

  const onToggle = async (channel: PublishedChannel) => {
    setBusyId(channel.id);
    const nextEnabled = !channel.enabled;
    try {
      await setChannelEnabled(channel.id, nextEnabled);
      await load(true);
      flash('ok', nextEnabled ? tr('渠道已启用', 'Channel enabled') : tr('渠道已停用', 'Channel disabled'));
    } catch (e) {
      flash('err', errorText(e));
    } finally {
      setBusyId(null);
    }
  };

  if (!isAdmin) {
    return (
      <main className="anim-fade flex flex-1 flex-col overflow-hidden">
        <PageHeader
          title={tr('发布管理', 'Publishing')}
          subtitle={tr(
            '把助理发布为 OpenAI 兼容 API',
            'Publish assistants as an OpenAI-compatible API',
          )}
        />
        <div className="px-6 py-6">
          <EmptyStateCard
            icon={Globe}
            title={tr('仅管理员可管理发布渠道', 'Only admins can manage channels')}
            hint={tr(
              '发布渠道会签发可直接调用模型的 Key，需要 admin 角色。',
              'Publishing issues keys that can call models directly; admin role required.',
            )}
          />
        </div>
      </main>
    );
  }

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tr('发布管理', 'Publishing')}
        subtitle={tr(
          '每个渠道一把 API Key、一个持久会话；外部走 POST /v1/chat/completions（OpenAI 兼容）',
          'Each channel has one API key and one persistent session; call POST /v1/chat/completions (OpenAI compatible)',
        )}
        actions={
          <>
            <RowAction icon={RefreshCw} onClick={() => void load()}>
              {tr('刷新', 'Refresh')}
            </RowAction>
            <Button variant="primary" size="sm" onClick={() => setEditor({ channel: null })}>
              <Plus size={12} />
              {tr('新建渠道', 'New channel')}
            </Button>
          </>
        }
      />

      <FilterBar className="mx-6 mt-3">
        <SearchInput
          value={query}
          onChange={(v) => {
            setQuery(v);
            setPage(0);
          }}
          placeholder={tr('按名称 / 助理 / 模型搜索…', 'Search name, assistant or model…')}
          ariaLabel={tr('搜索发布渠道', 'Search published channels')}
        />
        <span className="ml-auto text-[11px] tabular-nums text-zinc-500">
          {tr(`共 ${filtered.length} 条`, `${filtered.length} total`)}
        </span>
      </FilterBar>

      <div className="flex min-h-0 flex-1 flex-col px-6 py-4">
        {err && (
          <ErrorBanner className="mb-3">{err}</ErrorBanner>
        )}
        {loading && items === null ? (
          <div className="flex flex-1 items-center justify-center text-[11px] text-zinc-500">
            <Loader2 size={12} className="mr-2 animate-spin" />
            {tr('加载中…', 'Loading…')}
          </div>
        ) : err !== null && items !== null && items.length === 0 ? null : items !== null && items.length === 0 ? (
          <EmptyStateCard
            icon={Globe}
            title={tr('还没有发布渠道', 'No published channels yet')}
            hint={tr(
              '创建渠道后，外部系统用签发的 Key 即可调用该助理。',
              'After creating a channel, external systems can call the assistant with the issued key.',
            )}
            action={
              <Button
                variant="primary"
                size="sm"
                onClick={() => setEditor({ channel: null })}
              >
                <Plus size={12} />
                {tr('新建渠道', 'New channel')}
              </Button>
            }
          />
        ) : filtered.length === 0 ? (
          <EmptyStateCard
            icon={Globe}
            title={tr('没有匹配的渠道', 'No matching channels')}
            action={
              <Button size="sm" onClick={() => setQuery('')}>
                {tr('清除筛选', 'Clear filters')}
              </Button>
            }
          />
        ) : (
          <TableCard
            className="min-h-0 flex-1"
            footer={
              <TablePager
                page={safePage}
                pageSize={pageSize}
                total={filtered.length}
                shown={paged.length}
                onPageChange={setPage}
                pageSizeOptions={PAGE_SIZE_OPTIONS}
                onPageSizeChange={(size) => {
                  setPage(0);
                  setPageSize(size);
                }}
              />
            }
          >
            <table {...tableLayout(62)}>
              <colgroup>
                <col className={COL.main} />
                <col className={COL.detail} />
                <col className={COL.tag} />
                <col className={COL.tag} />
                <col className={COL.tag} />
                <col className={COL.time} />
                <col className={COL.actionsIconWide} />
              </colgroup>
              <TableHead>
                <tr className="whitespace-nowrap">
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('名称', 'Name')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('助理 / 模型', 'Assistant / model')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('API Key', 'API key')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('用量', 'Usage')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('状态', 'Status')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('最近调用', 'Last call')}</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">{tr('操作', 'Actions')}</th>
                </tr>
              </TableHead>
              <tbody className="divide-y divide-zinc-800/40">
                {paged.map((channel) => {
                  const expired = isExpired(channel);
                  return (
                    <tr key={channel.id} className="group transition-colors hover:bg-zinc-900/40">
                      <td className="px-3 py-2">
                        <button
                          type="button"
                          onClick={() => setEditor({ channel })}
                          className="block max-w-full truncate text-left text-[12.5px] font-medium text-zinc-100 transition-colors hover:text-indigo-300"
                          title={tr(`查看并编辑 ${channel.name}`, `View and edit ${channel.name}`)}
                        >
                          {channel.name}
                        </button>
                        <span className="block truncate text-[10px] leading-4 text-zinc-500" title={channel.expires_at ? fullDateTime(channel.expires_at) : tr('永不过期', 'Never expires')}>
                          {channel.expires_at
                            ? tr(
                                `有效期至 ${fullDateTime(channel.expires_at)}`,
                                `Expires ${fullDateTime(channel.expires_at)}`,
                              )
                            : tr('永不过期', 'Never expires')}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <span className="block truncate font-sans text-[11px] text-zinc-300" title={channel.agent_id}>
                          {channel.agent_id}
                        </span>
                        <span className="block truncate text-[10px] text-zinc-500">
                          {channel.provider} · {channel.model}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <span className="font-sans text-[11px] text-zinc-400">
                          {channel.key_masked}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <span className="block text-[11px] tabular-nums text-zinc-300">
                          {tr(
                            `${channel.usage.turns} 回合`,
                            `${channel.usage.turns} turns`,
                          )}
                        </span>
                        <span className="block text-[10px] tabular-nums text-zinc-500">
                          {channel.usage.total_tokens.toLocaleString()} tokens
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        {expired ? (
                          <StatusPill tone="neutral" label={tr('已过期', 'Expired')} />
                        ) : channel.enabled ? (
                          <StatusPill tone="ok" label={tr('启用中', 'Enabled')} />
                        ) : (
                          <StatusPill tone="neutral" label={tr('已停用', 'Disabled')} />
                        )}
                      </td>
                      <td className="px-3 py-2 text-[11px] text-zinc-500">
                        {channel.usage.last_used_at
                          ? fullDateTime(channel.usage.last_used_at)
                          : '—'}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <div className="flex items-center justify-end gap-0.5 opacity-60 transition-opacity group-hover:opacity-100">
                          <Hint content={tr('轮换 Key（旧 Key 立即失效）', 'Rotate key (old key stops working)')}>
                            <RowActionIcon
                              icon={KeyRound}
                              label={tr('轮换 Key', 'Rotate key')}
                              onClick={() => setRotating(channel)}
                            />
                          </Hint>
                          <Hint content={channel.enabled ? tr('停用', 'Disable') : tr('启用', 'Enable')}>
                            <RowActionIcon
                              icon={busyId === channel.id ? Loader2 : Power}
                              label={channel.enabled ? tr('停用', 'Disable') : tr('启用', 'Enable')}
                              disabled={busyId === channel.id}
                              onClick={() => void onToggle(channel)}
                            />
                          </Hint>
                          <Hint content={tr('删除', 'Delete')}>
                            <RowActionIcon
                              icon={Trash2}
                              label={tr('删除', 'Delete')}
                              tone="danger"
                              onClick={() => setDeleting(channel)}
                            />
                          </Hint>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableCard>
        )}
      </div>

      {editor !== null && (
        <ChannelEditor
          channel={editor.channel}
          onClose={() => setEditor(null)}
          onSaved={(channel, key) => {
            setEditor(null);
            void load(true);
            if (key !== null) {
              setRevealed({ name: channel.name, key });
              flash('ok', tr('渠道已创建', 'Channel created'));
            } else {
              flash('ok', tr('渠道已更新', 'Channel updated'));
            }
          }}
        />
      )}

      {revealed && (
        <KeyRevealModal
          name={revealed.name}
          apiKey={revealed.key}
          onCopied={() => flash('ok', tr('Key 已复制', 'Key copied'))}
          onClose={() => setRevealed(null)}
        />
      )}
      {toast !== null && <Toast tone={toast.tone} text={toast.text} />}

      {rotating && (
        <ConfirmDialog
          open
          title={tr(`轮换 ${rotating.name} 的 Key`, `Rotate key for ${rotating.name}`)}
          description={tr(
            '旧 Key 会立即失效；正在使用旧 Key 的外部系统需要更新配置。',
            'The old key stops working immediately; external systems must be updated.',
          )}
          confirmLabel={tr('轮换', 'Rotate')}
          onClose={() => setRotating(null)}
          onConfirm={async () => {
            const result = await rotateChannelKey(rotating.id);
            setRotating(null);
            setRevealed({ name: rotating.name, key: result.key });
            await load(true);
            flash('ok', tr('Key 已轮换', 'Key rotated'));
          }}
        />
      )}

      {deleting && (
        <ConfirmDialog
          open
          title={tr(`删除渠道 ${deleting.name}`, `Delete channel ${deleting.name}`)}
          description={tr(
            'Key 立即失效；渠道复用的会话会保留在会话列表里。',
            'The key stops working immediately; the reused session stays in the session list.',
          )}
          confirmLabel={tr('删除', 'Delete')}
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            await deleteChannel(deleting.id);
            setDeleting(null);
            await load(true);
            flash('ok', tr('渠道已删除', 'Channel deleted'));
          }}
        />
      )}
    </main>
  );
}

function ChannelEditor({
  channel,
  onClose,
  onSaved,
}: {
  channel: PublishedChannel | null;
  onClose(): void;
  onSaved(channel: PublishedChannel, key: string | null): void;
}) {
  const { tr } = useI18n();
  const editing = channel !== null;
  const [name, setName] = useState(channel?.name ?? '');
  const [agentId, setAgentId] = useState(channel?.agent_id ?? 'default');
  const [modelValue, setModelValue] = useState(
    channel === null ? '' : `${channel.provider}::${channel.model}`,
  );
  // 编辑时默认不动有效期；选中预设才从当前时间重新计算
  const [expiresHours, setExpiresHours] = useState(editing ? '' : '0');
  const [agents, setAgents] = useState<AgentSummary[] | null>(null);
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([listAgents(), listModels()])
      .then(([agentList, modelCatalog]) => {
        if (cancelled) return;
        setAgents(agentList.items);
        setCatalog(modelCatalog);
      })
      .catch((e: Error) => {
        if (!cancelled) setErr(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const agentOptions = useMemo(() => {
    const rows = agents ?? [];
    return rows.map((agent) => ({
      value: agent.name,
      label: agent.display_name && agent.display_name !== agent.name
        ? `${agent.display_name}（${agent.name}） — ${agent.description}`
        : `${agent.name} — ${agent.description}`,
    }));
  }, [agents]);

  const modelOptions = useMemo(() => {
    const options = [
      { value: '', label: tr('跟随全局默认模型', 'Follow global default') },
    ];
    for (const provider of catalog?.providers ?? []) {
      for (const model of provider.models) {
        options.push({
          value: `${provider.id}::${model}`,
          label: `${provider.label} · ${model}`,
        });
      }
    }
    // 渠道当前模型可能已从目录里下架：保留一个「当前」选项，避免显示裸值
    if (
      channel !== null &&
      modelValue !== '' &&
      !options.some((option) => option.value === modelValue)
    ) {
      options.splice(1, 0, {
        value: modelValue,
        label: `${channel.provider} · ${channel.model}`,
      });
    }
    return options;
  }, [catalog, tr, channel, modelValue]);

  const save = async () => {
    setSaving(true);
    setErr(null);
    try {
      const [provider, model] =
        modelValue === '' ? ['', ''] : modelValue.split('::', 2);
      if (channel !== null) {
        const patch: ChannelUpdateInput = {
          name: name.trim(),
          agent_id: agentId,
          provider,
          model,
        };
        if (expiresHours !== '') {
          patch.expires_in_hours = Number(expiresHours);
        }
        const updated = await updateChannel(channel.id, patch);
        onSaved(updated, null);
      } else {
        const created = await createChannel({
          name: name.trim(),
          agent_id: agentId,
          provider,
          model,
          expires_in_hours: Number(expiresHours),
        });
        onSaved(created, created.key);
      }
    } catch (e) {
      setErr(errorText(e));
      setSaving(false);
    }
  };

  const expired = channel !== null && isExpired(channel);

  return (
    <Modal
      open
      onClose={onClose}
      title={editing ? tr('渠道详情', 'Channel details') : tr('新建发布渠道', 'New published channel')}
      size="md"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose}>
            {tr('取消', 'Cancel')}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => void save()}
            disabled={saving || name.trim() === '' || agentId === ''}
          >
            {saving
              ? tr('保存中…', 'Saving…')
              : editing
                ? tr('保存修改', 'Save changes')
                : tr('创建并签发 Key', 'Create & issue key')}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {err && <ErrorBanner>{err}</ErrorBanner>}
        {channel !== null && (
          <div className="space-y-1 rounded-lg border border-zinc-800/60 bg-zinc-950/60 px-3 py-2">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-zinc-400">
              <span className="font-sans text-zinc-300">{channel.key_masked}</span>
              <span className="tabular-nums">
                {tr(`${channel.usage.turns} 回合`, `${channel.usage.turns} turns`)}
              </span>
              <span className="tabular-nums">
                {channel.usage.total_tokens.toLocaleString()} tokens
              </span>
              {expired ? (
                <StatusPill tone="neutral" label={tr('已过期', 'Expired')} />
              ) : channel.enabled ? (
                <StatusPill tone="ok" label={tr('启用中', 'Enabled')} />
              ) : (
                <StatusPill tone="neutral" label={tr('已停用', 'Disabled')} />
              )}
            </div>
            <div className="text-[10px] text-zinc-500">
              {tr('最近调用', 'Last call')}：
              {channel.usage.last_used_at ? fullDateTime(channel.usage.last_used_at) : '—'}
              {' · '}
              {tr('创建于', 'Created')}{' '}
              {channel.created_at ? fullDateTime(channel.created_at) : '—'}
            </div>
          </div>
        )}
        <div className="space-y-1.5">
          <Label htmlFor="channel-name">{tr('渠道名称', 'Channel name')}</Label>
          <Input
            id="channel-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={tr('例如 运维问答对外版', 'e.g. public ops assistant')}
            maxLength={128}
            className="w-full"
            autoFocus={!editing}
          />
          <p className="text-[11px] text-zinc-500">
            {tr(
              '同时用于用量记账，需唯一；改名会同步历史用量的渠道归属。',
              'Also used for usage accounting and must be unique; renaming moves past usage too.',
            )}
          </p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="channel-agent">{tr('发布助理', 'Assistant')}</Label>
          <Select
            id="channel-agent"
            value={agentId}
            onValueChange={setAgentId}
            options={agentOptions}
            disabled={agents === null}
            aria-label={tr('发布助理', 'Assistant')}
            className="w-full"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="channel-model">{tr('模型', 'Model')}</Label>
          <Select
            id="channel-model"
            value={modelValue}
            onValueChange={setModelValue}
            options={modelOptions}
            disabled={catalog === null}
            aria-label={tr('模型', 'Model')}
            className="w-full"
          />
          {editing && (
            <p className="text-[11px] text-zinc-500">
              {tr(
                '修改模型会为渠道重建会话，历史会话保留在会话列表。',
                'Changing the model rebuilds the channel session; the old session stays listed.',
              )}
            </p>
          )}
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="channel-expiry">{tr('有效期', 'Expiry')}</Label>
          <Select
            id="channel-expiry"
            value={expiresHours}
            onValueChange={setExpiresHours}
            options={[
              ...(editing
                ? [{ value: '', label: tr('保持不变', 'Keep current') }]
                : []),
              { value: '0', label: tr('永不过期', 'Never expires') },
              { value: '24', label: tr('24 小时', '24 hours') },
              { value: '168', label: tr('7 天', '7 days') },
              { value: '720', label: tr('30 天', '30 days') },
            ]}
            aria-label={tr('有效期', 'Expiry')}
            className="w-full"
          />
          {editing && (
            <p className="text-[11px] text-zinc-500">
              {tr(
                `当前：${
                  channel.expires_at ? fullDateTime(channel.expires_at) : tr('永不过期', 'never')
                }；修改后从当前时间重新计算。`,
                `Current: ${
                  channel.expires_at ? fullDateTime(channel.expires_at) : 'never'
                }; changes count from now.`,
              )}
            </p>
          )}
        </div>
      </div>
    </Modal>
  );
}

function KeyRevealModal({
  name,
  apiKey,
  onCopied,
  onClose,
}: {
  name: string;
  apiKey: string;
  onCopied(): void;
  onClose(): void;
}) {
  const { tr } = useI18n();
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    await navigator.clipboard.writeText(apiKey);
    setCopied(true);
    onCopied();
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={tr(`渠道 ${name} 的 API Key`, `API key for ${name}`)}
      size="md"
      footer={
        <Button variant="primary" size="sm" onClick={onClose}>
          {tr('我已保存', 'I saved it')}
        </Button>
      }
    >
      <div className="space-y-3 text-xs text-zinc-300">
        <p className="rounded-lg border border-warn/30 bg-warn/10 px-3 py-2 text-[12px] leading-relaxed text-warn">
          {tr(
            'Key 只显示这一次，关闭后无法再次查看；丢失请重新轮换。',
            'This key is shown only once and cannot be viewed again; rotate if lost.',
          )}
        </p>
        <CodeBlock maxHeightClassName="max-h-none">{apiKey}</CodeBlock>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => void copy()}>
            {copied ? <Check size={12} /> : <Copy size={12} />}
            {copied ? tr('已复制', 'Copied') : tr('复制 Key', 'Copy key')}
          </Button>
          <span className="text-[10px] text-zinc-500">
            {tr(
              '调用方式：Authorization: Bearer <Key>',
              'Usage: Authorization: Bearer <Key>',
            )}
          </span>
        </div>
      </div>
    </Modal>
  );
}
