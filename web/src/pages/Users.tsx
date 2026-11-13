import { useAssistantPage } from '@/lib/assistantPage';
import { useCallback, useEffect, useState } from 'react';
import { KeyRound, Plus, RefreshCw, Shield, Trash2, UserCheck, UserCog, UserX } from 'lucide-react';
import {
  Button,
  Chip,
  EmptyStateCard,
  ErrorBanner,
  Input,
  Label,
  PageHeader,
  RowAction,
  RowActionIcon,
  TableCard,
  TableHead,
} from '@/components/ui';
import { Hint } from '@/components/ui/Tooltip';
import { Select } from '@/components/ui/Select';
import { useRequestSeq } from '@/lib/requestSeq';
import { Modal } from '@/components/Modal';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import {
  createUser,
  deleteUser,
  listUsers,
  resetUserPassword,
  setUserStatus,
  updateUserRole,
  type ManagedUser,
  type SystemRole,
} from '@/api/users';
import { useI18n } from '@/i18n/locale';
import { usePermissions, useMe } from '@/store/me';
import { errorText } from '@/lib/errors';

// 用户与权限：本地账号的建号、角色与启停用；仅管理员可进入。
// 角色与后端 auth_service._ROLES 对齐：admin / developer / viewer。

const ROLE_OPTIONS: { value: SystemRole; zh: string; en: string; hintZh: string; hintEn: string }[] = [
  {
    value: 'admin',
    zh: '管理员',
    en: 'Admin',
    hintZh: '全部功能 + 平台配置 + 用户管理',
    hintEn: 'Everything, including platform config and user management',
  },
  {
    value: 'developer',
    zh: '开发者',
    en: 'Developer',
    hintZh: '业务功能全量，配置与用户管理只读',
    hintEn: 'Full business features; config and user management are read-only',
  },
  {
    value: 'viewer',
    zh: '只读',
    en: 'Viewer',
    hintZh: '只能提问，AI 仅调用只读工具',
    hintEn: 'Can ask questions; the AI only runs read-only tools',
  },
];

function roleMeta(role: SystemRole): { zh: string; en: string } {
  const found = ROLE_OPTIONS.find((r) => r.value === role);
  if (!found) throw new Error(`未知角色: ${role}`);
  return { zh: found.zh, en: found.en };
}

export default function UsersPage() {
  const { tr } = useI18n();
  const { isAdmin } = usePermissions();
  const { me } = useMe();
  const [items, setItems] = useState<ManagedUser[]>([]);
  const [loading, setLoading] = useState(true);
  const reqSeq = useRequestSeq();
  const [refreshing, setRefreshing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [formBusy, setFormBusy] = useState(false);
  const [formErr, setFormErr] = useState<string | null>(null);
  const [email, setEmail] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<SystemRole>('developer');
  const [roleTarget, setRoleTarget] = useState<ManagedUser | null>(null);
  const [roleDraft, setRoleDraft] = useState<SystemRole>('developer');
  const [roleBusy, setRoleBusy] = useState(false);
  const [roleErr, setRoleErr] = useState<string | null>(null);
  const [resetTarget, setResetTarget] = useState<ManagedUser | null>(null);
  // 删除账号走统一确认弹窗，与助理/会话的删除版式一致
  const [deleteTarget, setDeleteTarget] = useState<ManagedUser | null>(null);
  const [resetPassword, setResetPassword] = useState('');
  const [resetBusy, setResetBusy] = useState(false);
  const [resetErr, setResetErr] = useState<string | null>(null);

  const load = useCallback(async (silent = false) => {
    const current = reqSeq.next();
    if (silent) setRefreshing(true);
    else setLoading(true);
    try {
      const rows = await listUsers();
      if (reqSeq.isStale(current)) return;
      if (!Array.isArray(rows)) throw new Error('用户列表响应必须是数组');
      setItems(rows);
      setErr(null);
    } catch (e) {
      if (reqSeq.isStale(current)) return;
      setErr(errorText(e));
    } finally {
      if (!reqSeq.isStale(current)) {
        setLoading(false);
              setRefreshing(false);
      }
    }
  }, [reqSeq]);

  useEffect(() => {
    if (isAdmin) void load();
  }, [isAdmin, load]);

  useAssistantPage('用户与权限', 'Users & roles', {
    total: items.length,
    role,
    loading,
  });

  const resetForm = () => {
    setEmail('');
    setDisplayName('');
    setPassword('');
    setRole('developer');
    setFormErr(null);
  };

  const submitForm = async () => {
    if (email.trim() === '') throw new Error(tr('邮箱不能为空', 'Email is required'));
    if (displayName.trim() === '') throw new Error(tr('显示名不能为空', 'Display name is required'));
    if (password === '') throw new Error(tr('初始密码不能为空', 'Initial password is required'));
    setFormBusy(true);
    setFormErr(null);
    try {
      const created = await createUser({
        email: email.trim(),
        display_name: displayName.trim(),
        password,
        role,
      });
      setItems((prev) => [...prev, created].sort((a, b) => a.id - b.id));
      setFormOpen(false);
      resetForm();
    } catch (e) {
      setFormErr(errorText(e));
    } finally {
      setFormBusy(false);
    }
  };

  const toggleStatus = async (user: ManagedUser) => {
    const next = user.status === 'active' ? 'disabled' : 'active';
    setBusyId(user.id);
    try {
      const updated = await setUserStatus(user.id, next);
      setItems((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
      setErr(null);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusyId(null);
    }
  };

  const openRole = (user: ManagedUser) => {
    setRoleTarget(user);
    setRoleDraft(user.role);
    setRoleErr(null);
  };

  const submitRole = async () => {
    if (roleTarget === null) return;
    setRoleBusy(true);
    setRoleErr(null);
    try {
      const updated = await updateUserRole(roleTarget.id, roleDraft);
      setItems((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
      setRoleTarget(null);
    } catch (e) {
      setRoleErr(errorText(e));
    } finally {
      setRoleBusy(false);
    }
  };

  const openReset = (user: ManagedUser) => {
    setResetTarget(user);
    setResetPassword('');
    setResetErr(null);
  };

  const submitReset = async () => {
    if (resetTarget === null) return;
    if (resetPassword === '') {
      setResetErr(tr('新密码不能为空', 'New password is required'));
      return;
    }
    setResetBusy(true);
    setResetErr(null);
    try {
      await resetUserPassword(resetTarget.id, resetPassword);
      setResetTarget(null);
      setResetPassword('');
    } catch (e) {
      setResetErr(errorText(e));
    } finally {
      setResetBusy(false);
    }
  };

  const removeUser = async () => {
    if (deleteTarget === null) return;
    setBusyId(deleteTarget.id);
    try {
      await deleteUser(deleteTarget.id);
      setItems((prev) => prev.filter((u) => u.id !== deleteTarget.id));
      setErr(null);
      setDeleteTarget(null);
    } finally {
      setBusyId(null);
    }
  };

  if (!isAdmin) {
    return (
      <main className="anim-fade flex flex-1 flex-col overflow-hidden">
        <PageHeader title={tr('用户与权限', 'Users & roles')} subtitle={tr('本地账号与角色', 'Local accounts and roles')} />
        <div className="flex-1 overflow-y-auto px-6 py-6">
          <div className="mx-auto max-w-3xl">
            <EmptyStateCard
              icon={Shield}
              title={tr('需要管理员权限', 'Admin permission required')}
              hint={tr('用户管理只对管理员开放。', 'User management is admin-only.')}
            />
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tr('用户与权限', 'Users & roles')}
        subtitle={tr('本地账号、角色与启停用', 'Local accounts, roles, and activation')}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="primary"
              size="sm"
              type="button"
              onClick={() => {
                resetForm();
                setFormOpen(true);
              }}
              className="inline-flex items-center gap-1.5"
            >
              <Plus size={13} />
              {tr('新建用户', 'New user')}
            </Button>
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

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden px-6 py-4">
        {err && (
          <ErrorBanner>
            {err}
          </ErrorBanner>
        )}
        <div className="flex min-h-0 flex-1 flex-col">
          {loading ? (
            <div className="flex h-40 items-center justify-center text-sm text-zinc-500">
              {tr('加载中…', 'Loading…')}
            </div>
          ) : items.length === 0 ? (
            <EmptyStateCard
              icon={UserCheck}
              title={tr('还没有用户', 'No users yet')}
              hint={tr('本地账号由管理员创建。', 'Local accounts are created by admins.')}
            />
          ) : (
            <TableCard>
              <table className="og-action-table w-full min-w-[56rem] table-fixed text-sm">
                <colgroup>
                  <col className="w-[18rem]" />
                  <col className="w-[12rem]" />
                  <col className="w-[9rem]" />
                  <col className="w-[7rem]" />
                  <col className="w-[13rem]" />
                </colgroup>
                <TableHead>
                  <tr className="whitespace-nowrap">
                    <th className="px-3 py-2 text-left font-medium">{tr('邮箱', 'Email')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('显示名', 'Display name')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('角色', 'Role')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('状态', 'Status')}</th>
                    <th className="px-3 py-2 text-right font-medium">{tr('操作', 'Actions')}</th>
                  </tr>
                </TableHead>
                <tbody className="divide-y divide-zinc-800/40">
                  {items.map((user) => {
                    const meta = roleMeta(user.role);
                    const busy = busyId === user.id;
                    const isSelf = me?.email === user.email;
                    return (
                      <tr key={user.id} className="transition-colors">
                        <td className="min-w-0 px-3 py-2">
                          <span className="block truncate text-[13px] text-zinc-100" title={user.email}>
                            {user.email}
                          </span>
                        </td>
                        <td className="min-w-0 px-3 py-2">
                          <span className="block truncate text-[12px] text-zinc-300" title={user.display_name}>
                            {user.display_name}
                          </span>
                        </td>
                        <td className="px-3 py-2">
                          <Chip dense tone={user.role === 'admin' ? 'accent' : 'default'}>
                            {tr(meta.zh, meta.en)}
                          </Chip>
                        </td>
                        <td className="px-3 py-2">
                          <span
                            className={
                              user.status === 'active'
                                ? 'text-[12px] text-emerald-400'
                                : 'text-[12px] text-zinc-500'
                            }
                          >
                            {user.status === 'active' ? tr('启用', 'Active') : tr('已禁用', 'Disabled')}
                          </span>
                        </td>
                        <td className="px-3 py-2">
                          <div className="flex items-center justify-end gap-1">
                            <RowAction
                              icon={UserCog}
                              disabled={busy || isSelf}
                              onClick={() => openRole(user)}
                            >
                              {tr('改角色', 'Change role')}
                            </RowAction>
                            <Hint content={tr('重置密码', 'Reset password')}>
                              <RowActionIcon
                                icon={KeyRound}
                                label={tr('重置密码', 'Reset password')}
                                disabled={busy}
                                onClick={() => openReset(user)}
                              />
                            </Hint>
                            <RowAction
                              icon={user.status === 'active' ? UserX : UserCheck}
                              tone={user.status === 'active' ? 'danger' : 'positive'}
                              disabled={busy}
                              onClick={() => void toggleStatus(user)}
                            >
                              {user.status === 'active' ? tr('禁用', 'Disable') : tr('启用', 'Enable')}
                            </RowAction>
                            <Hint
                              content={
                                isSelf
                                  ? tr('不能删除当前登录账号', 'Cannot delete the signed-in account')
                                  : tr('删除账号', 'Delete account')
                              }
                            >
                              <RowActionIcon
                                icon={Trash2}
                                label={tr('删除账号', 'Delete account')}
                                tone="danger"
                                disabled={busy || isSelf}
                                onClick={() => setDeleteTarget(user)}
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
      </div>

      <Modal
        open={formOpen}
        onClose={() => {
          if (formBusy) return;
          setFormOpen(false);
        }}
        title={tr('新建用户', 'New user')}
        footer={
          <>
            <Button variant="ghost" size="sm" type="button" disabled={formBusy} onClick={() => setFormOpen(false)}>
              {tr('取消', 'Cancel')}
            </Button>
            <Button variant="primary" size="sm" type="button" disabled={formBusy} onClick={() => void submitForm()}>
              {formBusy ? tr('创建中…', 'Creating…') : tr('创建', 'Create')}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          {formErr && (
            <ErrorBanner>
              {formErr}
            </ErrorBanner>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="user-email">{tr('邮箱', 'Email')}</Label>
            <Input
              id="user-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="name@example.com"
              autoFocus
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="user-name">{tr('显示名', 'Display name')}</Label>
            <Input id="user-name" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="user-password">{tr('初始密码', 'Initial password')}</Label>
            <Input
              id="user-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="user-role">{tr('角色', 'Role')}</Label>
            <Select
              aria-label={tr('角色', 'Role')}
              value={role}
              onValueChange={(v) => setRole(v as SystemRole)}
              options={ROLE_OPTIONS.map((r) => ({ value: r.value, label: tr(r.zh, r.en) }))}
            />
            <p className="text-[11px] text-zinc-500">
              {tr(
                ROLE_OPTIONS.find((r) => r.value === role)?.hintZh ?? '',
                ROLE_OPTIONS.find((r) => r.value === role)?.hintEn ?? '',
              )}
            </p>
          </div>
        </div>
      </Modal>

      <Modal
        open={roleTarget !== null}
        onClose={() => {
          if (roleBusy) return;
          setRoleTarget(null);
        }}
        title={tr('修改角色', 'Change role')}
        footer={
          <>
            <Button
              variant="ghost"
              size="sm"
              type="button"
              disabled={roleBusy}
              onClick={() => setRoleTarget(null)}
            >
              {tr('取消', 'Cancel')}
            </Button>
            <Button
              variant="primary"
              size="sm"
              type="button"
              disabled={roleBusy}
              onClick={() => void submitRole()}
            >
              {roleBusy ? tr('保存中…', 'Saving…') : tr('保存', 'Save')}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          {roleErr && (
            <ErrorBanner>
              {roleErr}
            </ErrorBanner>
          )}
          <p className="text-[12px] text-zinc-400">
            {tr(
              `账号：${roleTarget?.email ?? ''}`,
              `Account: ${roleTarget?.email ?? ''}`,
            )}
          </p>
          <div className="space-y-1.5">
            <Label htmlFor="role-select">{tr('角色', 'Role')}</Label>
            <Select
              aria-label={tr('角色', 'Role')}
              value={roleDraft}
              onValueChange={(v) => setRoleDraft(v as SystemRole)}
              options={ROLE_OPTIONS.map((r) => ({ value: r.value, label: tr(r.zh, r.en) }))}
            />
            <p className="text-[11px] text-zinc-500">
              {tr(
                ROLE_OPTIONS.find((r) => r.value === roleDraft)?.hintZh ?? '',
                ROLE_OPTIONS.find((r) => r.value === roleDraft)?.hintEn ?? '',
              )}
            </p>
          </div>
        </div>
      </Modal>

      <Modal
        open={resetTarget !== null}
        onClose={() => {
          if (resetBusy) return;
          setResetTarget(null);
        }}
        title={tr('重置密码', 'Reset password')}
        footer={
          <>
            <Button
              variant="ghost"
              size="sm"
              type="button"
              disabled={resetBusy}
              onClick={() => setResetTarget(null)}
            >
              {tr('取消', 'Cancel')}
            </Button>
            <Button
              variant="primary"
              size="sm"
              type="button"
              disabled={resetBusy}
              onClick={() => void submitReset()}
            >
              {resetBusy ? tr('重置中…', 'Resetting…') : tr('重置', 'Reset')}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          {resetErr && (
            <ErrorBanner>
              {resetErr}
            </ErrorBanner>
          )}
          <p className="text-[12px] text-zinc-400">
            {tr(
              `账号：${resetTarget?.email ?? ''}`,
              `Account: ${resetTarget?.email ?? ''}`,
            )}
          </p>
          <div className="space-y-1.5">
            <Label htmlFor="reset-password">{tr('新密码', 'New password')}</Label>
            <Input
              id="reset-password"
              type="password"
              value={resetPassword}
              onChange={(e) => setResetPassword(e.target.value)}
              autoFocus
            />
            <p className="text-[11px] text-zinc-500">
              {tr(
                '重置后该账号的全部登录状态立即失效，需要用新密码重新登录。',
                'All sessions for this account are revoked immediately; sign in with the new password.',
              )}
            </p>
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={deleteTarget !== null}
        title={tr('删除账号', 'Delete account')}
        description={
          <>
            <p>
              {tr('确定删除账号 ', 'Delete ')}
              <span className="font-mono text-zinc-100">{deleteTarget?.email ?? ''}</span>
              {tr(' ？', '?')}
            </p>
            <p className="mt-2 text-zinc-500">
              {tr('该操作不可撤销。', 'This cannot be undone.')}
            </p>
          </>
        }
        onConfirm={removeUser}
        onClose={() => setDeleteTarget(null)}
      />
    </main>
  );
}
