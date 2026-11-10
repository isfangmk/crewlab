import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import SecretsPage from './Secrets';
import { server } from '@/test/msw-server';

const SECRETS = [
  {
    id: 1,
    name: 'gh-prod',
    type: 'custom',
    description: 'GitHub PAT',
    field_keys: ['GITHUB_TOKEN'],
    created_at: '2026-11-01T00:00:00Z',
    updated_at: '2026-11-02T00:00:00Z',
  },
  {
    id: 2,
    name: 'legacy',
    type: 'custom',
    description: '旧凭证',
    field_keys: ['token'],
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-02T00:00:00Z',
  },
];

const rotateBodies: { id: number; fields: Record<string, string> }[] = [];
const exportRequests: string[] = [];

describe('SecretsPage', () => {
  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
    rotateBodies.length = 0;
    exportRequests.length = 0;
    server.use(
      // 静态路径必须先注册：/secrets/health 不能被 /secrets/:id 抢走。
      http.get('/api/v1/secrets/health', () =>
        HttpResponse.json({
          total: 2,
          broken: 1,
          items: [
            { id: 1, name: 'gh-prod', type: 'custom', ok: true, error: null },
            { id: 2, name: 'legacy', type: 'custom', ok: false, error: '密钥不匹配' },
          ],
        }),
      ),
      http.get('/api/v1/secrets/export', () => {
        exportRequests.push('export');
        return new HttpResponse('\ufeff名称,类型\ngh-prod,custom\n', {
          headers: {
            'content-type': 'text/csv; charset=utf-8',
            'content-disposition': 'attachment; filename="credentials.csv"',
          },
        });
      }),
      http.get('/api/v1/secrets', () => HttpResponse.json({ items: SECRETS })),
      http.get('/api/v1/credential-types', () =>
        HttpResponse.json({
          items: [{ name: 'custom', label: '自定义', fields: [], builtin: true }],
        }),
      ),
      http.get('/api/v1/secrets/:id/usage', ({ params }) =>
        HttpResponse.json({
          total: params.id === '1' ? 1 : 0,
          items:
            params.id === '1'
              ? [{ id: 9, name: 'gh-mcp', enabled: true, status: 'ready' }]
              : [],
        }),
      ),
      http.post('/api/v1/secrets/:id/rotate', async ({ request, params }) => {
        const body = (await request.json()) as { fields: Record<string, string> };
        rotateBodies.push({ id: Number(params.id), fields: body.fields });
        return HttpResponse.json({
          ...SECRETS[0],
          updated_at: '2026-11-10T02:00:00Z',
        });
      }),
      http.delete('/api/v1/secrets/:id', () => HttpResponse.json({ ok: true })),
    );
  });

  it('lists credentials and warns about undecryptable rows', async () => {
    render(
      <MemoryRouter>
        <SecretsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('gh-prod')).toBeInTheDocument();
    expect(screen.getByText('legacy')).toBeInTheDocument();
    // 健康自检不过的行必须有显式标记，不能只靠一条横幅。
    expect(screen.getByText('不可解密')).toBeInTheDocument();
    expect(screen.getByText(/1 条凭证无法用当前加密密钥解密/)).toBeInTheDocument();
  });

  it('rotates only the submitted fields through the wizard', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <SecretsPage />
      </MemoryRouter>,
    );

    await screen.findByText('gh-prod');
    await user.click(screen.getAllByRole('button', { name: /轮换/ })[0]);

    // 第一步：先看清影响面，再动手。
    expect(await screen.findByText('影响面')).toBeInTheDocument();
    expect(await screen.findByText('gh-mcp')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '下一步' }));
    await user.type(await screen.findByLabelText('GITHUB_TOKEN'), 'ghp_new_value');
    await user.click(screen.getByRole('button', { name: '下一步' }));

    // 确认页只列字段名，不回显新值。
    expect(screen.getByText('未改动字段')).toBeInTheDocument();
    expect(screen.queryByDisplayValue('ghp_new_value')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /执行轮换/ }));

    await waitFor(() =>
      expect(rotateBodies).toEqual([{ id: 1, fields: { GITHUB_TOKEN: 'ghp_new_value' } }]),
    );
    expect(await screen.findByText('已轮换', { selector: 'span' })).toBeInTheDocument();
    expect(await screen.findByText(/已轮换 1 个字段/)).toBeInTheDocument();
  });

  it('skips rotation when no new value is entered', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <SecretsPage />
      </MemoryRouter>,
    );

    await screen.findByText('gh-prod');
    await user.click(screen.getAllByRole('button', { name: /轮换/ })[0]);
    await screen.findByText('影响面');
    await user.click(screen.getByRole('button', { name: '下一步' }));

    // 新值全空时不允许进入确认页。
    expect(screen.getByRole('button', { name: '下一步' })).toBeDisabled();
    expect(rotateBodies).toHaveLength(0);
  });

  it('requires every field when the current ciphertext is undecryptable', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <SecretsPage />
      </MemoryRouter>,
    );

    await screen.findByText('legacy');
    await user.click(screen.getAllByRole('button', { name: /轮换/ })[1]);
    await screen.findByText('影响面');
    await user.click(screen.getByRole('button', { name: '下一步' }));

    expect(
      await screen.findByText(/旧值不可解密，以下字段全部必填/),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下一步' })).toBeDisabled();
  });

  it('exports the credential inventory', async () => {
    const user = userEvent.setup();
    const createObjectURL = vi.fn(() => 'blob:credentials');
    const revokeObjectURL = vi.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    render(
      <MemoryRouter>
        <SecretsPage />
      </MemoryRouter>,
    );

    await screen.findByText('gh-prod');
    await user.click(screen.getByRole('button', { name: /导出清单/ }));

    await waitFor(() => expect(exportRequests).toHaveLength(1));
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1));
    expect(anchorClick).toHaveBeenCalledTimes(1);
    anchorClick.mockRestore();
  });
});
