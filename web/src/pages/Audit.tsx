import { useAssistantPage } from '@/lib/assistantPage';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, Download, RefreshCw, ScrollText, Settings2, Shield } from 'lucide-react';
import {
  Button,
  CodeBlock,
  EmptyStateCard,
  FilterBar,
  FilterPill,
  Input,
  Label,
  PageHeader,
  RowAction,
  TableCard,
  TablePager,
} from '@/components/ui';
import { Select } from '@/components/ui/Select';
import { Modal } from '@/components/Modal';
import { ApiError } from '@/api/client';
import { exportAudit, listAudit, type AuditItem } from '@/api/audit';
import { exportSettings } from '@/api/settings';
import { listUsers, type ManagedUser } from '@/api/users';
import { saveBlob } from '@/lib/download';
import { useI18n } from '@/i18n/locale';
import { fullDateTime } from '@/lib/format';
import { parseLocalDateTime, rangeForPreset, type TimePreset } from '@/lib/timeRange';
import { usePermissions } from '@/store/me';

const PAGE_SIZE = 20;

const ACTION_LABELS: Record<string, { zh: string; en: string }> = {
  'user.create': { zh: '创建用户', en: 'Create user' },
  'user.status': { zh: '启停用用户', en: 'Toggle user status' },
  'user.role': { zh: '修改角色', en: 'Change user role' },
  'user.password_reset': { zh: '重置密码', en: 'Reset password' },
  'user.delete': { zh: '删除用户', en: 'Delete user' },
  'approval.approve': { zh: '批准审批', en: 'Approve' },
  'approval.reject': { zh: '拒绝审批', en: 'Reject' },
  'approval.batch_approve': { zh: '批量批准', en: 'Batch approve' },
  'approval.batch_reject': { zh: '批量拒绝', en: 'Batch reject' },
  'tool.run': { zh: '运行内置工具', en: 'Run builtin tool' },
  'mcp.tool_run': { zh: '运行 MCP 工具', en: 'Run MCP tool' },
  'mcp.server_create': { zh: '新增 MCP Server', en: 'Create MCP server' },
  'mcp.server_update': { zh: '修改 MCP Server', en: 'Update MCP server' },
  'mcp.server_delete': { zh: '删除 MCP Server', en: 'Delete MCP server' },
  'secret.create': { zh: '创建凭证', en: 'Create credential' },
  'secret.delete': { zh: '删除凭证', en: 'Delete credential' },
  'agent.create': { zh: '新建助理', en: 'Create assistant' },
  'agent.update': { zh: '修改助理', en: 'Update assistant' },
  'agent.delete': { zh: '删除助理', en: 'Delete assistant' },
  'knowledge.doc_create': { zh: '新增文档', en: 'Create document' },
  'knowledge.doc_update': { zh: '修改文档', en: 'Update document' },
  'knowledge.doc_move': { zh: '移动文档', en: 'Move document' },
  'knowledge.doc_delete': { zh: '删除文档', en: 'Delete document' },
  'knowledge.doc_upload': { zh: '上传文档', en: 'Upload document' },
  'knowledge.doc_toggle': { zh: '启停文档检索', en: 'Toggle doc retrieval' },
  'knowledge.reindex': { zh: '重建知识索引', en: 'Reindex knowledge' },
  'setting.update': { zh: '修改系统设置', en: 'Update setting' },
  'setting.reveal': { zh: '查看密钥明文', en: 'Reveal secret' },
  'llm.save': { zh: '保存模型配置', en: 'Save LLM config' },
  'session.share': { zh: '创建会话分享', en: 'Share session' },
  'session.delete': { zh: '删除会话', en: 'Delete session' },
  'session.batch_delete': { zh: '批量删除会话', en: 'Batch delete sessions' },
  'session.model': { zh: '切换会话模型', en: 'Switch session model' },
  'session.agent': { zh: '切换会话助理', en: 'Switch session assistant' },
  'auth.login': { zh: '登录', en: 'Sign in' },
  'skill.create': { zh: '新建技能', en: 'Create skill' },
  'skill.update': { zh: '修改技能', en: 'Update skill' },
  'skill.delete': { zh: '删除技能', en: 'Delete skill' },
  'skill.import': { zh: '导入技能包', en: 'Import skill package' },
  'skill.export': { zh: '导出技能包', en: 'Export skill package' },
  'skills.reload': { zh: '重载技能目录', en: 'Reload skills' },
  'secret.rotate': { zh: '轮换凭证', en: 'Rotate credential' },
  'secret.export': { zh: '导出凭证清单', en: 'Export credentials' },
  'secret.verify': { zh: '验证凭证引用', en: 'Verify credential usage' },
  'setting.export': { zh: '导出配置快照', en: 'Export settings snapshot' },
  'audit.export': { zh: '导出审计日志', en: 'Export audit log' },
};

const RESOURCE_LABELS: Record<string, { zh: string; en: string }> = {
  user: { zh: '用户', en: 'User' },
  approval: { zh: '审批', en: 'Approval' },
  builtin_tool: { zh: '内置工具', en: 'Builtin tool' },
  mcp_server: { zh: 'MCP Server', en: 'MCP server' },
  secret: { zh: '凭证', en: 'Credential' },
  agent: { zh: '助理', en: 'Assistant' },
  knowledge_doc: { zh: '知识文档', en: 'Knowledge doc' },
  knowledge: { zh: '知识库', en: 'Knowledge' },
  setting: { zh: '系统设置', en: 'Setting' },
  llm_provider: { zh: '模型厂商', en: 'LLM provider' },
  chat_session: { zh: '会话', en: 'Session' },
  skill: { zh: '技能', en: 'Skill' },
  audit: { zh: '审计', en: 'Audit' },
};

function summary(text: string, limit = 96): string {
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length <= limit ? flat : `${flat.slice(0, limit)}…`;
}

export default function AuditPage() {
  const { tr } = useI18n();
  const { isAdmin } = usePermissions();
  const [items, setItems] = useState<AuditItem[]>([]);
  const [total, setTotal] = useState(0);
  const [actions, setActions] = useState<string[]>([]);
  const [resourceTypes, setResourceTypes] = useState<string[]>([]);
  const [action, setAction] = useState('');
  const [resourceType, setResourceType] = useState('');
  const [actorId, setActorId] = useState<number | ''>('');
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [timePreset, setTimePreset] = useState<TimePreset>('all');
  const [customFrom, setCustomFrom] = useState('');
  const [customTo, setCustomTo] = useState('');
  const [sortDir, setSortDir] = useState<'desc' | 'asc'>('desc');
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [detail, setDetail] = useState<AuditItem | null>(null);
  const [exporting, setExporting] = useState(false);

  const resetFiltersToFirstPage = () => setPage(0);

  const load = useCallback(
    async (silent = false) => {
      if (silent) setRefreshing(true);
      else setLoading(true);
      try {
        const presetRange = rangeForPreset(timePreset);
        const createdFrom = timePreset === 'custom' ? parseLocalDateTime(customFrom) : presetRange.from;
        const createdTo = timePreset === 'custom' ? parseLocalDateTime(customTo) : presetRange.to;
        const res = await listAudit({
          action: action || undefined,
          resource_type: resourceType || undefined,
          actor_user_id: actorId === '' ? undefined : actorId,
          created_from: createdFrom,
          created_to: createdTo,
          sort_dir: sortDir,
          limit: PAGE_SIZE,
          offset: page * PAGE_SIZE,
        });
        setItems(res.items);
        setTotal(res.total);
        setActions(res.actions);
        setResourceTypes(res.resource_types);
        setErr(null);
      } catch (e) {
        setErr(e instanceof ApiError ? e.message : (e as Error).message);
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [action, resourceType, actorId, timePreset, customFrom, customTo, sortDir, page],
  );

  useEffect(() => {
    if (isAdmin) void load();
  }, [isAdmin, load]);

  useEffect(() => {
    if (!isAdmin) return;
    void listUsers()
      .then((rows) => setUsers(rows))
      .catch(() => setUsers([]));
  }, [isAdmin]);

  const runExport = async () => {
    setExporting(true);
    try {
      const presetRange = rangeForPreset(timePreset);
      const createdFrom = timePreset === 'custom' ? parseLocalDateTime(customFrom) : presetRange.from;
      const createdTo = timePreset === 'custom' ? parseLocalDateTime(customTo) : presetRange.to;
      const { blob, filename } = await exportAudit({
        action: action || undefined,
        resource_type: resourceType || undefined,
        actor_user_id: actorId === '' ? undefined : actorId,
        created_from: createdFrom,
        created_to: createdTo,
        sort_dir: sortDir,
      });
      saveBlob(blob, filename);
      setErr(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setExporting(false);
    }
  };

  // 配置快照与审计同属合规材料：导出都带脱敏，且导出本身会被审计。
  const runSettingsExport = async () => {
    setExporting(true);
    try {
      const { blob, filename } = await exportSettings();
      saveBlob(blob, filename);
      setErr(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setExporting(false);
    }
  };

  useAssistantPage('审计日志', 'Audit logs', {
    total,
    action,
    resource_type: resourceType,
    time_preset: timePreset,
    sort: `created_at:${sortDir}`,
    page,
    loading,
  });

  const actionOptions = useMemo(
    () => [
      { value: '', label: tr('全部动作', 'All actions') },
      ...actions.map((a) => {
        const meta = ACTION_LABELS[a];
        return { value: a, label: meta ? tr(meta.zh, meta.en) : a };
      }),
    ],
    [actions, tr],
  );

  const resourceOptions = useMemo(
    () => [
      { value: '', label: tr('全部资源', 'All resources') },
      ...resourceTypes.map((r) => {
        const meta = RESOURCE_LABELS[r];
        return { value: r, label: meta ? tr(meta.zh, meta.en) : r };
      }),
    ],
    [resourceTypes, tr],
  );

  const actorOptions = useMemo(
    () => [
      { value: '', label: tr('全部操作者', 'All actors') },
      ...users.map((u) => ({ value: String(u.id), label: u.email })),
    ],
    [users, tr],
  );

  if (!isAdmin) {
    return (
      <main className="anim-fade flex flex-1 flex-col overflow-hidden">
        <PageHeader title={tr('审计日志', 'Audit logs')} subtitle={tr('关键动作留痕', 'Action trail')} />
        <div className="flex-1 overflow-y-auto px-6 py-6">
          <div className="mx-auto max-w-3xl">
            <EmptyStateCard
              icon={Shield}
              title={tr('需要管理员权限', 'Admin permission required')}
              hint={tr('审计日志只对管理员开放。', 'Audit logs are admin-only.')}
            />
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tr('审计日志', 'Audit logs')}
        subtitle={tr('用户、审批、工具与凭证的关键动作留痕', 'Trail for users, approvals, tools, and credentials')}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <RowAction
              icon={Download}
              iconClassName={exporting ? 'animate-pulse' : undefined}
              disabled={loading || exporting || items.length === 0}
              onClick={() => void runExport()}
            >
              {tr('导出审计（脱敏）', 'Export audit (redacted)')}
            </RowAction>
            <RowAction
              icon={Settings2}
              disabled={loading || exporting}
              onClick={() => void runSettingsExport()}
            >
              {tr('配置快照（脱敏）', 'Settings snapshot')}
            </RowAction>
            <RowAction
              icon={RefreshCw}
              iconClassName={refreshing ? 'animate-spin' : undefined}
              disabled={loading || refreshing}
              onClick={() => void load(true)}
            >
              {tr('刷新', 'Refresh')}
            </RowAction>
          </div>
        }
      />

      <FilterBar className="mx-6 mt-3">
        <FilterPill
          label={tr('动作', 'Action')}
          active={action !== ''}
          onClear={() => {
            resetFiltersToFirstPage();
            setAction('');
          }}
          width="w-[11rem]"
        >
          <Select
            aria-label={tr('动作', 'Action')}
            value={action}
            onValueChange={(v) => {
              resetFiltersToFirstPage();
              setAction(v);
            }}
            options={actionOptions}
            variant="ghost"
            className="h-7 min-w-0 border-0 bg-transparent px-0 text-[12px] shadow-none"
          />
        </FilterPill>
        <FilterPill
          label={tr('资源', 'Resource')}
          active={resourceType !== ''}
          onClear={() => {
            resetFiltersToFirstPage();
            setResourceType('');
          }}
          width="w-[10rem]"
        >
          <Select
            aria-label={tr('资源类型', 'Resource type')}
            value={resourceType}
            onValueChange={(v) => {
              resetFiltersToFirstPage();
              setResourceType(v);
            }}
            options={resourceOptions}
            variant="ghost"
            className="h-7 min-w-0 border-0 bg-transparent px-0 text-[12px] shadow-none"
          />
        </FilterPill>
        <FilterPill
          label={tr('操作者', 'Actor')}
          active={actorId !== ''}
          onClear={() => {
            resetFiltersToFirstPage();
            setActorId('');
          }}
          width="w-[12rem]"
        >
          <Select
            aria-label={tr('操作者', 'Actor')}
            value={actorId === '' ? '' : String(actorId)}
            onValueChange={(v) => {
              resetFiltersToFirstPage();
              setActorId(v === '' ? '' : Number(v));
            }}
            options={actorOptions}
            variant="ghost"
            className="h-7 min-w-0 border-0 bg-transparent px-0 text-[12px] shadow-none"
          />
        </FilterPill>
        <FilterPill
          label={tr('时间', 'Time')}
          active={timePreset !== 'all'}
          onClear={() => {
            resetFiltersToFirstPage();
            setTimePreset('all');
            setCustomFrom('');
            setCustomTo('');
          }}
          width="w-[9rem]"
        >
          <Select
            aria-label={tr('时间范围', 'Time range')}
            value={timePreset}
            onValueChange={(v) => {
              resetFiltersToFirstPage();
              setTimePreset(v as TimePreset);
            }}
            options={[
              { value: 'all', label: tr('全部时间', 'All time') },
              { value: 'today', label: tr('今天', 'Today') },
              { value: '7d', label: tr('近 7 天', 'Last 7 days') },
              { value: '30d', label: tr('近 30 天', 'Last 30 days') },
              { value: 'custom', label: tr('自定义', 'Custom') },
            ]}
            variant="ghost"
            className="h-7 min-w-0 border-0 bg-transparent px-0 text-[12px] shadow-none"
          />
        </FilterPill>
        {timePreset === 'custom' ? (
          <>
            <Input
              type="datetime-local"
              aria-label={tr('开始时间', 'Start')}
              value={customFrom}
              onChange={(e) => {
                resetFiltersToFirstPage();
                setCustomFrom(e.target.value);
              }}
              className="h-8 w-[12.5rem] text-[12px]"
            />
            <span aria-hidden className="text-[11px] text-zinc-600">→</span>
            <Input
              type="datetime-local"
              aria-label={tr('结束时间', 'End')}
              value={customTo}
              onChange={(e) => {
                resetFiltersToFirstPage();
                setCustomTo(e.target.value);
              }}
              className="h-8 w-[12.5rem] text-[12px]"
            />
          </>
        ) : null}
        <Label className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-zinc-800/60 bg-zinc-900/50 px-2 text-[11px] text-zinc-400">
          <ScrollText size={12} aria-hidden />
          {tr(`共 ${total} 条`, `${total} total`)}
        </Label>
      </FilterBar>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden px-6 py-4">
        {err && (
          <div className="mb-3 rounded-lg border border-red-500/40 bg-red-500/5 px-4 py-3 text-sm text-red-300">
            {err}
          </div>
        )}
        <div className="flex min-h-0 flex-1 flex-col">
          {loading ? (
            <div className="flex h-40 items-center justify-center text-sm text-zinc-500">
              {tr('加载中…', 'Loading…')}
            </div>
          ) : items.length === 0 ? (
            <EmptyStateCard
              icon={ScrollText}
              title={
                action !== '' || resourceType !== '' || actorId !== '' || timePreset !== 'all'
                  ? tr('没有匹配的审计记录', 'No matching audit records')
                  : tr('还没有审计记录', 'No audit records yet')
              }
            />
          ) : (
            <TableCard
              footer={
                <TablePager
                  page={page}
                  pageSize={PAGE_SIZE}
                  total={total}
                  shown={items.length}
                  loading={loading || refreshing}
                  onPageChange={setPage}
                />
              }
            >
              <table className="og-action-table w-full min-w-[62rem] table-fixed text-sm">
                <colgroup>
                  <col className="w-[11rem]" />
                  <col className="w-[13rem]" />
                  <col className="w-[11rem]" />
                  <col className="w-[12rem]" />
                  <col />
                  <col className="w-[5.5rem]" />
                </colgroup>
                <thead className="sticky top-0 z-10 border-b border-zinc-800/60 bg-zinc-900/95 text-[11px] text-zinc-500 backdrop-blur">
                  <tr className="whitespace-nowrap">
                    <th className="px-3 py-2 text-left font-medium" aria-sort={sortDir === 'asc' ? 'ascending' : 'descending'}>
                      <button
                        type="button"
                        className="inline-flex items-center gap-1 hover:text-zinc-200"
                        onClick={() => {
                          resetFiltersToFirstPage();
                          setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
                        }}
                      >
                        {tr('时间', 'Time')}
                        {sortDir === 'desc' ? (
                          <ArrowDown size={12} aria-hidden />
                        ) : (
                          <ArrowUp size={12} aria-hidden />
                        )}
                      </button>
                    </th>
                    <th className="px-3 py-2 text-left font-medium">{tr('操作者', 'Actor')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('动作', 'Action')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('资源', 'Resource')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('详情', 'Detail')}</th>
                    <th className="px-3 py-2 text-right font-medium">{tr('操作', 'Actions')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/40">
                  {items.map((row) => {
                    const actionMeta = ACTION_LABELS[row.action];
                    const resourceMeta = RESOURCE_LABELS[row.resource_type];
                    return (
                      <tr key={row.id} className="transition-colors">
                        <td className="whitespace-nowrap px-3 py-2 text-[12px] tabular-nums text-zinc-500">
                          {fullDateTime(row.created_at)}
                        </td>
                        <td className="min-w-0 px-3 py-2">
                          <span className="block truncate text-[12px] text-zinc-300" title={row.actor_email ?? ''}>
                            {row.actor_email ?? '—'}
                          </span>
                        </td>
                        <td className="min-w-0 px-3 py-2">
                          <span className="block truncate text-[12px] text-zinc-200" title={row.action}>
                            {actionMeta ? tr(actionMeta.zh, actionMeta.en) : row.action}
                          </span>
                        </td>
                        <td className="min-w-0 px-3 py-2">
                          <span className="block truncate text-[12px] text-zinc-400" title={row.resource_id}>
                            {resourceMeta ? tr(resourceMeta.zh, resourceMeta.en) : row.resource_type}
                            {row.resource_id ? ` · ${row.resource_id}` : ''}
                          </span>
                        </td>
                        <td className="min-w-0 px-3 py-2">
                          <span className="block truncate font-mono text-[11px] text-zinc-500" title={JSON.stringify(row.detail)}>
                            {summary(JSON.stringify(row.detail))}
                          </span>
                        </td>
                        <td className="px-3 py-2">
                          <div className="flex items-center justify-end">
                            <Button variant="ghost" size="sm" type="button" onClick={() => setDetail(row)}>
                              {tr('详情', 'Detail')}
                            </Button>
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
      </div>

      <Modal
        open={detail !== null}
        onClose={() => setDetail(null)}
        title={tr('审计详情', 'Audit detail')}
        size="lg"
      >
        {detail && (
          <div className="space-y-3 text-[12px]">
            <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1.5">
              <dt className="text-zinc-500">{tr('时间', 'Time')}</dt>
              <dd className="tabular-nums text-zinc-200">{fullDateTime(detail.created_at)}</dd>
              <dt className="text-zinc-500">{tr('操作者', 'Actor')}</dt>
              <dd className="text-zinc-200">
                {detail.actor_email ?? '—'}
                {detail.actor_user_id !== null ? ` (#${detail.actor_user_id})` : ''}
              </dd>
              <dt className="text-zinc-500">{tr('动作', 'Action')}</dt>
              <dd className="font-mono text-zinc-200">{detail.action}</dd>
              <dt className="text-zinc-500">{tr('资源', 'Resource')}</dt>
              <dd className="break-all font-mono text-zinc-200">
                {detail.resource_type}
                {detail.resource_id ? ` / ${detail.resource_id}` : ''}
              </dd>
              <dt className="text-zinc-500">Request ID</dt>
              <dd className="break-all font-mono text-zinc-400">{detail.request_id ?? '—'}</dd>
            </dl>
            <div>
              <div className="mb-1 text-[11px] uppercase tracking-wide text-zinc-500">
                {tr('详情 JSON', 'Detail JSON')}
              </div>
              <CodeBlock>{JSON.stringify(detail.detail, null, 2)}</CodeBlock>
              <p className="mt-1.5 text-[11px] leading-relaxed text-zinc-600">
                {tr(
                  '此处为存储原文；导出 CSV 默认对密钥类字段与 URL 凭证脱敏。',
                  'This is the stored payload; CSV export masks secret fields and URL credentials by default.',
                )}
              </p>
            </div>
          </div>
        )}
      </Modal>
    </main>
  );
}
