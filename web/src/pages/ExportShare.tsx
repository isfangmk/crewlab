// 导出与分享管理：分享链接列表（会话 + 评测统一）+ 生成新链接 + 撤销；
// 导出会话 MD/JSON、评测 JSON/CSV；PDF 走打印视图（浏览器打印另存为 PDF）。
//
// 安全口径：原链接无法回显（库里只存 token 哈希）——「获取链接」= 生成
// 一条新分享并复制，旧链接可单独撤销。
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Check,
  Copy,
  Download,
  FileText,
  Link2,
  Printer,
  RefreshCw,
  Share2,
  Trash2,
} from 'lucide-react';
import { createEvalRunShare, exportEvalRun, listEvalDatasets, type EvalRun } from '@/api/evals';
import { createSessionShare, exportSession, listSessions } from '@/api/chat';
import { listShares, revokeShare, type ShareItem, type ShareKind } from '@/api/shares';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { Modal } from '@/components/Modal';
import {
  Button,
  ChoiceChip,
  COL,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
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
  SortButton,
  StatusPill,
  TableCard,
  TableHead,
  TablePager,
  flash,
  tableLayout,
} from '@/components/ui';
import { useI18n } from '@/i18n/locale';
import { usePermissions } from '@/store/me';
import { errorText } from '@/lib/errors';
import { fullDateTime } from '@/lib/format';

const PAGE_SIZE_OPTIONS = [20, 50, 100];
const HOUR_PRESETS = [
  { hours: 24, zh: '24 小时', en: '24 hours' },
  { hours: 72, zh: '3 天', en: '3 days' },
  { hours: 168, zh: '7 天', en: '7 days' },
  { hours: 720, zh: '30 天', en: '30 days' },
] as const;

function downloadText(text: string, filename: string, mime: string) {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

type GeneratePreset = { kind: ShareKind; target_id: string; title: string };

/** 生成分享链接：新分享独立成行，旧链接不自动作废（可单独撤销）。 */
function ShareGenerateDialog({
  open,
  preset,
  onClose,
  onCreated,
}: {
  open: boolean;
  /** 行内「获取链接」= 锁定对象只选有效期；空 = 自选对象 */
  preset: GeneratePreset | null;
  onClose(): void;
  onCreated(): void;
}) {
  const { tr } = useI18n();
  const [kind, setKind] = useState<ShareKind>('session');
  const [targetId, setTargetId] = useState('');
  const [hours, setHours] = useState(24);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<{ link: string; expiresAt: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [sessionOptions, setSessionOptions] = useState<{ value: string; label: string }[]>([]);
  const [evalOptions, setEvalOptions] = useState<{ value: string; label: string }[]>([]);

  useEffect(() => {
    if (!open) return;
    setBusy(false);
    setErr(null);
    setResult(null);
    setCopied(false);
    setHours(24);
    if (preset) {
      setKind(preset.kind);
      setTargetId(preset.target_id);
    } else {
      setTargetId('');
    }
    void (async () => {
      try {
        const [sessions, datasets] = await Promise.all([listSessions({ limit: 50 }), listEvalDatasets()]);
        setSessionOptions(
          sessions.items.map((s) => ({ value: s.id, label: s.title || s.id })),
        );
        setEvalOptions(
          datasets.items
            .filter((d) => d.last_run !== null)
            .map((d) => {
              const run = d.last_run as EvalRun;
              return {
                value: run.run_uid,
                label: `${d.name} · ${run.started_at ? fullDateTime(run.started_at) : run.run_uid}`,
              };
            }),
        );
      } catch (e) {
        setErr(errorText(e));
      }
    })();
  }, [open, preset]);

  const create = async () => {
    if (targetId === '') {
      setErr(tr('请选择要分享的对象', 'Pick an object to share'));
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const resp =
        kind === 'session'
          ? await createSessionShare(targetId, hours)
          : await createEvalRunShare(targetId, hours);
      const link = `${window.location.origin}${kind === 'session' ? '/share/' : '/eval-share/'}${resp.token}`;
      await navigator.clipboard.writeText(link);
      setResult({ link, expiresAt: resp.expires_at });
      setCopied(true);
      onCreated();
    } catch (e) {
      setResult(null);
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  if (!open) return null;
  return (
    <Modal
      open
      onClose={onClose}
      size="md"
      title={preset ? tr('获取分享链接', 'Get share link') : tr('生成分享链接', 'Create share link')}
      footer={
        result === null ? (
          <>
            <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>
              {tr('取消', 'Cancel')}
            </Button>
            <Button
              type="button"
              variant="primary"
              onClick={() => void create()}
              disabled={busy || targetId === ''}
            >
              <Link2 size={13} />
              {busy ? tr('生成中…', 'Creating…') : tr('生成并复制链接', 'Create & copy link')}
            </Button>
          </>
        ) : (
          <>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                void navigator.clipboard.writeText(result.link).then(() => setCopied(true));
              }}
            >
              {copied ? <Check size={13} /> : <Copy size={13} />}
              {copied ? tr('已复制', 'Copied') : tr('再复制', 'Copy again')}
            </Button>
            <Button type="button" variant="primary" onClick={onClose}>
              {tr('完成', 'Done')}
            </Button>
          </>
        )
      }
    >
      <div className="space-y-4">
        {err && <ErrorBanner>{err}</ErrorBanner>}
        {result === null ? (
          <>
            <p className="text-sm text-zinc-400">
              {tr(
                '生成只读链接，对方打开即可查看，无需登录。每次生成都是新链接；旧链接在列表里单独撤销。',
                'Create a read-only link anyone can open without signing in. Each creation issues a new link; revoke older links from the list.',
              )}
            </p>
            {preset === null && (
              <div className="space-y-1.5">
                <Label>{tr('类型', 'Type')}</Label>
                <div className="flex gap-1.5">
                  <ChoiceChip
                    active={kind === 'session'}
                    label={tr('会话', 'Session')}
                    onClick={() => {
                      setKind('session');
                      setTargetId('');
                    }}
                  />
                  <ChoiceChip
                    active={kind === 'eval'}
                    label={tr('评测运行', 'Eval run')}
                    onClick={() => {
                      setKind('eval');
                      setTargetId('');
                    }}
                  />
                </div>
              </div>
            )}
            <div className="space-y-1.5">
              <Label>{tr('对象', 'Object')}</Label>
              {preset !== null ? (
                <div className="rounded-lg border border-zinc-800/60 bg-zinc-900/40 px-3 py-2 text-sm text-zinc-200">
                  {preset.title}
                </div>
              ) : (
                <Select
                  aria-label={tr('选择对象', 'Pick object')}
                  value={targetId}
                  onValueChange={setTargetId}
                  searchable
                  options={kind === 'session' ? sessionOptions : evalOptions}
                />
              )}
            </div>
            <div className="space-y-1.5">
              <Label>{tr('有效期', 'Expires in')}</Label>
              <div className="flex flex-wrap gap-1.5">
                {HOUR_PRESETS.map((p) => (
                  <ChoiceChip
                    key={p.hours}
                    active={hours === p.hours}
                    label={tr(p.zh, p.en)}
                    onClick={() => setHours(p.hours)}
                  />
                ))}
              </div>
            </div>
          </>
        ) : (
          <div className="space-y-3">
            <div>
              <span className="mb-1 block text-[12px] text-zinc-500">{tr('分享链接', 'Share link')}</span>
              <Input readOnly value={result.link} aria-label={tr('分享链接', 'Share link')} />
            </div>
            <p className="text-[11px] text-zinc-500">
              {tr(`有效期至 ${fullDateTime(result.expiresAt)}`, `Expires ${fullDateTime(result.expiresAt)}`)}
            </p>
          </div>
        )}
      </div>
    </Modal>
  );
}

export default function ExportSharePage() {
  const { tr } = useI18n();
  const { isViewer } = usePermissions();
  const [items, setItems] = useState<ShareItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const [kind, setKind] = useState<'all' | ShareKind>('all');
  const [status, setStatus] = useState<'all' | 'active' | 'expired'>('all');
  const [sortBy, setSortBy] = useState<'created_at' | 'expires_at'>('created_at');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(20);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);
  const seqRef = useRef(0);
  const [generateOpen, setGenerateOpen] = useState(false);
  const [generatePreset, setGeneratePreset] = useState<GeneratePreset | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<ShareItem | null>(null);

  const reload = () => setRefreshTick((t) => t + 1);

  // 标题筛选 250ms 防抖 + 竞态守卫：连改筛选时不让慢响应覆盖新结果
  useEffect(() => {
    const seq = ++seqRef.current;
    setLoading(true);
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const res = await listShares({
            kind,
            status,
            q,
            sort_by: sortBy,
            sort_dir: sortDir,
            limit: pageSize,
            offset: page * pageSize,
          });
          if (seqRef.current !== seq) return;
          setItems(res.items);
          setTotal(res.total);
          setErr(null);
        } catch (e) {
          if (seqRef.current !== seq) return;
          setErr(errorText(e));
        } finally {
          if (seqRef.current === seq) {
            setLoading(false);
            setRefreshing(false);
          }
        }
      })();
    }, 250);
    return () => clearTimeout(timer);
  }, [q, kind, status, sortBy, sortDir, page, pageSize, refreshTick]);

  const resetToFirst = () => setPage(0);

  const toggleSort = (column: 'created_at' | 'expires_at') => {
    if (sortBy === column) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortBy(column);
      setSortDir('desc');
    }
    resetToFirst();
  };

  const copyLink = async (item: ShareItem) => {
    setGeneratePreset({ kind: item.kind, target_id: item.target_id, title: item.title });
    setGenerateOpen(true);
  };

  const runExport = async (item: ShareItem, format: 'markdown' | 'json' | 'csv' | 'pdf') => {
    try {
      if (format === 'pdf') {
        const path =
          item.kind === 'session'
            ? `/print/session/${encodeURIComponent(item.target_id)}`
            : `/print/eval/${encodeURIComponent(item.target_id)}`;
        window.open(path, '_blank');
        return;
      }
      if (item.kind === 'session') {
        const text = await exportSession(item.target_id, format === 'json' ? 'json' : 'markdown');
        downloadText(
          text,
          `${item.title || item.target_id}.${format === 'json' ? 'json' : 'md'}`,
          format === 'json' ? 'application/json;charset=utf-8' : 'text/markdown;charset=utf-8',
        );
      } else {
        const file = await exportEvalRun(item.target_id, format === 'csv' ? 'csv' : 'json');
        downloadBlob(file.blob, file.filename || `${item.title || item.target_id}.${format}`);
      }
      flash('ok', tr('导出已开始下载', 'Export download started'));
    } catch (e) {
      flash('err', errorText(e));
    }
  };

  const hasActiveFilters = q !== '' || kind !== 'all' || status !== 'all';

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tr('导出与分享', 'Export & share')}
        subtitle={tr(
          '管理只读分享链接（会话与评测）；导出会话 Markdown/JSON、评测 JSON/CSV，PDF 走打印视图',
          'Manage read-only share links (sessions and evals); export session Markdown/JSON, eval JSON/CSV, PDF via print view',
        )}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {!isViewer && (
              <Button
                variant="primary"
                size="sm"
                type="button"
                className="inline-flex items-center gap-1.5"
                onClick={() => {
                  setGeneratePreset(null);
                  setGenerateOpen(true);
                }}
              >
                <Share2 size={13} />
                {tr('生成分享链接', 'Create share link')}
              </Button>
            )}
            <RowAction
              icon={RefreshCw}
              iconClassName={refreshing ? 'animate-spin' : undefined}
              disabled={loading || refreshing}
              onClick={() => {
                setRefreshing(true);
                reload();
              }}
            >
              {tr('刷新', 'Refresh')}
            </RowAction>
          </div>
        }
      />

      <FilterBar
        className="mx-6 mt-3"
        footer={
          hasActiveFilters ? (
            <button
              type="button"
              className="text-[11px] text-zinc-500 hover:text-zinc-300"
              onClick={() => {
                setQ('');
                setKind('all');
                setStatus('all');
                resetToFirst();
              }}
            >
              {tr('清除全部', 'Clear all')}
            </button>
          ) : null
        }
      >
        <SearchInput
          value={q}
          onChange={(v) => {
            setQ(v);
            resetToFirst();
          }}
          ariaLabel={tr('按对象标题筛选', 'Filter by object title')}
          placeholder={tr('按对象标题筛选…', 'Filter by object title…')}
        />
        <div className="flex flex-wrap items-center gap-1.5">
          {(['all', 'session', 'eval'] as const).map((k) => (
            <ChoiceChip
              key={k}
              active={kind === k}
              label={k === 'all' ? tr('全部类型', 'All types') : k === 'session' ? tr('会话', 'Session') : tr('评测', 'Eval')}
              onClick={() => {
                setKind(k);
                resetToFirst();
              }}
            />
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {(['all', 'active', 'expired'] as const).map((s) => (
            <ChoiceChip
              key={s}
              active={status === s}
              label={s === 'all' ? tr('全部状态', 'All') : s === 'active' ? tr('有效', 'Active') : tr('已过期', 'Expired')}
              onClick={() => {
                setStatus(s);
                resetToFirst();
              }}
            />
          ))}
        </div>
      </FilterBar>

      {err && <ErrorBanner className="mx-6 mt-3">{err}</ErrorBanner>}

      {loading ? (
        <div className="p-6 text-center text-sm text-zinc-500">{tr('加载中…', 'Loading…')}</div>
      ) : items.length === 0 ? (
        <EmptyStateCard
          className="mx-6 mt-6"
          icon={Share2}
          title={hasActiveFilters ? tr('没有匹配的分享', 'No matching shares') : tr('还没有分享链接', 'No share links yet')}
          hint={
            hasActiveFilters
              ? tr('换个筛选条件试试', 'Try different filters')
              : tr(
                  '在会话或评测页点「分享」生成只读链接；链接可在本页统一管理与撤销',
                  'Create read-only links from a session or eval page; manage and revoke them here',
                )
          }
          action={
            !isViewer && !hasActiveFilters ? (
              <Button
                variant="primary"
                size="sm"
                type="button"
                onClick={() => {
                  setGeneratePreset(null);
                  setGenerateOpen(true);
                }}
              >
                {tr('生成分享链接', 'Create share link')}
              </Button>
            ) : undefined
          }
        />
      ) : (
        <TableCard
          className="mx-6 mt-3 min-h-0 flex-1"
          footer={
            <TablePager
              page={page}
              pageSize={pageSize}
              total={total}
              shown={items.length}
              loading={loading}
              onPageChange={setPage}
              pageSizeOptions={PAGE_SIZE_OPTIONS}
              onPageSizeChange={(size) => {
                setPage(0);
                setPageSize(size);
              }}
            />
          }
        >
          <table {...tableLayout(66)}>
            <colgroup>
              <col className={COL.tag} />
              <col className={COL.main} />
              <col className={COL.person} />
              <col className={COL.time} />
              <col className={COL.time} />
              <col className={COL.actionsIconWide} />
            </colgroup>
            <TableHead>
              <tr>
                <th className="px-3 py-2 text-left text-[11px] font-medium text-zinc-500">
                  {tr('类型', 'Type')}
                </th>
                <th className="px-3 py-2 text-left text-[11px] font-medium text-zinc-500">
                  {tr('对象', 'Object')}
                </th>
                <th className="px-3 py-2 text-left text-[11px] font-medium text-zinc-500">
                  {tr('创建人', 'Creator')}
                </th>
                <th className="px-3 py-2 text-left text-[11px] font-medium text-zinc-500">
                  <SortButton
                    label={tr('创建于', 'Created')}
                    active={sortBy === 'created_at'}
                    order={sortDir}
                    onClick={() => toggleSort('created_at')}
                  />
                </th>
                <th className="px-3 py-2 text-left text-[11px] font-medium text-zinc-500">
                  <SortButton
                    label={tr('有效期至', 'Expires')}
                    active={sortBy === 'expires_at'}
                    order={sortDir}
                    onClick={() => toggleSort('expires_at')}
                  />
                </th>
                <th className="px-3 py-2 text-right text-[11px] font-medium text-zinc-500">
                  {tr('操作', 'Actions')}
                </th>
              </tr>
            </TableHead>
            <tbody className="divide-y divide-zinc-800/40">
              {items.map((item) => (
                <tr key={`${item.kind}-${item.id}`}>
                  <td className="px-3 py-2">
                    <span
                      className={
                        item.kind === 'session'
                          ? 'inline-flex rounded-md bg-indigo-500/10 px-1.5 py-0.5 text-[11px] text-indigo-300'
                          : 'inline-flex rounded-md bg-teal-500/10 px-1.5 py-0.5 text-[11px] text-teal-300'
                      }
                    >
                      {item.kind === 'session' ? tr('会话', 'Session') : tr('评测', 'Eval')}
                    </span>
                  </td>
                  <td className="max-w-0 truncate px-3 py-2">
                    {item.kind === 'session' ? (
                      <Link
                        to={`/chat/${encodeURIComponent(item.target_id)}`}
                        className="text-zinc-200 hover:text-indigo-300"
                        title={item.title}
                      >
                        {item.title || item.target_id}
                      </Link>
                    ) : (
                      <Link to="/evals" className="text-zinc-200 hover:text-indigo-300" title={item.target_id}>
                        {item.title || item.target_id}
                      </Link>
                    )}
                  </td>
                  <td className="truncate px-3 py-2 text-[12px] text-zinc-400">
                    {item.creator ?? tr('（已删除）', '(deleted)')}
                  </td>
                  <td className="px-3 py-2 font-sans text-[12px] tabular-nums text-zinc-400">
                    {fullDateTime(item.created_at)}
                  </td>
                  <td className="px-3 py-2">
                    <span className="inline-flex items-center gap-1.5">
                      <span className="font-sans text-[12px] tabular-nums text-zinc-400">
                        {fullDateTime(item.expires_at)}
                      </span>
                      {item.expired ? (
                        <StatusPill tone="danger" label={tr('已过期', 'Expired')} />
                      ) : (
                        <StatusPill tone="ok" label={tr('有效', 'Active')} />
                      )}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex items-center justify-end gap-0.5">
                      {!isViewer && (
                        <RowActionIcon
                          icon={Link2}
                          label={tr('获取链接', 'Get link')}
                          onClick={() => void copyLink(item)}
                        />
                      )}
                      <DropdownMenu>
                        <DropdownMenuTrigger
                          render={
                            <button
                              type="button"
                              aria-label={tr('导出', 'Export')}
                              className="inline-flex h-7 w-7 items-center justify-center rounded-md text-zinc-400 transition-colors hover:bg-zinc-800/60 hover:text-zinc-200"
                            />
                          }
                        >
                          <Download size={14} aria-hidden />
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          {item.kind === 'session' ? (
                            <>
                              <DropdownMenuItem onClick={() => void runExport(item, 'markdown')}>
                                <FileText size={12} aria-hidden className="mr-1.5" />
                                {tr('导出 Markdown', 'Export Markdown')}
                              </DropdownMenuItem>
                              <DropdownMenuItem onClick={() => void runExport(item, 'json')}>
                                <FileText size={12} aria-hidden className="mr-1.5" />
                                {tr('导出 JSON', 'Export JSON')}
                              </DropdownMenuItem>
                            </>
                          ) : (
                            <>
                              <DropdownMenuItem onClick={() => void runExport(item, 'json')}>
                                <FileText size={12} aria-hidden className="mr-1.5" />
                                {tr('导出 JSON', 'Export JSON')}
                              </DropdownMenuItem>
                              <DropdownMenuItem onClick={() => void runExport(item, 'csv')}>
                                <FileText size={12} aria-hidden className="mr-1.5" />
                                {tr('导出 CSV', 'Export CSV')}
                              </DropdownMenuItem>
                            </>
                          )}
                          <DropdownMenuItem onClick={() => void runExport(item, 'pdf')}>
                            <Printer size={12} aria-hidden className="mr-1.5" />
                            {tr('导出 PDF（打印）', 'Export PDF (print)')}
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                      {!isViewer && (
                        <RowActionIcon
                          icon={Trash2}
                          tone="danger"
                          label={tr('撤销分享', 'Revoke share')}
                          onClick={() => setRevokeTarget(item)}
                        />
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableCard>
      )}

      <ShareGenerateDialog
        open={generateOpen}
        preset={generatePreset}
        onClose={() => setGenerateOpen(false)}
        onCreated={reload}
      />

      {revokeTarget !== null && (
        <ConfirmDialog
          open
          title={tr('撤销分享链接', 'Revoke share link')}
          description={tr(
            `撤销后该链接立即失效（对方打开会 404）：${revokeTarget.title || revokeTarget.target_id}`,
            `The link dies immediately (visitors get 404): ${revokeTarget.title || revokeTarget.target_id}`,
          )}
          confirmLabel={tr('撤销', 'Revoke')}
          onClose={() => setRevokeTarget(null)}
          onConfirm={async () => {
            await revokeShare(revokeTarget.kind, revokeTarget.id);
            flash('ok', tr('分享已撤销', 'Share revoked'));
            setRevokeTarget(null);
            reload();
          }}
        />
      )}
    </main>
  );
}
