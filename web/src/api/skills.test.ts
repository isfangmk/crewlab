import { describe, expect, it } from 'vitest';
import { HttpResponse, http } from 'msw';

import { server } from '@/test/msw-server';

import { executeSkill } from './skills';

describe('executeSkill', () => {
  it('sends the skill params as the request body', async () => {
    let requestBody: unknown;
    server.use(
      http.post('/api/v1/skills/host_probe_tcp/execute', async ({ request }) => {
        requestBody = await request.json();
        return HttpResponse.json({ tool: 'host_probe_tcp', result: { ok: true } });
      }),
    );

    await executeSkill('host_probe_tcp', { target: '127.0.0.1:22' });

    expect(requestBody).toEqual({ params: { target: '127.0.0.1:22' } });
  });

  it('omits empty params', async () => {
    let requestBody: unknown;
    server.use(
      http.post('/api/v1/skills/query_devices/execute', async ({ request }) => {
        requestBody = await request.json();
        return HttpResponse.json({ tool: 'query_devices', result: { items: [] } });
      }),
    );

    await executeSkill('query_devices', {});

    expect(requestBody).toEqual({ params: {} });
  });
});
