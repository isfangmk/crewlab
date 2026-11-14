import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { FileText } from 'lucide-react';
import { fetchEvalShare, type EvalShareReport } from '@/api/evals';
import { CASE_STATUS_META, RUN_STATUS_META } from '@/lib/evalStatus';
import {
  Chip,
  DetailSection,
  DetailStat,
  DetailStatGrid,
  EmptyStateCard,
  ErrorBanner,
  PageHeader,
  StatusPill,
  TableHead,
} from '@/components/ui';
import { useI18n } from '@/i18n/locale';
import { fullDateTime } from '@/lib/format';
import { errorText } from '@/lib/errors';

/** 评分均分：与评测页同口径，parse 解析失败行不计入。 */
function judgeAvg(items: Array<{ dimension: string; score: number }> | undefined): number | null {
  const dims = (items ?? []).filter((item) => item.dimension !== 'parse');
  if (dims.length === 0) return null;
  return dims.reduce((sum, item) => sum + item.score, 0) / dims.length;
}

/** 评测报告只读分享页：无需登录，载荷已在服务端脱敏。 */
export default function EvalShareViewPage() {
  const { token = '' } = useParams<{ token: string }>();
  const { tr } = useI18n();
  const [report, setReport] = useState<EvalShareReport | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await fetchEvalShare(token);
        if (!cancelled) setReport(data);
      } catch (e) {
        if (!cancelled) setErr(errorText(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  const run = report?.run ?? null;
  const runMeta = run ? RUN_STATUS_META[run.status] : null;
  const gate = report?.gate ?? null;
  const consistency = report?.consistency ?? null;

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-4xl flex-col">
      <PageHeader
        title={report?.dataset.name ?? tr('评测报告', 'Eval report')}
        subtitle={
          <>
            {tr('只读分享报告', 'Read-only shared report')}
            {run && (
              <span className="ml-2 font-sans text-[10px] text-zinc-500">{run.run_uid}</span>
            )}
          </>
        }
      />
      <div className="space-y-5 px-6 py-6">
        {err && <ErrorBanner>{tr(`报告加载失败：${err}`, `Failed to load report: ${err}`)}</ErrorBanner>}
        {!err && report === null && (
          <EmptyStateCard
            icon={FileText}
            title={tr('加载中…', 'Loading…')}
            hint={tr('正在获取分享报告。', 'Fetching the shared report.')}
          />
        )}
        {report !== null && run !== null && (
          <>
            <div className="flex flex-wrap items-center gap-2">
              {runMeta && <StatusPill tone={runMeta.tone} label={tr(runMeta.zh, runMeta.en)} />}
              <span className="text-[11px] text-zinc-300">
                {tr(
                  `通过 ${run.passed_cases}/${run.total_cases}`,
                  `${run.passed_cases}/${run.total_cases} passed`,
                )}
              </span>
              <span className="text-[11px] text-zinc-500">
                {run.agent_id} · {run.provider} · {run.model}
              </span>
              <span className="ml-auto text-[10px] text-zinc-500">
                {tr('链接过期时间：', 'Link expires: ')}
                {fullDateTime(report.expires_at)}
              </span>
            </div>

            <DetailStatGrid>
              <DetailStat
                label={tr('通过率', 'Pass rate')}
                value={
                  run.total_cases > 0
                    ? `${Math.round((run.passed_cases / run.total_cases) * 100)}%`
                    : '—'
                }
              />
              <DetailStat
                label={tr('通过 / 失败', 'Passed / failed')}
                value={`${run.passed_cases} / ${run.failed_cases}`}
              />
              <DetailStat
                label={tr('累计 Tokens', 'Total tokens')}
                value={report.results.reduce((sum, item) => sum + (item.total_tokens ?? 0), 0).toLocaleString()}
              />
              <DetailStat
                label={tr('总耗时', 'Duration')}
                value={`${(report.results.reduce((sum, item) => sum + (item.duration_ms ?? 0), 0) / 1000).toFixed(1)}s`}
              />
            </DetailStatGrid>

            {gate && (
              <DetailSection title={tr('门禁结论', 'Gate verdict')}>
                <div className="flex flex-wrap items-center gap-2">
                  <StatusPill
                    tone={gate.pass ? 'ok' : 'danger'}
                    label={gate.pass ? tr('通过', 'Pass') : tr('未通过', 'Fail')}
                  />
                  <span className="text-[11px] text-zinc-400">
                    {tr(
                      `通过率 ${(gate.pass_rate * 100).toFixed(0)}% · 回归 ${gate.regressions} · 改善 ${gate.improved}`,
                      `Pass rate ${(gate.pass_rate * 100).toFixed(0)}% · ${gate.regressions} regressed · ${gate.improved} improved`,
                    )}
                  </span>
                </div>
                {gate.reasons.length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {gate.reasons.map((reason) => (
                      <li key={reason} className="text-[11.5px] leading-5 text-warn">
                        {reason}
                      </li>
                    ))}
                  </ul>
                )}
                {gate.regressed_cases.length > 0 && (
                  <div className="mt-2 overflow-hidden rounded-lg border border-zinc-800/60">
                    <table className="w-full text-left text-[11px]">
                      <TableHead>
                        <tr>
                          <th className="px-3 py-2 font-medium">{tr('回归用例', 'Regressed case')}</th>
                          <th className="px-3 py-2 font-medium">{tr('基线', 'Baseline')}</th>
                          <th className="px-3 py-2 font-medium">{tr('本次', 'Current')}</th>
                        </tr>
                      </TableHead>
                      <tbody className="divide-y divide-zinc-800/40">
                        {gate.regressed_cases.map((row) => (
                          <tr key={row.case_id}>
                            <td className="px-3 py-2 text-zinc-200">{row.case_name}</td>
                            <td className="px-3 py-2 text-zinc-400">{row.baseline_status}</td>
                            <td className="px-3 py-2 text-zinc-400">{row.current_status}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </DetailSection>
            )}

            <DetailSection title={tr('结果明细', 'Results')}>
              <div className="overflow-hidden rounded-lg border border-zinc-800/60">
                <table className="w-full text-left text-[11px]">
                  <TableHead>
                    <tr>
                      <th className="px-3 py-2 font-medium">{tr('用例', 'Case')}</th>
                      <th className="px-3 py-2 font-medium">{tr('结果', 'Result')}</th>
                      <th className="px-3 py-2 font-medium">{tr('评分', 'Score')}</th>
                      <th className="px-3 py-2 font-medium">{tr('失败原因', 'Reasons')}</th>
                      <th className="px-3 py-2 font-medium">{tr('工具', 'Tools')}</th>
                    </tr>
                  </TableHead>
                  <tbody className="divide-y divide-zinc-800/40 align-top">
                    {report.results.map((result) => {
                      const itemMeta = CASE_STATUS_META[result.status];
                      const avg = judgeAvg(result.judge_scores);
                      return (
                        <tr key={result.id} className="hover:bg-zinc-900/40">
                          <td className="max-w-[12rem] px-3 py-2">
                            <span className="block truncate font-medium text-zinc-200">
                              {result.case_name}
                            </span>
                            <span className="block truncate text-[10px] text-zinc-500">
                              {result.input}
                            </span>
                          </td>
                          <td className="whitespace-nowrap px-3 py-2">
                            <StatusPill tone={itemMeta.tone} label={tr(itemMeta.zh, itemMeta.en)} />
                            <span className="mt-1 block tabular-nums text-[10px] text-zinc-500">
                              {result.duration_ms ?? '—'}ms · {result.total_tokens ?? '—'} tk
                            </span>
                          </td>
                          <td className="whitespace-nowrap px-3 py-2 tabular-nums text-zinc-200">
                            {avg === null ? '—' : avg.toFixed(1)}
                          </td>
                          <td className="max-w-[16rem] px-3 py-2">
                            {(result.failure_reasons ?? []).map((reason) => (
                              <div key={reason} className="text-[10.5px] leading-4 text-warn">
                                {reason}
                              </div>
                            ))}
                            {(result.failure_reasons ?? []).length === 0 && (
                              <span className="text-[10px] text-zinc-600">—</span>
                            )}
                          </td>
                          <td className="px-3 py-2">
                            <div className="flex flex-wrap gap-1">
                              {(result.tool_names ?? []).map((name) => (
                                <Chip key={name} dense className="font-sans">{name}</Chip>
                              ))}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </DetailSection>

            {consistency && consistency.annotated_results > 0 && (
              <DetailSection title={tr('人工标注一致性', 'Annotation consistency')}>
                <div className="flex flex-wrap items-center gap-2 text-[11px] text-zinc-400">
                  <span>
                    {tr(
                      `已标注 ${consistency.annotated_results}/${consistency.results_total} 条`,
                      `${consistency.annotated_results}/${consistency.results_total} annotated`,
                    )}
                  </span>
                  <span className="text-zinc-700">·</span>
                  <span>
                    {tr(
                      `多人标注 ${consistency.multi_annotated} 条`,
                      `${consistency.multi_annotated} multi-annotated`,
                    )}
                  </span>
                  {consistency.agreement_rate !== null && (
                    <>
                      <span className="text-zinc-700">·</span>
                      <span>
                        {tr(
                          `一致率 ${Math.round(consistency.agreement_rate * 100)}%`,
                          `Agreement ${Math.round(consistency.agreement_rate * 100)}%`,
                        )}
                      </span>
                    </>
                  )}
                </div>
              </DetailSection>
            )}
          </>
        )}
      </div>
    </main>
  );
}
