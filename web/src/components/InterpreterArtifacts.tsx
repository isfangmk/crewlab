// 代码解释器的产出物渲染：图表（ECharts）/ 表格 / 文件下载。
// 数据来源有两个口径：工具返回的摘要（live，payload 未落库）与
// 落库后的完整载荷（历史回放 / tool_card 更新）——payload 缺失时降级为摘要胶囊。
import { useState } from 'react';
import { Download } from 'lucide-react';
import { ChartCanvas } from './ChartCard';
import { downloadArtifact } from '@/api/artifacts';
import { errorText } from '@/lib/errors';
import { useI18n } from '@/i18n/locale';

export interface InterpreterArtifact {
  kind?: string;
  title?: string;
  mime?: string;
  payload?: unknown;
  id?: string;
  file_name?: string;
  pending?: boolean;
  size?: number;
}

function asOption(payload: unknown): Record<string, unknown> | null {
  if (typeof payload === 'object' && payload !== null && !Array.isArray(payload)) {
    return payload as Record<string, unknown>;
  }
  return null;
}

function ArtifactFileButton({ artifact }: { artifact: InterpreterArtifact }) {
  const { tr } = useI18n();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    if (!artifact.id || busy) return;
    setBusy(true);
    setErr(null);
    try {
      const { blob, filename } = await downloadArtifact(artifact.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename || artifact.file_name || artifact.title || 'download';
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={() => void save()}
        disabled={busy || !artifact.id}
        className="inline-flex items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-800/60 px-2 py-1 text-[11px] text-zinc-200 transition-colors hover:border-zinc-600 hover:bg-zinc-700/60 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Download size={12} />
        {tr('下载', 'Download')}
      </button>
      {err && <span className="text-[11px] text-danger">{err}</span>}
    </div>
  );
}

function ArtifactTable({ payload }: { payload: unknown }) {
  let columns: string[] = [];
  let rows: unknown[][] = [];
  if (Array.isArray(payload)) {
    columns = payload.length > 0 ? Object.keys(payload[0] as object) : [];
    rows = payload.map((r) => columns.map((c) => (r as Record<string, unknown>)[c]));
  } else if (typeof payload === 'object' && payload !== null) {
    const p = payload as { columns?: unknown; rows?: unknown };
    if (Array.isArray(p.columns) && Array.isArray(p.rows)) {
      columns = p.columns.map(String);
      rows = p.rows as unknown[][];
    }
  }
  if (columns.length === 0) {
    return <p className="text-[11px] text-zinc-500">表格数据为空。</p>;
  }
  return (
    <div className="max-h-64 overflow-auto">
      <table className="og-action-table w-full text-[11px]">
        <thead>
          <tr className="whitespace-nowrap">
            {columns.map((c) => (
              <th key={c} scope="col" className="px-2 py-1.5 text-left font-medium text-zinc-400">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800/40">
          {rows.slice(0, 100).map((r, i) => (
            <tr key={i}>
              {columns.map((c, j) => (
                <td key={c} className="px-2 py-1 text-zinc-300">
                  {r[j] === null || r[j] === undefined ? '—' : String(r[j])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 100 && (
        <p className="px-2 py-1 text-[10px] text-zinc-500">
          仅展示前 100 行，完整数据请下载。
        </p>
      )}
    </div>
  );
}

function ArtifactCard({ artifact }: { artifact: InterpreterArtifact }) {
  const kind = artifact.kind ?? 'text';
  return (
    <figure className="w-full overflow-hidden rounded-lg border border-zinc-800/60 bg-zinc-900/40">
      <figcaption className="flex items-center justify-between gap-2 border-b border-zinc-800/60 px-3 py-2">
        <span className="truncate text-[12px] font-medium text-zinc-200">
          {artifact.title || kind}
        </span>
        <span className="shrink-0 text-[10px] uppercase tracking-wide text-zinc-500">{kind}</span>
      </figcaption>
      <div className="p-2">
        {kind === 'chart' && asOption(artifact.payload) !== null && (
          <ChartCanvas option={asOption(artifact.payload) as Record<string, unknown>} />
        )}
        {kind === 'table' && <ArtifactTable payload={artifact.payload} />}
        {kind === 'file' && <ArtifactFileButton artifact={artifact} />}
        {artifact.pending && (
          <p className="mt-1 text-[10px] text-zinc-500">产出物已保存，可在右侧 Artifacts 面板查看。</p>
        )}
      </div>
    </figure>
  );
}

/** 代码解释器结果里的产出物列表；payload 缺失时降级为摘要胶囊。 */
export function InterpreterArtifacts({ artifacts }: { artifacts: InterpreterArtifact[] }) {
  const { tr } = useI18n();
  if (!Array.isArray(artifacts) || artifacts.length === 0) return null;
  return (
    <div className="space-y-2">
      {artifacts.map((a, i) =>
        a.payload !== undefined || a.id ? (
          <ArtifactCard key={i} artifact={a} />
        ) : (
          <div
            key={i}
            className="flex items-center justify-between gap-2 rounded-md border border-zinc-800/60 bg-zinc-950/40 px-2.5 py-1.5"
          >
            <span className="truncate text-[11px] text-zinc-300">
              {a.title || a.kind}
            </span>
            <span className="shrink-0 text-[10px] uppercase tracking-wide text-zinc-500">
              {a.kind}
            </span>
            <span className="shrink-0 text-[10px] text-zinc-500">
              {tr('见右侧面板', 'See panel')}
            </span>
          </div>
        ),
      )}
    </div>
  );
}
