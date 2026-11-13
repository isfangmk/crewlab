// 会话 Artifacts 停靠面板：右侧滑出，列出当前会话的全部产出物。
// 数据打真后端（listSessionArtifacts），refreshTick 变化即重新拉取
// （代码解释器 tool_end 后由 ChatThread 递增）。
import { useCallback, useEffect, useState } from 'react';
import { Download, FileJson, FileText, Loader2, Table2, TrendingUp, X } from 'lucide-react';
import { ChartCanvas } from './ChartCard';
import { downloadArtifact, listSessionArtifacts, type ArtifactItem } from '@/api/artifacts';
import { errorText } from '@/lib/errors';
import { useI18n } from '@/i18n/locale';
import { cn } from '@/lib/cn';

const KIND_META: Record<string, { icon: typeof FileText; label: string; labelEn: string }> = {
  chart: { icon: TrendingUp, label: '图表', labelEn: 'Chart' },
  table: { icon: Table2, label: '表格', labelEn: 'Table' },
  file: { icon: FileText, label: '文件', labelEn: 'File' },
  text: { icon: FileJson, label: '文本', labelEn: 'Text' },
};

export function ArtifactsPanel({
  sessionId,
  refreshTick,
  onClose,
}: {
  sessionId: string;
  refreshTick: number;
  onClose(): void;
}) {
  const { tr } = useI18n();
  const [items, setItems] = useState<ArtifactItem[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!sessionId) return;
    try {
      const r = await listSessionArtifacts(sessionId);
      setItems(r.items);
      setErr(null);
    } catch (e) {
      setErr(errorText(e));
    }
  }, [sessionId]);

  useEffect(() => {
    void load();
  }, [load, refreshTick]);

  const save = async (item: ArtifactItem) => {
    setBusyId(item.id);
    try {
      const { blob, filename } = await downloadArtifact(item.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename || item.file_name || `${item.title || item.id}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusyId(null);
    }
  };

  const selected = items?.find((i) => i.id === selectedId) ?? null;

  return (
    <aside
      className="flex h-full w-[22rem] shrink-0 flex-col overflow-hidden border-l border-zinc-800/60 bg-zinc-900/40"
      aria-label={tr('会话产出物', 'Session artifacts')}
    >
      <div className="flex items-center justify-between gap-2 border-b border-zinc-800/60 px-3 py-2.5">
        <span className="text-[12.5px] font-semibold text-zinc-100">
          {tr('Artifacts', 'Artifacts')}
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label={tr('关闭产出物面板', 'Close artifacts panel')}
          className="rounded p-1 text-zinc-400 transition-colors hover:bg-zinc-800/60 hover:text-zinc-200"
        >
          <X size={14} />
        </button>
      </div>

      {err && (
        <div className="mx-3 my-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-[11px] text-red-300">
          {err}
        </div>
      )}

      {items === null ? (
        <div className="flex flex-1 items-center justify-center text-sm text-zinc-500">
          <Loader2 size={14} className="mr-2 animate-spin" />
          {tr('加载中…', 'Loading…')}
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-1 px-4 text-center">
          <p className="text-[12px] text-zinc-400">
            {tr('还没有产出物', 'No artifacts yet')}
          </p>
          <p className="text-[11px] text-zinc-600">
            {tr(
              '让助理执行 Python 代码，图表与文件会出现在这里。',
              'Ask the assistant to run Python — charts and files land here.',
            )}
          </p>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          <ul className="divide-y divide-zinc-800/40 overflow-y-auto">
            {items.map((item) => {
              const meta = KIND_META[item.kind] ?? KIND_META.text;
              const Icon = meta.icon;
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(item.id === selectedId ? null : item.id)}
                    className={cn(
                      'flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-zinc-800/40',
                      selectedId === item.id && 'bg-zinc-800/40',
                    )}
                  >
                    <Icon size={13} className="shrink-0 text-indigo-300" />
                    <span className="min-w-0 flex-1 truncate text-[11.5px] text-zinc-200">
                      {item.title || item.id}
                    </span>
                    <span className="shrink-0 text-[10px] text-zinc-500">{meta.label}</span>
                  </button>
                </li>
              );
            })}
          </ul>
          {selected && (
            <div className="max-h-[55%] overflow-auto border-t border-zinc-800/60 bg-zinc-950/40 p-3">
              <div className="mb-2 flex items-center justify-between gap-2">
                <span className="truncate text-[11.5px] font-medium text-zinc-200">
                  {selected.title || selected.id}
                </span>
                <button
                  type="button"
                  onClick={() => void save(selected)}
                  disabled={busyId === selected.id}
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-800/60 px-2 py-1 text-[11px] text-zinc-200 transition-colors hover:border-zinc-600 hover:bg-zinc-700/60 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {busyId === selected.id ? (
                    <Loader2 size={12} className="animate-spin" />
                  ) : (
                    <Download size={12} />
                  )}
                  {tr('下载', 'Download')}
                </button>
              </div>
              {selected.kind === 'chart' &&
                typeof selected.payload === 'object' &&
                selected.payload !== null && (
                  <ChartCanvas option={selected.payload as Record<string, unknown>} />
                )}
              {selected.kind === 'table' && <ArtifactTableLite payload={selected.payload} />}
              {selected.kind === 'text' && (
                <p className="whitespace-pre-wrap break-words text-[11px] leading-relaxed text-zinc-300">
                  {String(selected.payload ?? '')}
                </p>
              )}
              {selected.kind === 'file' && (
                <p className="text-[11px] text-zinc-500">
                  {selected.file_name} · {selected.size} bytes
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </aside>
  );
}

function ArtifactTableLite({ payload }: { payload: unknown }) {
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
  return (
    <div className="max-h-full overflow-auto">
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
    </div>
  );
}
