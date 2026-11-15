// 用量与成本看板 — observation_turns 的日/模型/助理聚合 + 手动单价估算。
//
// 骨架（与 docs/UI设计规范.md 一致）：
//   PageHeader   标题 / 说明
//   FilterBar    时间范围 + 模型 / 助理 / 渠道筛选；软限额设置（admin）
//   MetricCard   回合数 / tokens / 估算成本 / 未计价 / 平均耗时
//   ChartCanvas  日消耗（tokens 柱 + 成本线；无价目日留空，不画假点）
//   TableCard    按模型 / 按助理 明细
//
// 成本口径：只认「模型配置」里手填的每 1M token 单价；缺价目显示「—」与
// 未计价回合数，绝不按 0 计。软限额只提醒不拦截。
import { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Coins,
  Hash,
  Timer,
} from 'lucide-react';
import { useAssistantPage } from '@/lib/assistantPage';
import { usePermissions } from '@/store/me';
import {
  Button,
  COL,
  Chip,
  EmptyStateCard,
  ErrorBanner,
  FilterBar,
  FilterPill,
  Input,
  MetricCard,
  PageHeader,
  Select,
  TableCard,
  TableHead,
  flash,
  tableLayout,
} from '@/components/ui';
import { ChartCanvas, CHART_HEIGHT_CLASS } from '@/components/ChartCanvas';
import { getUsageSummary, type UsageRow, type UsageSummary } from '@/api/usage';
import { setSetting } from '@/api/settings';
import { rangeForPreset, type TimePreset } from '@/lib/timeRange';
import { formatNumber } from '@/lib/format';
import { useI18n } from '@/i18n/locale';
import { errorText } from '@/lib/errors';

// 成本列口径：null = 有未计价回合，给「—」而不是半真合计
function formatCost(row: { cost: number | null }): string {
  return row.cost === null ? '—' : row.cost.toFixed(2);
}

function formatDuration(ms: number | null): string {
  return ms === null ? '—' : `${(ms / 1000).toFixed(1)}s`;
}

// 软限额超限提示条：警告语义（不是错误），提醒但不拦截
function LimitBanner({ today, limit, tr }: { today: number; limit: number; tr: (zh: string, en: string) => string }) {
  return (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-lg border border-warn/30 bg-warn/10 px-3 py-2 text-[11.5px] leading-5 text-warn"
    >
      <AlertTriangle size={12} className="mt-0.5 shrink-0" aria-hidden />
      <span className="min-w-0 flex-1 break-words">
        {tr(
          `今日 token 已达软限额（${formatNumber(today)} / ${formatNumber(limit)}），只提醒不拦截。`,
          `Today's tokens reached the soft limit (${formatNumber(today)} / ${formatNumber(limit)}); warning only.`,
        )}
      </span>
    </div>
  );
}

function UsageTable({
  title,
  rows,
  keyLabel,
  tr,
}: {
  title: string;
  rows: UsageRow[];
  keyLabel: string;
  tr: (zh: string, en: string) => string;
}) {
  return (
    <div>
      <p className="mb-1.5 text-[11px] text-zinc-500">{title}</p>
      <TableCard>
        <table {...tableLayout(32)}>
          <colgroup>
            <col className={COL.main} />
            <col className={COL.tag} />
            <col className={COL.tag} />
            <col className={COL.tag} />
            <col className={COL.tag} />
          </colgroup>
          <TableHead>
            <tr className="whitespace-nowrap">
              <th scope="col" className="px-3 py-2 text-left font-medium">{keyLabel}</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">{tr('回合', 'Turns')}</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">{tr('总 tokens', 'Tokens')}</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">{tr('估算成本', 'Est. cost')}</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">{tr('未计价', 'Unpriced')}</th>
            </tr>
          </TableHead>
          <tbody className="divide-y divide-zinc-800/40">
            {rows.map((row) => (
              <tr key={row.key}>
                <td className="max-w-[14rem] truncate px-3 py-2 text-[12px] text-zinc-200" title={row.key}>
                  {row.key}
                </td>
                <td className="px-3 py-2 text-right text-[11.5px] tabular-nums text-zinc-400">{formatNumber(row.turns)}</td>
                <td className="px-3 py-2 text-right text-[11.5px] tabular-nums text-zinc-300">{formatNumber(row.total_tokens)}</td>
                <td className="px-3 py-2 text-right text-[11.5px] tabular-nums text-zinc-300">{formatCost(row)}</td>
                <td className="px-3 py-2 text-right text-[11.5px] tabular-nums">
                  {row.unpriced_turns > 0 ? (
                    <Chip dense tone="warning">{formatNumber(row.unpriced_turns)}</Chip>
                  ) : (
                    <span className="text-zinc-600">0</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableCard>
    </div>
  );
}

export default function UsagePage() {
  const { tr } = useI18n();
  const { isAdmin } = usePermissions();
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [preset, setPreset] = useState<TimePreset>('30d');
  const [model, setModel] = useState('');
  const [agent, setAgent] = useState('');
  const [channel, setChannel] = useState('');
  // 软限额输入（仅 admin 可改）；空 = 不限
  const [limitText, setLimitText] = useState('');
  const [limitSaved, setLimitSaved] = useState(true);
  const [savingLimit, setSavingLimit] = useState(false);

  const fetchSummary = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const range = rangeForPreset(preset);
      const res = await getUsageSummary({
        started_from: range.from,
        started_to: range.to,
        model: model || undefined,
        agent_id: agent || undefined,
        channel: channel || undefined,
      });
      setSummary(res);
      setLimitText(res.soft_limit_tokens === null ? '' : String(res.soft_limit_tokens));
      setLimitSaved(true);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setLoading(false);
    }
  }, [preset, model, agent, channel]);

  useEffect(() => {
    void fetchSummary();
  }, [fetchSummary]);

  useAssistantPage('用量与成本', 'Usage & cost', {
    preset,
    model: model || 'all',
    agent: agent || 'all',
    loading,
  });

  const saveLimit = async () => {
    setSavingLimit(true);
    setErr(null);
    try {
      const raw = limitText.trim();
      if (raw !== '' && (!/^\d+$/.test(raw) || Number(raw) < 1)) {
        throw new Error(tr('软限额必须是 >= 1 的整数（空 = 不限）', 'Soft limit must be an integer >= 1 (empty = unlimited)'));
      }
      await setSetting('usage', 'daily_token_soft_limit', raw);
      setLimitSaved(true);
      flash('ok', tr('软限额已保存', 'Soft limit saved'));
      await fetchSummary();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setSavingLimit(false);
    }
  };

  // 日消耗图：tokens 柱 + 成本线；成本 null 的日子留空档，不画假点
  const chartOption = summary && summary.by_day.length > 0
    ? {
        tooltip: { trigger: 'axis' },
        legend: { data: [tr('总 tokens', 'Tokens'), tr('估算成本', 'Est. cost')] },
        xAxis: { type: 'category', data: summary.by_day.map((d) => d.key) },
        yAxis: [
          { type: 'value', name: tr('tokens', 'tokens') },
          { type: 'value', name: tr('成本', 'cost') },
        ],
        series: [
          {
            name: tr('总 tokens', 'Tokens'),
            type: 'bar',
            data: summary.by_day.map((d) => d.total_tokens),
          },
          {
            name: tr('估算成本', 'Est. cost'),
            type: 'line',
            yAxisIndex: 1,
            showSymbol: false,
            data: summary.by_day.map((d) => d.cost),
          },
        ],
      }
    : null;

  const stats = summary?.stats;
  const overLimit =
    summary !== null &&
    summary.soft_limit_tokens !== null &&
    summary.today_tokens >= summary.soft_limit_tokens;

  return (
    <main className="anim-fade flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={tr('用量与成本', 'Usage & cost')}
        subtitle={tr(
          '按日 / 模型 / 助理汇总 token 用量；成本按「模型配置」里手填的单价估算，未定价显示「—」',
          'Token usage by day / model / assistant; cost estimated from manually configured prices, unpriced shown as "—"',
        )}
      />

      <FilterBar
        className="mx-6 mt-3"
        footer={
          isAdmin ? (
            <>
              <span className="text-[11px] text-zinc-500">
                {tr('日 token 软限额（超限只提醒不拦截）', 'Daily token soft limit (warning only)')}
              </span>
              <Input
                type="number"
                min={1}
                value={limitText}
                onChange={(e) => {
                  setLimitText(e.target.value);
                  setLimitSaved(false);
                }}
                placeholder={tr('不限', 'Unlimited')}
                aria-label={tr('日 token 软限额', 'Daily token soft limit')}
                className="h-7 w-28 text-[12px]"
              />
              <Button
                size="sm"
                variant="outline"
                onClick={() => void saveLimit()}
                disabled={savingLimit || limitSaved}
              >
                {savingLimit ? tr('保存中…', 'Saving…') : tr('保存限额', 'Save limit')}
              </Button>
            </>
          ) : null
        }
      >
        <FilterPill
          label={tr('时间范围', 'Range')}
          active={preset !== 'all'}
          onClear={() => setPreset('all')}
          width="w-[7.5rem]"
        >
          <Select
            aria-label={tr('时间范围', 'Range')}
            value={preset}
            onValueChange={(v) => setPreset(v as TimePreset)}
            options={[
              { value: 'today', label: tr('今天', 'Today') },
              { value: '7d', label: tr('近 7 天', 'Last 7 days') },
              { value: '30d', label: tr('近 30 天', 'Last 30 days') },
              { value: 'all', label: tr('全部', 'All') },
            ]}
            variant="ghost"
            className="h-7 min-w-0 border-0 bg-transparent px-0 text-[12px] shadow-none"
          />
        </FilterPill>
        <FilterPill
          label={tr('模型', 'Model')}
          active={model !== ''}
          onClear={() => setModel('')}
          width="w-[10rem]"
        >
          <Select
            aria-label={tr('模型', 'Model')}
            value={model}
            onValueChange={setModel}
            options={[
              { value: '', label: tr('全部模型', 'All models') },
              ...(summary?.facets.models ?? []).map((m) => ({ value: m, label: m })),
            ]}
            variant="ghost"
            className="h-7 min-w-0 border-0 bg-transparent px-0 text-[12px] shadow-none"
          />
        </FilterPill>
        <FilterPill
          label={tr('助理', 'Assistant')}
          active={agent !== ''}
          onClear={() => setAgent('')}
          width="w-[10rem]"
        >
          <Select
            aria-label={tr('助理', 'Assistant')}
            value={agent}
            onValueChange={setAgent}
            options={[
              { value: '', label: tr('全部助理', 'All assistants') },
              ...(summary?.facets.agents ?? []).map((a) => ({ value: a, label: a })),
            ]}
            variant="ghost"
            className="h-7 min-w-0 border-0 bg-transparent px-0 text-[12px] shadow-none"
          />
        </FilterPill>
        <FilterPill
          label={tr('渠道', 'Channel')}
          active={channel !== ''}
          onClear={() => setChannel('')}
          width="w-[9rem]"
        >
          <Select
            aria-label={tr('渠道', 'Channel')}
            value={channel}
            onValueChange={setChannel}
            options={[
              { value: '', label: tr('全部渠道', 'All channels') },
              ...(summary?.facets.channels ?? []).map((c) => ({
                value: c,
                label: c === '' ? tr('站内对话', 'In-app') : c,
              })),
            ]}
            variant="ghost"
            className="h-7 min-w-0 border-0 bg-transparent px-0 text-[12px] shadow-none"
          />
        </FilterPill>
      </FilterBar>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 pb-6 pt-3">
        {err && <ErrorBanner>{err}</ErrorBanner>}
        {overLimit && summary && summary.soft_limit_tokens !== null && (
          <LimitBanner today={summary.today_tokens} limit={summary.soft_limit_tokens} tr={tr} />
        )}

        {summary && stats ? (
          stats.turns === 0 ? (
            <EmptyStateCard
              icon={Hash}
              title={tr('该范围内没有对话回合', 'No turns in this range')}
              hint={tr('换个时间范围或筛选条件试试。', 'Try another range or filter.')}
            />
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-7">
                <MetricCard icon={Hash} label={tr('回合数', 'Turns')} value={formatNumber(stats.turns)} />
                <MetricCard
                  icon={ArrowDown}
                  label={tr('输入 tokens', 'Prompt tokens')}
                  value={formatNumber(stats.prompt_tokens)}
                />
                <MetricCard
                  icon={ArrowUp}
                  label={tr('输出 tokens', 'Completion tokens')}
                  value={formatNumber(stats.completion_tokens)}
                />
                <MetricCard icon={Coins} label={tr('总 tokens', 'Total tokens')} value={formatNumber(stats.total_tokens)} />
                <MetricCard
                  icon={Coins}
                  label={tr('估算成本', 'Est. cost')}
                  value={formatCost(stats)}
                  tone={stats.cost === null ? 'warn' : 'accent'}
                  hint={
                    stats.unpriced_turns > 0
                      ? tr(`${formatNumber(stats.unpriced_turns)} 回合未计价`, `${formatNumber(stats.unpriced_turns)} unpriced turns`)
                      : tr('按手动单价估算', 'manual prices')
                  }
                />
                <MetricCard
                  icon={AlertTriangle}
                  label={tr('未计价回合', 'Unpriced turns')}
                  value={formatNumber(stats.unpriced_turns)}
                  tone={stats.unpriced_turns > 0 ? 'warn' : 'default'}
                  hint={tr('缺手动单价', 'no manual price')}
                />
                <MetricCard
                  icon={Timer}
                  label={tr('平均耗时', 'Avg duration')}
                  value={formatDuration(stats.duration_avg_ms)}
                />
              </div>

              {chartOption && (
                <TableCard className={CHART_HEIGHT_CLASS}>
                  <div className={CHART_HEIGHT_CLASS}>
                    <ChartCanvas option={chartOption} className="h-full" />
                  </div>
                </TableCard>
              )}

              <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                <UsageTable
                  title={tr('按模型', 'By model')}
                  keyLabel={tr('模型', 'Model')}
                  rows={summary.by_model}
                  tr={tr}
                />
                <UsageTable
                  title={tr('按助理', 'By assistant')}
                  keyLabel={tr('助理', 'Assistant')}
                  rows={summary.by_agent}
                  tr={tr}
                />
              </div>
            </>
          )
        ) : loading ? (
          <div className="text-[11.5px] text-zinc-500">{tr('加载中…', 'Loading…')}</div>
        ) : null}
      </div>
    </main>
  );
}
