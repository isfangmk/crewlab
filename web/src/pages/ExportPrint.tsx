// 导出打印视图：PDF = 浏览器「打印 → 另存为 PDF」（.report-print-area 复用
// index.css 里 HLD-014 的 @media print 白底文档样式）。服务端不渲染 PDF：
// Windows 部署装不动 weasyprint 类依赖，中文嵌字体也复杂，打印链路零依赖且可靠。
import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Printer } from 'lucide-react';
import { exportSession } from '@/api/chat';
import { exportEvalRun, type EvalCaseResult } from '@/api/evals';
import { ErrorBanner, PageHeader, RowAction } from '@/components/ui';
import { useI18n } from '@/i18n/locale';
import { errorText } from '@/lib/errors';
import { fullDateTime } from '@/lib/format';

type EvalExportPayload = {
  dataset_name?: string;
  agent_id?: string;
  provider?: string;
  model?: string;
  status?: string;
  total_cases?: number;
  passed_cases?: number;
  failed_cases?: number;
  started_at?: string | null;
  finished_at?: string | null;
  results?: EvalCaseResult[];
};

export default function ExportPrintPage({ kind }: { kind: 'session' | 'eval' }) {
  const { id = '' } = useParams<{ id: string }>();
  const { tr } = useI18n();
  const [markdown, setMarkdown] = useState('');
  const [evalPayload, setEvalPayload] = useState<EvalExportPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        if (kind === 'session') {
          const md = await exportSession(id, 'markdown');
          if (!cancelled) setMarkdown(md);
        } else {
          const file = await exportEvalRun(id, 'json');
          const payload = JSON.parse(await file.blob.text()) as EvalExportPayload;
          if (!cancelled) setEvalPayload(payload);
        }
      } catch (e) {
        if (!cancelled) setErr(errorText(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [kind, id]);

  return (
    <main className="flex flex-1 flex-col overflow-hidden">
      <PageHeader
        title={kind === 'session' ? tr('会话导出（打印）', 'Session export (print)') : tr('评测运行导出（打印）', 'Eval run export (print)')}
        subtitle={tr(
          '打印对话框里选「另存为 PDF」即可导出 PDF',
          'In the print dialog choose "Save as PDF" to export a PDF',
        )}
        actions={
          <RowAction icon={Printer} onClick={() => window.print()}>
            {tr('打印 / 另存为 PDF', 'Print / Save as PDF')}
          </RowAction>
        }
      />
      <div className="flex-1 overflow-y-auto px-6 py-6">
        {err && <ErrorBanner>{err}</ErrorBanner>}
        {loading ? (
          <div className="text-center text-sm text-zinc-500">{tr('加载中…', 'Loading…')}</div>
        ) : (
          <div className="report-print-area mx-auto w-full max-w-3xl px-8 py-8">
            {kind === 'session' ? (
              <div className="md-body text-zinc-100">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
              </div>
            ) : evalPayload !== null ? (
              <div className="space-y-5">
                <div>
                  <h1 className="text-xl font-semibold text-zinc-100">
                    {evalPayload.dataset_name || tr('评测运行', 'Eval run')}
                  </h1>
                  <p className="mt-1 text-[12px] text-zinc-500">
                    {[
                      `${tr('助理', 'Agent')}: ${evalPayload.agent_id ?? '-'}`,
                      `${tr('模型', 'Model')}: ${evalPayload.provider ?? '-'}/${evalPayload.model ?? '-'}`,
                      `${tr('状态', 'Status')}: ${evalPayload.status ?? '-'}`,
                      `${tr('通过', 'Passed')}: ${evalPayload.passed_cases ?? 0}/${evalPayload.total_cases ?? 0}`,
                      evalPayload.started_at ? `${tr('开始', 'Started')}: ${fullDateTime(evalPayload.started_at)}` : '',
                      evalPayload.finished_at ? `${tr('结束', 'Finished')}: ${fullDateTime(evalPayload.finished_at)}` : '',
                    ]
                      .filter((s) => s !== '')
                      .join(' · ')}
                  </p>
                </div>
                <table className="w-full table-fixed text-[12px]">
                  <colgroup>
                    <col className="w-[22%]" />
                    <col className="w-[12%]" />
                    <col className="w-[12%]" />
                    <col className="w-[12%]" />
                    <col className="w-[42%]" />
                  </colgroup>
                  <thead>
                    <tr className="border-b border-zinc-700 text-left text-[11px] text-zinc-500">
                      <th className="py-1.5 pr-2">{tr('用例', 'Case')}</th>
                      <th className="py-1.5 pr-2">{tr('状态', 'Status')}</th>
                      <th className="py-1.5 pr-2">{tr('耗时', 'Duration')}</th>
                      <th className="py-1.5 pr-2">{tr('Tokens', 'Tokens')}</th>
                      <th className="py-1.5">{tr('失败原因', 'Failure reasons')}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/60">
                    {(evalPayload.results ?? []).map((r) => (
                      <tr key={r.id}>
                        <td className="py-1.5 pr-2 align-top text-zinc-200">{r.case_name}</td>
                        <td className="py-1.5 pr-2 align-top text-zinc-300">{r.status}</td>
                        <td className="py-1.5 pr-2 align-top font-sans tabular-nums text-zinc-400">
                          {r.duration_ms != null ? `${r.duration_ms}ms` : '-'}
                        </td>
                        <td className="py-1.5 pr-2 align-top font-sans tabular-nums text-zinc-400">
                          {r.total_tokens ?? '-'}
                        </td>
                        <td className="py-1.5 align-top text-zinc-400">
                          {r.failure_reasons.length > 0 ? r.failure_reasons.join('；') : '-'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </div>
        )}
      </div>
    </main>
  );
}
