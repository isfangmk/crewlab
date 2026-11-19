// 审批规则页 — 工具×助理×风险 的自动放行/需审批/禁用/升级策略。
//
// 骨架（与 docs/UI设计规范.md 一致）：
//   PageHeader   标题 / 说明 + 新建按钮（admin）
//   TableCard    规则表（按判定顺序排列：具体度 > 助理专属 > 优先级）
//   Modal        新建/编辑表单；ConfirmDialog 删除确认
//
// 规则优先于 per-server 审批策略与会话信任；列表顺序即匹配顺序，方便核对。
import { useCallback, useEffect, useState } from 'react';
import { Pencil, Plus, ShieldCheck, Trash2 } from 'lucide-react';
import { useAssistantPage } from '@/lib/assistantPage';
import { usePermissions } from '@/store/me';
import { Modal } from '@/components/Modal';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import {
  Button,
  Checkbox,
  Chip,
  COL,
  EmptyStateCard,
  ErrorBanner,
  Input,
  Label,
  PageHeader,
  RowActionIcon,
  Select,
  TableCard,
  TableHead,
  flash,
  tableLayout,
} from '@/components/ui';
import {
  createApprovalRule,
  deleteApprovalRule,
  listApprovalRules,
  updateApprovalRule,
  type ApprovalRule,
  type ApprovalRuleAction,
  type ApprovalRuleInput,
} from '@/api/approvals';
import { listAgents, type AgentSummary } from '@/api/agents';
import { useI18n } from '@/i18n/locale';
import { errorText } from '@/lib/errors';

type DraftForm = {
  name: string;
  tool_pattern: string;
  agent_id: string;
  risk: string;
  action: ApprovalRuleAction;
  priority: string;
  enabled: boolean;
};

const emptyDraft: DraftForm = {
  name: '',
  tool_pattern: '',
  agent_id: '',
  risk: '',
  action: 'require_approval',
  priority: '0',
  enabled: true,
};

// 三级危险度的中文含义（与 RiskBadge 同一口径）
const RISK_TEXT: Record<string, { zh: string; en: string }> = {
  safe: { zh: '安全', en: 'Safe' },
  mutating: { zh: '变更', en: 'Mutating' },
  dangerous: { zh: '危险', en: 'Dangerous' },
};

function actionChip(action: ApprovalRuleAction, tr: (zh: string, en: string) => string) {
  const map: Record<ApprovalRuleAction, { label: string; tone: 'success' | 'info' | 'danger' | 'warning' }> = {
    auto_approve: { label: tr('自动放行', 'Auto approve'), tone: 'success' },
    require_approval: { label: tr('需审批', 'Approval'), tone: 'info' },
    block: { label: tr('禁用', 'Block'), tone: 'danger' },
    escalate: { label: tr('升级', 'Escalate'), tone: 'warning' },
  };
  const item = map[action];
  return <Chip dense tone={item.tone}>{item.label}</Chip>;
}

export default function ApprovalRulesPage() {
  const { tr } = useI18n();
  const { isAdmin } = usePermissions();
  const [rules, setRules] = useState<ApprovalRule[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  // null = 不在编辑；'create' 或规则 id
  const [editing, setEditing] = useState<'create' | string | null>(null);
  const [draft, setDraft] = useState<DraftForm>(emptyDraft);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState<ApprovalRule | null>(null);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const [ruleResp, agentResp] = await Promise.all([listApprovalRules(), listAgents()]);
      setRules(ruleResp.items ?? []);
      setAgents(agentResp.items ?? []);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchAll();
  }, [fetchAll]);

  useAssistantPage('审批规则', 'Approval rules', {
    rule_count: rules.length,
    loading,
  });

  const openCreate = () => {
    setDraft(emptyDraft);
    setEditing('create');
  };

  const openEdit = (rule: ApprovalRule) => {
    setDraft({
      name: rule.name,
      tool_pattern: rule.tool_pattern,
      agent_id: rule.agent_id ?? '',
      risk: rule.risk ?? '',
      action: rule.action,
      priority: String(rule.priority),
      enabled: rule.enabled,
    });
    setEditing(rule.id);
  };

  const submit = async () => {
    setSaving(true);
    setErr(null);
    try {
      const priority = Number(draft.priority);
      if (!Number.isInteger(priority)) {
        throw new Error(tr('优先级必须是整数', 'Priority must be an integer'));
      }
      const input: ApprovalRuleInput = {
        name: draft.name.trim(),
        tool_pattern: draft.tool_pattern.trim(),
        agent_id: draft.agent_id === '' ? null : draft.agent_id,
        risk: draft.risk === '' ? null : (draft.risk as 'safe' | 'mutating' | 'dangerous'),
        action: draft.action,
        priority,
        enabled: draft.enabled,
      };
      if (editing === 'create') {
        await createApprovalRule(input);
        flash('ok', tr('规则已创建', 'Rule created'));
      } else if (editing !== null) {
        await updateApprovalRule(editing, input);
        flash('ok', tr('规则已更新', 'Rule updated'));
      }
      setEditing(null);
      await fetchAll();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  const confirmDelete = async () => {
    if (!deleting) return;
    try {
      await deleteApprovalRule(deleting.id);
      flash('ok', tr('规则已删除', 'Rule deleted'));
      setDeleting(null);
      await fetchAll();
    } catch (e) {
      setErr(errorText(e));
    }
  };

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tr('审批规则', 'Approval rules')}
        subtitle={tr(
          '按 工具名通配 × 助理 × 风险 匹配，动作优先于服务器策略与会话信任；表格顺序即匹配顺序',
          'Match by tool glob × assistant × risk; rules override server policy and session trust; table order is match order',
        )}
        actions={
          isAdmin ? (
            <Button variant="primary" size="sm" onClick={openCreate}>
              <Plus size={12} />
              {tr('新建规则', 'New rule')}
            </Button>
          ) : undefined
        }
      />

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-6 pb-6 pt-3">
        {err && <ErrorBanner>{err}</ErrorBanner>}

        {loading ? (
          <div className="text-[11.5px] text-zinc-500">{tr('加载中…', 'Loading…')}</div>
        ) : rules.length === 0 ? (
          <EmptyStateCard
            icon={ShieldCheck}
            title={tr('还没有审批规则', 'No approval rules yet')}
            hint={tr(
              '规则用于细调审批闸门：例如让某个高危工具必须管理员批准，或让只读工具免审。',
              'Rules fine-tune the approval gate: force admin approval for a dangerous tool, or auto-approve read-only ones.',
            )}
          />
        ) : (
          <TableCard>
            <table {...tableLayout(52)}>
              <colgroup>
                <col className={COL.main} />
                <col className={COL.detail} />
                <col className={COL.tag} />
                <col className={COL.tag} />
                <col className={COL.tag} />
                <col className={COL.tag} />
                <col className={COL.tag} />
                <col className={COL.actionsIcon} />
              </colgroup>
              <TableHead>
                <tr className="whitespace-nowrap">
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('名称 / 工具匹配', 'Name / tool pattern')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('助理', 'Assistant')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('风险', 'Risk')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('动作', 'Action')}</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">{tr('优先级', 'Priority')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('状态', 'State')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">{tr('顺序', 'Order')}</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">{tr('操作', 'Actions')}</th>
                </tr>
              </TableHead>
              <tbody className="divide-y divide-zinc-800/40">
                {rules.map((rule, index) => (
                  <tr key={rule.id}>
                    <td className="px-3 py-2">
                      <div className="truncate text-[12.5px] text-zinc-100">{rule.name}</div>
                      <code className="font-sans text-[10.5px] text-zinc-500">{rule.tool_pattern}</code>
                    </td>
                    <td className="px-3 py-2 text-[11px] text-zinc-400">
                      {rule.agent_id ?? tr('全部', 'All')}
                    </td>
                    <td className="px-3 py-2 text-[11px] text-zinc-400">
                      {rule.risk ? tr(RISK_TEXT[rule.risk].zh, RISK_TEXT[rule.risk].en) : tr('不限', 'Any')}
                    </td>
                    <td className="px-3 py-2">{actionChip(rule.action, tr)}</td>
                    <td className="px-3 py-2 text-right text-[11.5px] tabular-nums text-zinc-400">{rule.priority}</td>
                    <td className="px-3 py-2">
                      {rule.enabled ? (
                        <Chip dense tone="success">{tr('启用', 'On')}</Chip>
                      ) : (
                        <Chip dense>{tr('停用', 'Off')}</Chip>
                      )}
                    </td>
                    <td className="px-3 py-2 text-[11px] tabular-nums text-zinc-600">#{index + 1}</td>
                    <td className="px-3 py-2 text-right">
                      {isAdmin && (
                        <span className="inline-flex items-center gap-1">
                          <RowActionIcon icon={Pencil} label={tr(`编辑 ${rule.name}`, `Edit ${rule.name}`)} onClick={() => openEdit(rule)} />
                          <RowActionIcon icon={Trash2} label={tr(`删除 ${rule.name}`, `Delete ${rule.name}`)} tone="danger" onClick={() => setDeleting(rule)} />
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableCard>
        )}
      </div>

      {editing !== null && (
        <Modal
          open
          onClose={() => setEditing(null)}
          size="lg"
          title={editing === 'create' ? tr('新建审批规则', 'New approval rule') : tr('编辑审批规则', 'Edit approval rule')}
          footer={
            <>
              <Button variant="outline" size="sm" onClick={() => setEditing(null)}>
                {tr('取消', 'Cancel')}
              </Button>
              <Button variant="primary" size="sm" onClick={() => void submit()} disabled={saving}>
                {saving ? tr('保存中…', 'Saving…') : tr('保存', 'Save')}
              </Button>
            </>
          }
        >
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="rule-name">{tr('名称 *', 'Name *')}</Label>
              <Input
                id="rule-name"
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                placeholder={tr('例：告警确认升级', 'e.g. Alert ack escalation')}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="rule-pattern">{tr('工具匹配（fnmatch 通配）*', 'Tool pattern (fnmatch) *')}</Label>
              <Input
                id="rule-pattern"
                value={draft.tool_pattern}
                onChange={(e) => setDraft({ ...draft, tool_pattern: e.target.value })}
                placeholder={tr('例：mcp__obs-tools__* / *', 'e.g. mcp__obs-tools__* / *')}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="rule-agent">{tr('助理（空 = 全部）', 'Assistant (empty = all)')}</Label>
                <Select
                  aria-label={tr('助理', 'Assistant')}
                  value={draft.agent_id}
                  onValueChange={(v) => setDraft({ ...draft, agent_id: v })}
                  options={[
                    { value: '', label: tr('全部助理', 'All assistants') },
                    ...agents.map((a) => ({ value: a.name, label: `${a.display_name}（${a.name}）` })),
                  ]}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="rule-risk">{tr('风险（空 = 不限）', 'Risk (empty = any)')}</Label>
                <Select
                  aria-label={tr('风险', 'Risk')}
                  value={draft.risk}
                  onValueChange={(v) => setDraft({ ...draft, risk: v })}
                  options={[
                    { value: '', label: tr('不限', 'Any') },
                    { value: 'safe', label: tr('安全', 'Safe') },
                    { value: 'mutating', label: tr('变更', 'Mutating') },
                    { value: 'dangerous', label: tr('危险', 'Dangerous') },
                  ]}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="rule-action">{tr('动作 *', 'Action *')}</Label>
                <Select
                  aria-label={tr('动作', 'Action')}
                  value={draft.action}
                  onValueChange={(v) => setDraft({ ...draft, action: v as ApprovalRuleAction })}
                  options={[
                    { value: 'auto_approve', label: tr('自动放行', 'Auto approve') },
                    { value: 'require_approval', label: tr('需审批', 'Require approval') },
                    { value: 'block', label: tr('禁用', 'Block') },
                    { value: 'escalate', label: tr('升级（仅 admin 可批）', 'Escalate (admin only)') },
                  ]}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="rule-priority">{tr('优先级（大者先）', 'Priority (higher first)')}</Label>
                <Input
                  id="rule-priority"
                  type="number"
                  value={draft.priority}
                  onChange={(e) => setDraft({ ...draft, priority: e.target.value })}
                />
              </div>
            </div>
            <Label className="flex items-center gap-2 text-[11.5px] text-zinc-400">
              <Checkbox
                checked={draft.enabled}
                onCheckedChange={(checked) => setDraft({ ...draft, enabled: checked === true })}
              />
              {tr('启用该规则', 'Rule enabled')}
            </Label>
            <p className="text-[11px] leading-5 text-zinc-500">
              {tr(
                '匹配顺序：工具名越具体越优先 → 助理专属优先于全局 → 优先级大者先。命中规则优先于服务器审批策略与会话信任。',
                'Match order: more specific tool pattern first → assistant-specific before global → higher priority first. Rules override server policy and session trust.',
              )}
            </p>
          </div>
        </Modal>
      )}

      {deleting && (
        <ConfirmDialog
          open
          title={tr('删除审批规则', 'Delete approval rule')}
          description={tr(
            `确定删除「${deleting.name}」（${deleting.tool_pattern}）？删除后立即不再参与匹配。`,
            `Delete "${deleting.name}" (${deleting.tool_pattern})? It stops matching immediately.`,
          )}
          confirmLabel={tr('删除', 'Delete')}
          onClose={() => setDeleting(null)}
          onConfirm={confirmDelete}
        />
      )}
    </main>
  );
}
