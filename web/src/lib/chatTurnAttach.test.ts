// 多标签页续流：attachActiveTurn 把事件帧 relay 进 chatLiveBus，供已打开的
// ChatThread 复用 onLive 渲染；无进行中回合时不 attach。

import { beforeEach, describe, expect, it, vi } from 'vitest';

// getToken 返回空即可（测试环境无登录态，请求不带 auth 头，服务端由 msw/stub 接管）
vi.mock('@/store/auth', () => ({ getToken: () => '' }));

import { attachActiveTurn } from './chatTurnAttach';
import { CHAT_LIVE_EVENT, type ChatLiveDetail } from './chatLiveBus';

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
}

function sseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  });
}

describe('attachActiveTurn', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('无进行中回合时不订阅，返回 false', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ turn_id: null }));
    vi.stubGlobal('fetch', fetchMock);
    expect(await attachActiveTurn('s1')).toBe(false);
    // 只发了 active-turn 查询，没有连 events
    expect(fetchMock).toHaveBeenCalledTimes(1);
    vi.unstubAllGlobals();
  });

  it('有进行中回合时把 delta/tool/done 帧 relay 到 chatLiveBus', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ turn_id: 't1' }))
      .mockResolvedValueOnce(
        sseResponse([
          'event: assistant_delta\ndata: {"message_id":"m1","delta":"你好","partial":true}\n\n',
          'event: done\ndata: {"session_id":"s1","iterations":1}\n\n',
        ]),
      );
    vi.stubGlobal('fetch', fetchMock);

    const seen: ChatLiveDetail[] = [];
    const onLive = (ev: Event) => seen.push((ev as CustomEvent<ChatLiveDetail>).detail);
    window.addEventListener(CHAT_LIVE_EVENT, onLive);
    try {
      expect(await attachActiveTurn('s1')).toBe(true);
      expect(fetchMock).toHaveBeenCalledTimes(2);
      const urls = fetchMock.mock.calls.map((c) => String(c[0]));
      expect(urls[0]).toContain('/active-turn');
      expect(urls[1]).toContain('/events?turn_id=t1');

      const deltas = seen.filter((d) => d.type === 'assistant_delta');
      expect(deltas).toHaveLength(1);
      expect(deltas[0]).toMatchObject({ event: { delta: '你好' } });
    } finally {
      window.removeEventListener(CHAT_LIVE_EVENT, onLive);
      vi.unstubAllGlobals();
    }
  });
});
