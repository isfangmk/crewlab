// KnowledgeSources 页面测试：列表与筛选、触发同步、创建后自动同步、
// 查看来源文档与删除确认。
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import KnowledgeSourcesPage from './KnowledgeSources';
import { server } from '@/test/msw-server';

vi.mock('@/store/auth', () => ({
  useAuth: Object.assign(
    <T,>(selector: (s: { role: string }) => T): T => selector({ role: 'admin' }),
    { getState: () => ({ logout: () => {} }) },
  ),
  getToken: () => null,
}));

vi.mock('@/store/me', () => ({
  usePermissions: () => ({
    role: 'admin',
    isAdmin: true,
    isDeveloper: false,
    isViewer: false,
    canMutate: true,
  }),
}));

const urlSource = {
  id: 'src-url',
  name: '运维 Wiki',
  kind: 'url',
  location: 'https://wiki.internal/runbook/nginx',
  branch: '',
  path_prefix: '',
  knowledge_path: '外部源/运维 Wiki',
  enabled: true,
  sync_status: 'ok',
  last_error: '',
  last_synced_at: '2026-11-14T08:00:00Z',
  last_duration_ms: 1200,
  document_count: 3,
  created_at: '2026-11-10T00:00:00Z',
  updated_at: '2026-11-14T08:00:00Z',
};

const gitSource = {
  id: 'src-git',
  name: 'Runbook 仓库',
  kind: 'git',
  location: 'https://git.example.com/ops/runbooks.git',
  branch: 'main',
  path_prefix: 'docs',
  knowledge_path: '外部源/Runbook 仓库',
  enabled: true,
  sync_status: 'error',
  last_error: '克隆超时',
  last_synced_at: null,
  last_duration_ms: null,
  document_count: 0,
  created_at: '2026-11-11T00:00:00Z',
  updated_at: '2026-11-14T09:00:00Z',
};

const syncedDocs = [
  {
    id: 'doc-1',
    source_type: 'url',
    title: 'nginx 重启 SOP',
    path: '外部源/运维 Wiki',
    enabled: true,
    created_at: '2026-11-14T08:00:00Z',
    updated_at: '2026-11-14T08:00:00Z',
    chunk_count: 4,
    external_source_id: 'src-url',
    external_key: 'https://wiki.internal/runbook/nginx',
  },
];

const sourcesURL = '/api/v1/knowledge/sources';
const docsURL = '/api/v1/knowledge/docs';

function useBaseHandlers() {
  server.use(
    http.get(sourcesURL, () =>
      HttpResponse.json({ items: [urlSource, gitSource], total: 2 }),
    ),
    http.get(docsURL, ({ request }) => {
      const sourceId = new URL(request.url).searchParams.get('external_source_id');
      return HttpResponse.json({
        items: sourceId === 'src-url' ? syncedDocs : [],
        total: sourceId === 'src-url' ? syncedDocs.length : 0,
      });
    }),
  );
}

describe('KnowledgeSourcesPage', () => {
  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
    useBaseHandlers();
  });

  it('展示来源、知识路径、文档数与同步状态', async () => {
    render(<KnowledgeSourcesPage />);

    expect(await screen.findByText('运维 Wiki')).toBeInTheDocument();
    expect(screen.getByText('Runbook 仓库')).toBeInTheDocument();
    expect(screen.getByText('3 篇')).toBeInTheDocument();
    expect(screen.getByText('已同步')).toBeInTheDocument();
    expect(screen.getByText('同步失败')).toBeInTheDocument();
    expect(screen.getByText('克隆超时')).toBeInTheDocument();
    // Git 来源额外展示分支与目录前缀
    expect(screen.getByText('分支 main · docs')).toBeInTheDocument();
  });

  it('按名称过滤来源', async () => {
    render(<KnowledgeSourcesPage />);
    await screen.findByText('运维 Wiki');

    await userEvent.type(screen.getByRole('searchbox', { name: '搜索外部知识源' }), '仓库');
    expect(screen.getByText('Runbook 仓库')).toBeInTheDocument();
    expect(screen.queryByText('运维 Wiki')).not.toBeInTheDocument();

    await userEvent.clear(screen.getByRole('searchbox', { name: '搜索外部知识源' }));
    expect(screen.getByText('运维 Wiki')).toBeInTheDocument();
  });

  it('点击同步触发 POST 并提示已开始', async () => {
    let synced = false;
    server.use(
      http.post(`${sourcesURL}/src-url/sync`, () => {
        synced = true;
        return HttpResponse.json({ ...urlSource, sync_status: 'syncing' });
      }),
    );
    render(<KnowledgeSourcesPage />);
    await screen.findByText('运维 Wiki');

    await userEvent.click(screen.getByRole('button', { name: '同步 运维 Wiki' }));

    await waitFor(() => expect(synced).toBe(true));
    expect(await screen.findByText('已开始同步「运维 Wiki」')).toBeInTheDocument();
    expect(screen.getByText('同步中')).toBeInTheDocument();
  });

  it('创建来源后自动触发首次同步', async () => {
    let createdBody: Record<string, unknown> | null = null;
    let syncedId = '';
    server.use(
      http.post(sourcesURL, async ({ request }) => {
        createdBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          ...urlSource,
          id: 'src-new',
          name: createdBody.name,
          sync_status: 'idle',
          document_count: 0,
        });
      }),
      http.post(`${sourcesURL}/src-new/sync`, () => {
        syncedId = 'src-new';
        return HttpResponse.json({ ...urlSource, id: 'src-new', sync_status: 'syncing' });
      }),
    );
    render(<KnowledgeSourcesPage />);
    await screen.findByText('运维 Wiki');

    await userEvent.click(screen.getByRole('button', { name: '新建来源' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.type(within(dialog).getByLabelText('名称'), '新 Runbook');
    await userEvent.type(within(dialog).getByLabelText('页面地址'), 'https://wiki.internal/new');
    await userEvent.click(within(dialog).getByRole('button', { name: '创建并同步' }));

    await waitFor(() => expect(createdBody).not.toBeNull());
    expect(createdBody).toMatchObject({
      name: '新 Runbook',
      kind: 'url',
      location: 'https://wiki.internal/new',
      enabled: true,
    });
    await waitFor(() => expect(syncedId).toBe('src-new'));
    expect(await screen.findByText('来源已创建，正在后台同步')).toBeInTheDocument();
  });

  it('查看来源文档按 external_source_id 拉取并展示同步键', async () => {
    let requestedId = '';
    server.use(
      http.get(docsURL, ({ request }) => {
        requestedId = new URL(request.url).searchParams.get('external_source_id') ?? '';
        return HttpResponse.json({ items: syncedDocs, total: syncedDocs.length });
      }),
    );
    render(<KnowledgeSourcesPage />);
    await screen.findByText('运维 Wiki');

    await userEvent.click(
      screen.getByRole('button', { name: '查看 运维 Wiki 的文档' }),
    );

    expect(await screen.findByText('nginx 重启 SOP')).toBeInTheDocument();
    const dialog = screen.getByRole('dialog');
    expect(
      within(dialog).getByText('https://wiki.internal/runbook/nginx'),
    ).toBeInTheDocument();
    expect(requestedId).toBe('src-url');
  });

  it('删除来源需二次确认并提示已删除', async () => {
    let deletedId = '';
    server.use(
      http.delete(`${sourcesURL}/src-git`, () => {
        deletedId = 'src-git';
        return HttpResponse.json({ ok: true });
      }),
    );
    render(<KnowledgeSourcesPage />);
    await screen.findByText('Runbook 仓库');

    await userEvent.click(screen.getByRole('button', { name: '删除 Runbook 仓库' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/会连同切块一起删除/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: '删除' }));

    await waitFor(() => expect(deletedId).toBe('src-git'));
    expect(await screen.findByText('外部源已删除')).toBeInTheDocument();
  });
});
