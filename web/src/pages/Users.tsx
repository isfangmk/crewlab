import { useAssistantPage } from '@/lib/assistantPage';
import { useCallback, useEffect, useState } from 'react';
import { Plus, RefreshCw, Shield, UserCheck, UserX } from 'lucide-react';
import {
  Button,
  Chip,
  EmptyStateCard,
  Input,
  Label,
  PageHeader,
  RowAction,
  TableCard,
} from '@/components/ui';
import { Select } from '@/components/ui/Select';
import { Modal } from '@/components/Modal';
import { ApiError } from '@/api/client';
import {
  createUser,
  listUsers,
  setUserStatus,
  type ManagedUser,
  type SystemRole,
} from '@/api/users';
import { useI18n } from '@/i18n/locale';
import { usePermissions } from '@/store/me';

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
  const [items, setItems] = useState<ManagedUser[]>([]);
  const [loading, setLoading] = useState(true);
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

  const load = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true);
    else setLoading(true);
    try {
      const rows = await listUsers();
      if (!Array.isArray(rows)) throw new Error('用户列表响应必须是数组');
      setItems(rows);
      setErr(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

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
      setFormErr(e instanceof ApiError ? e.message : (e as Error).message);
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
      setErr(e instanceof ApiError ? e.message : (e as Error).message);
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
              icon={UserCheck}
              title={tr('还没有用户', 'No users yet')}
              hint={tr('本地账号由管理员创建。', 'Local accounts are created by admins.')}
            />
          ) : (
            <TableCard>
              <table className="og-action-table w-full min-w-[52rem] table-fixed text-sm">
                <colgroup>
                  <col className="w-[18rem]" />
                  <col className="w-[12rem]" />
                  <col className="w-[9rem]" />
                  <col className="w-[7rem]" />
                  <col className="w-[8rem]" />
                </colgroup>
                <thead className="sticky top-0 z-10 border-b border-zinc-800/60 bg-zinc-900/95 text-[11px] text-zinc-500 backdrop-blur">
                  <tr className="whitespace-nowrap">
                    <th className="px-3 py-2 text-left font-medium">{tr('邮箱', 'Email')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('显示名', 'Display name')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('角色', 'Role')}</th>
                    <th className="px-3 py-2 text-left font-medium">{tr('状态', 'Status')}</th>
                    <th className="px-3 py-2 text-right font-medium">{tr('操作', 'Actions')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/40">
                  {items.map((user) => {
                    const meta = roleMeta(user.role);
                    const busy = busyId === user.id;
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
                          <div className="flex items-center justify-end">
                            <RowAction
                              icon={user.status === 'active' ? UserX : UserCheck}
                              tone={user.status === 'active' ? 'danger' : 'positive'}
                              disabled={busy}
                              onClick={() => void toggleStatus(user)}
                            >
                              {user.status === 'active' ? tr('禁用', 'Disable') : tr('启用', 'Enable')}
                            </RowAction>
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
            <div role="alert" className="rounded-lg border border-red-500/40 bg-red-500/5 px-3 py-2 text-[12px] text-red-300">
              {formErr}
            </div>
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
    </main>
  );
}
