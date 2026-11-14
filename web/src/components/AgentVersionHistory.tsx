import { useCallback, useEffect, useState } from 'react';
import { History, RotateCcw } from 'lucide-react';

import {
  listAgentVersions,
  rollbackAgent,
  type AgentVersionMeta,
} from '@/api/agents';
import { errorText } from '@/lib/errors';
import { fullDateTime } from '@/lib/format';
import { Modal } from '@/components/Modal';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { Button } from '@/components/ui/Button';
import { Chip } from '@/components/ui/Chip';
import { ErrorBanner } from '@/components/ui/Banner';
import { EmptyStateCard } from '@/components/ui/EmptyState';
import { RowAction } from '@/components/ui/RowAction';
import { TableCard } from '@/components/ui/TableCard';
import { TableHead, TableHeadCell } from '@/components/ui/TableHead';
import { useI18n } from '@/i18n/locale';

/** 助理配置版本历史弹窗：列出快照，可回滚到任一版本。 */
export function AgentVersionHistoryModal({
  name,
  onClose,
  onRolledBack,
}: {
  name: string;
  onClose: () => void;
  onRolledBack?: () => void;
}) {
  const { tr } = useI18n();
  const [items, setItems] = useState<AgentVersionMeta[] | null>(null);
  const [err, setErr] = useState('');
  const [confirm, setConfirm] = useState<AgentVersionMeta | null>(null);

  const load = useCallback(async () => {
    setErr('');
    try {
      const r = await listAgentVersions(name);
      setItems(r.items);
    } catch (e) {
      setErr(errorText(e));
      setItems([]);
    }
  }, [name]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Modal
      open
      onClose={onClose}
      size="lg"
      title={tr('版本历史', 'Version history')}
    >
      <div className="space-y-3 text-xs text-zinc-300">
        <p className="text-zinc-500">
          {tr(
            '每次保存都会生成一个版本快照。回滚会用旧版本覆盖当前配置并生成新版本，且立即对所有会话生效。',
            'Every save creates a version snapshot. Rolling back overwrites the current config with an old version, creates a new version, and takes effect for all sessions immediately.',
          )}
        </p>
        {err !== '' && <ErrorBanner>{err}</ErrorBanner>}
        {items === null ? (
          <div className="py-6 text-center text-zinc-500">{tr('加载中…', 'Loading…')}</div>
        ) : items.length === 0 ? (
          <EmptyStateCard
            icon={History}
            title={tr('还没有版本记录', 'No versions yet')}
            hint={tr('保存一次配置后即可在此回溯。', 'Save once to start tracking versions.')}
          />
        ) : (
          <TableCard>
            <table className="w-full text-left">
              <TableHead>
                <tr>
                  <TableHeadCell>{tr('版本', 'Version')}</TableHeadCell>
                  <TableHeadCell>{tr('保存时间', 'Saved at')}</TableHeadCell>
                  <TableHeadCell>{tr('变更摘要', 'Summary')}</TableHeadCell>
                  <TableHeadCell className="text-right">{tr('操作', 'Actions')}</TableHeadCell>
                </tr>
              </TableHead>
              <tbody>
                {items.map((v) => (
                  <tr key={v.version} className="border-t border-zinc-800/60">
                    <td className="px-3 py-2">
                      <span className="font-medium text-zinc-200">v{v.version}</span>
                      {v.restored_from_version !== null && (
                        <Chip className="ml-2">
                          {tr(`回滚自 v${v.restored_from_version}`, `Rollback of v${v.restored_from_version}`)}
                        </Chip>
                      )}
                    </td>
                    <td className="px-3 py-2 text-zinc-400">
                      {v.created_at ? fullDateTime(v.created_at) : '-'}
                    </td>
                    <td className="px-3 py-2 text-zinc-400">{v.change_summary || '-'}</td>
                    <td className="px-3 py-2 text-right">
                      <RowAction
                        icon={RotateCcw}
                        onClick={() => setConfirm(v)}
                        title={tr(`回滚到版本 ${v.version}`, `Roll back to v${v.version}`)}
                      >
                        {tr('回滚', 'Roll back')}
                      </RowAction>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableCard>
        )}
        <div className="flex justify-end">
          <Button variant="outline" size="sm" onClick={onClose} className="px-3 py-1.5">
            {tr('关闭', 'Close')}
          </Button>
        </div>
      </div>

      {confirm !== null && (
        <ConfirmDialog
          open
          title={tr(`回滚到版本 ${confirm.version}`, `Roll back to v${confirm.version}`)}
          confirmLabel={tr('回滚', 'Roll back')}
          danger={false}
          description={
            <p>
              {tr(
                '回滚会用该版本的配置覆盖当前配置，并立即对所有使用此助理的会话生效。确定继续？',
                'Rolling back overwrites the current config and takes effect for all sessions using this assistant immediately. Continue?',
              )}
            </p>
          }
          onConfirm={async () => {
            try {
              await rollbackAgent(name, confirm.version);
              await load();
              onRolledBack?.();
            } catch (e) {
              setErr(errorText(e));
            } finally {
              setConfirm(null);
            }
          }}
          onClose={() => setConfirm(null)}
        />
      )}
    </Modal>
  );
}

export default AgentVersionHistoryModal;
