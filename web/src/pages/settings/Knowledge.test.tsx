import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it } from 'vitest';

import SettingsKnowledge from './Knowledge';
import { server } from '@/test/msw-server';

const BASE_SETTINGS = [
  { category: 'knowledge', key: 'chunk_size', value: '1200', sensitive: false },
  { category: 'knowledge', key: 'chunk_overlap', value: '200', sensitive: false },
  { category: 'knowledge', key: 'rrf_min_score', value: '0.03', sensitive: false },
];

function mockSettings() {
  return http.get('/api/v1/system-settings', () =>
    HttpResponse.json({ items: BASE_SETTINGS, total: BASE_SETTINGS.length }),
  );
}

beforeEach(() => {
  localStorage.setItem('ongrid-locale', 'en-US');
});

describe('SettingsKnowledge', () => {
  it('saves the threshold without reindexing when chunk params are unchanged', async () => {
    const saved = new Map<string, string>();
    let reindexCalls = 0;
    server.use(
      mockSettings(),
      http.put('/api/v1/system-settings/knowledge/:key', async ({ params, request }) => {
        const body = (await request.json()) as { value: string };
        const key = String(params.key);
        saved.set(key, body.value);
        return HttpResponse.json({ category: 'knowledge', key, value: body.value, sensitive: false });
      }),
      http.post('/api/v1/knowledge/reindex', () => {
        reindexCalls++;
        return HttpResponse.json({ chunks: 12 });
      }),
    );

    render(
      <MemoryRouter>
        <SettingsKnowledge />
      </MemoryRouter>,
    );

    const threshold = await screen.findByLabelText('Minimum RRF score (0 = no filter)');
    expect(threshold).toHaveValue('0.03');

    const user = userEvent.setup();
    await user.clear(threshold);
    await user.type(threshold, '0.2');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText('Settings saved')).toBeInTheDocument();
    expect(saved.get('rrf_min_score')).toBe('0.2');
    expect(reindexCalls).toBe(0);
  });

  it('reindexes after saving changed chunk params', async () => {
    const saved = new Map<string, string>();
    let reindexCalls = 0;
    server.use(
      mockSettings(),
      http.put('/api/v1/system-settings/knowledge/:key', async ({ params, request }) => {
        const body = (await request.json()) as { value: string };
        const key = String(params.key);
        saved.set(key, body.value);
        return HttpResponse.json({ category: 'knowledge', key, value: body.value, sensitive: false });
      }),
      http.post('/api/v1/knowledge/reindex', () => {
        reindexCalls++;
        return HttpResponse.json({ chunks: 77 });
      }),
    );

    render(
      <MemoryRouter>
        <SettingsKnowledge />
      </MemoryRouter>,
    );

    const size = await screen.findByLabelText('Chunk size (chars)');
    const user = userEvent.setup();
    await user.clear(size);
    await user.type(size, '900');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText('Saved; reindexed with the new params (77 chunks)')).toBeInTheDocument();
    expect(saved.get('chunk_size')).toBe('900');
    expect(reindexCalls).toBe(1);
  });
});
