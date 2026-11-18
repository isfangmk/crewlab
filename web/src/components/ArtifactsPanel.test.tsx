// Artifacts 面板：多选勾选、全选、图表 PNG/JSON 双下载、ZIP 打包。
// jsdom 无真实 canvas，chartOptionToPngBlob 用桩返回已知字节；JSZip 用真依赖解包校验。
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import JSZip from 'jszip';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ArtifactsPanel } from './ArtifactsPanel';
import { setLocale } from '@/i18n/locale';

const { listMock, downloadMock, saveBlobMock, pngBlobMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
  downloadMock: vi.fn(),
  saveBlobMock: vi.fn(),
  pngBlobMock: vi.fn(),
}));

vi.mock('@/api/artifacts', () => ({
  listSessionArtifacts: listMock,
  downloadArtifact: downloadMock,
}));
vi.mock('@/lib/download', () => ({ saveBlob: saveBlobMock }));
vi.mock('./ChartCanvas', () => ({
  ChartCanvas: () => null,
  chartOptionToPngBlob: pngBlobMock,
}));
vi.mock('./MermaidGraph', () => ({ MermaidGraph: () => null }));

const now = '2026-11-18T00:00:00.000Z';
const chart = {
  id: 'a-chart',
  session_id: 's1',
  tool_call_id: 'tc1',
  kind: 'chart' as const,
  title: '销售图',
  mime: 'application/json',
  payload: { series: [{ type: 'bar', data: [1, 2] }] },
  file_name: '',
  size: 0,
  created_at: now,
};
const table = {
  id: 'a-table',
  session_id: 's1',
  tool_call_id: 'tc1',
  kind: 'table' as const,
  title: '汇总',
  mime: 'application/json',
  payload: { columns: ['k'], rows: [['v']] },
  file_name: '',
  size: 0,
  created_at: now,
};
const fileItem = {
  id: 'a-file',
  session_id: 's1',
  tool_call_id: 'tc2',
  kind: 'file' as const,
  title: '数据',
  mime: 'text/csv',
  payload: {},
  file_name: 'raw.csv',
  size: 3,
  created_at: now,
};

async function zipNames(): Promise<string[]> {
  const blob = saveBlobMock.mock.calls.at(-1)?.[0] as Blob;
  const zip = await JSZip.loadAsync(blob);
  return Object.keys(zip.files).filter((n) => !zip.files[n].dir);
}

describe('ArtifactsPanel 多选与导出', () => {
  beforeEach(() => {
    setLocale('zh-CN');
    listMock.mockResolvedValue({ items: [chart, table, fileItem] });
    downloadMock.mockResolvedValue({
      blob: new Blob(['a,b,c'], { type: 'text/csv' }),
      filename: 'raw.csv',
    });
    pngBlobMock.mockResolvedValue(new Blob(['PNGDATA'], { type: 'image/png' }));
    saveBlobMock.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  function renderPanel() {
    return render(<ArtifactsPanel sessionId="s1" refreshTick={0} onClose={() => {}} />);
  }

  it('未勾选时不显示批量下载，勾选后出现并显示数量', async () => {
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('销售图');

    expect(screen.queryByText(/下载选中/)).not.toBeInTheDocument();
    await user.click(screen.getByRole('checkbox', { name: '选择 销售图' }));
    expect(screen.getByText('下载选中 (1) · ZIP')).toBeInTheDocument();
  });

  it('全选后所有项被勾选，取消全选后清空', async () => {
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('销售图');

    await user.click(screen.getByText('全选', { exact: true }));
    expect(screen.getByText('下载选中 (3) · ZIP')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: '选择 汇总' })).toBeChecked();

    await user.click(screen.getByText('取消全选', { exact: true }));
    expect(screen.queryByText(/下载选中/)).not.toBeInTheDocument();
  });

  it('ZIP 打包：图表含 png+json，表格 json，文件用后端字节', async () => {
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('销售图');

    await user.click(screen.getByText('全选', { exact: true }));
    await user.click(screen.getByText('下载选中 (3) · ZIP'));

    await waitFor(() => expect(saveBlobMock).toHaveBeenCalled());
    const names = await zipNames();
    expect(names).toContain('销售图.png');
    expect(names).toContain('销售图.json');
    expect(names).toContain('汇总.json');
    expect(names).toContain('数据.csv');
    expect(downloadMock).toHaveBeenCalledWith('a-file');
    expect(pngBlobMock).toHaveBeenCalled();
  });

  it('预览区图表有 PNG 与 JSON 两个下载入口', async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByText('销售图'));

    // 预览区里出现两个下载按钮
    expect(screen.getByRole('button', { name: 'PNG' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'JSON' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'PNG' }));
    await waitFor(() => expect(saveBlobMock).toHaveBeenCalled());
    expect(saveBlobMock.mock.calls.at(-1)?.[1]).toBe('销售图.png');
  });
});
