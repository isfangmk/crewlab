import { useAssistantPage } from '@/lib/assistantPage';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Check, ChevronLeft, RefreshCw, ShieldCheck, X } from 'lucide-react';
import { getApproval, approveApproval, rejectApproval, type Approval } from '@/api/approvals';
import {
  Button,
  CodeBlock,
  DetailSection,
  DetailStat,
  DetailStatGrid,
  EmptyStateCard,
  PageHeader,
  RowAction,
  StatusPill,
} from '@/components/ui';
import { RejectApprovalDialog } from '@/components/RejectApprovalDialog';
import { useI18n } from '@/i18n/locale';
import { APPROVAL_STATUS_META } from '@/lib/approvalStatus';
import { fullDateTime } from '@/lib/format';
import { useAuth } from '@/store/auth';

/** 详情页用长文案说明状态含义；语义色取共享映射，避免同一状态两种颜色。 */
const STATUS_DETAIL_TEXT: Record<Approval['status'], { zh: string; en: string }> = {
  pending: { zh: '等待确认', en: 'Awaiting approval' },
  approved: { zh: '已批准，等待执行结果', en: 'Approved; awaiting execution result' },
  executed: { zh: '已执行', en: 'Executed' },
  rejected: { zh: '已拒绝', en: 'Rejected' },
  failed: { zh: '执行失败或请求已失效', en: 'Execution failed or request invalidated' },
  expired: { zh: '已过期', en: 'Expired' },
};

export default function ApprovalDetailPage() {
  const { id = '' } = useParams();
  const { tr } = useI18n();
  const role = useAuth(s => s.role);
  const [item, setItem] = useState<Approval | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const deciding = useRef(false);
  const generation = useRef(0);
  const requestSeq = useRef(0);
  const load = useCallback(async () => {
    const current = ++requestSeq.current;
    try {
      const next = await getApproval(id);
      if (current === requestSeq.current) setItem(next);
    } catch (e) {
      if (current === requestSeq.current) setError((e as Error).message);
    } finally { if (current === requestSeq.current) setLoading(false); }
  }, [id]);

  const invalidate = useCallback(() => { requestSeq.current++; generation.current++; }, []);
  useEffect(() => {
    generation.current++;
    setItem(null); setError(''); setLoading(true);
    if (role !== 'admin') { setLoading(false); return; }
    void load();
    return invalidate;
  }, [load, role, invalidate]);
  useEffect(() => {
    if (!item || !['pending', 'approved'].includes(item.status) || busy) return;
    const timer = window.setInterval(() => { void load(); }, 5000);
    return () => window.clearInterval(timer);
  }, [item, busy, load]);

  // 批准直接提交，无二次确认弹窗。
  const approve = async () => {
    if (!item || item.status !== 'pending' || deciding.current) return;
    deciding.current = true;
    setBusy(true); setError('');
    const current = generation.current;
    try {
      if (current !== generation.current) return;
      try {
        await approveApproval(id);
      } catch (e) {
        if (current === generation.current) setError(tr('提交未确认，请核对最新状态；不要重复执行。', 'Decision not confirmed. Check the latest status before retrying.') + ' ' + (e as Error).message);
      }
      if (current === generation.current) await load();
    } finally { deciding.current = false; setBusy(false); }
  };

  const rejectWithReason = async (reason: string) => {
    if (!item || item.status !== 'pending' || deciding.current) {
      throw new Error(tr('审批状态已变更，请刷新后重试', 'Approval state changed; refresh and retry'));
    }
    deciding.current = true;
    setBusy(true); setError('');
    const current = generation.current;
    try {
      if (current !== generation.current) {
        throw new Error(tr('审批状态已变更，请刷新后重试', 'Approval state changed; refresh and retry'));
      }
      try {
        await rejectApproval(id, reason);
      } catch (e) {
        const msg = tr('提交未确认，请核对最新状态；不要重复执行。', 'Decision not confirmed. Check the latest status before retrying.') + ' ' + (e as Error).message;
        if (current === generation.current) setError(msg);
        throw new Error(msg);
      }
      if (current === generation.current) await load();
    } finally { deciding.current = false; setBusy(false); }
  };

  useAssistantPage('审批详情', 'Approval details', {
    approval_id: id,
    kind: item?.kind,
    status: item?.status,
    loading,
  });

  const statusText = item ? STATUS_DETAIL_TEXT[item.status] : null;
  const statusUI = item ? APPROVAL_STATUS_META[item.status] : null;

  return (
    <main className="anim-fade flex min-w-0 flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tr('审批详情', 'Approval details')}
        subtitle={tr('查看操作内容后，再决定是否批准执行。', 'Review the action before approving execution.')}
        leading={
          <Link
            to="/approvals"
            className="inline-flex items-center gap-1 text-[11px] text-zinc-500 transition-colors hover:text-zinc-300"
          >
            <ChevronLeft size={12} aria-hidden />
            {tr('全部审批', 'All approvals')}
          </Link>
        }
        actions={
          statusUI ? (
            <StatusPill
              tone={statusUI.tone}
              label={statusText === null ? '' : tr(statusText.zh, statusText.en)}
              pulse={item?.status === 'pending'}
              className="h-6 px-2 text-[11px]"
            />
          ) : undefined
        }
      />

      <div className="flex-1 overflow-auto px-6 py-4">
        <div className="mx-auto w-full max-w-3xl space-y-4">
          {role !== 'admin' ? (
            <EmptyStateCard
              icon={ShieldCheck}
              title={tr('需要管理员权限', 'Administrator access required')}
              hint={tr('请联系管理员查看并处理此审批。', 'Ask an administrator to review this approval.')}
            />
          ) : (
            <>
              {error && (
                <div
                  role="alert"
                  className="break-words rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-[12px] leading-5 text-red-300"
                >
                  {error}
                </div>
              )}
              {loading ? (
                <p className="text-[12px] text-zinc-500">{tr('加载中…', 'Loading…')}</p>
              ) : item ? (
                <>
                  <header className="min-w-0 space-y-1">
                    <h2 className="break-words text-[16px] font-semibold leading-6 text-zinc-100">{item.title}</h2>
                    {item.summary && item.summary !== item.title ? (
                      <p className="whitespace-pre-wrap break-words text-[12px] leading-5 text-zinc-400">{item.summary}</p>
                    ) : null}
                  </header>

                  <DetailStatGrid>
                    <DetailStat label={tr('审批编号', 'Approval ID')} value={item.id} />
                    <DetailStat label={tr('操作类型', 'Action type')} value={item.kind} />
                    <DetailStat label={tr('提交时间', 'Created')} value={fullDateTime(item.created_at)} />
                    <DetailStat
                      label={tr('决策时间', 'Decided')}
                      value={item.decided_at ? fullDateTime(item.decided_at) : '—'}
                    />
                  </DetailStatGrid>

                  <DetailSection title={tr('操作内容', 'Action payload')}>
                    <CodeBlock maxHeightClassName="max-h-80">{reviewPayload(item.payload)}</CodeBlock>
                  </DetailSection>

                  {item.result ? (
                    <DetailSection title={tr('执行结果', 'Execution result')}>
                      <CodeBlock maxHeightClassName="max-h-80">{reviewPayload(item.result)}</CodeBlock>
                    </DetailSection>
                  ) : null}

                  {item.decision_reason || item.reason ? (
                    <DetailSection title={tr('决策说明', 'Decision reason')}>
                      <p className="whitespace-pre-wrap break-words rounded-lg border border-zinc-800/60 bg-zinc-900/30 px-3 py-2 text-[12px] leading-5 text-zinc-300">
                        {item.decision_reason || item.reason}
                      </p>
                    </DetailSection>
                  ) : null}

                  {item.status === 'approved' ? (
                    <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] leading-5 text-amber-300">
                      {tr('批准不代表执行完成；结果未确认前请勿重新发起相同操作。', 'Approval does not mean completion. Do not repeat the operation while its result is unconfirmed.')}
                    </p>
                  ) : null}
                </>
              ) : (
                <div className="flex items-center gap-2">
                  <span className="text-[12px] text-zinc-500">{tr('未能加载这条审批。', 'This approval could not be loaded.')}</span>
                  <Button variant="outline" size="sm" onClick={() => { setError(''); void load(); }}>
                    {tr('重试', 'Retry')}
                  </Button>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {role === 'admin' && item ? (
        <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-zinc-800/60 bg-zinc-950/40 px-6 py-3">
          <span className="mr-auto text-[11px] text-zinc-500">
            {item.status === 'pending'
              ? tr('批准后立即执行；拒绝需填写原因。', 'Approving executes immediately; rejecting requires a reason.')
              : tr('该审批已处理，可刷新查看最新状态。', 'This approval is decided; refresh for the latest status.')}
          </span>
          {item.status === 'pending' ? (
            <>
              <RowAction icon={X} tone="danger" disabled={busy} onClick={() => setRejectOpen(true)}>
                {tr('拒绝', 'Reject')}
              </RowAction>
              <Button variant="primary" size="sm" disabled={busy} onClick={() => void approve()}>
                <Check size={12} aria-hidden />
                {tr('批准并执行', 'Approve and execute')}
              </Button>
            </>
          ) : null}
          <RowAction
            icon={RefreshCw}
            iconClassName={busy ? 'animate-spin' : undefined}
            disabled={busy}
            onClick={() => { setError(''); void load(); }}
          >
            {tr('刷新状态', 'Refresh status')}
          </RowAction>
        </footer>
      ) : null}

      <RejectApprovalDialog
        open={rejectOpen}
        subject={item?.title}
        onClose={() => setRejectOpen(false)}
        onConfirm={rejectWithReason}
      />
    </main>
  );
}

// Do not expose execution capabilities or credential fields in the review UI.
// This is presentation redaction, not a substitute for server-side authorization.
export function reviewPayload(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), (key, value) => {
      if (/token|password|secret|credential|authorization/i.test(key)) return '[redacted]';
      if (key === 'args_json' && typeof value === 'string') {
        try { return JSON.parse(value); } catch { return value; }
      }
      return value;
    }, 2);
  } catch { return raw; }
}
