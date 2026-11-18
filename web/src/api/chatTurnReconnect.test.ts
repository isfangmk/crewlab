// 对话帧流断线续传：fetch/连接中断后带 last_id 重连，已消费帧不重复，
// 收到 done/error 终态立即停止，不再重连（避免终态回调被重放）。

import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { streamMessage, type SendOptions, type StreamCallbacks } from './chat';
import { useAuth } from '@/store/auth';
import { server } from '@/test/msw-server';

const OPTS: SendOptions = { attachmentIds: [], thinkingEnabled: false, webSearchEnabled: false };

function sse(...frames: string[]): Response {
  return new HttpResponse(frames.join('\n\n') + '\n\n', {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  });
}

function startHandler() {
  return http.post('/api/v1/chat/sessions/s1/messages/stream', () =>
    HttpResponse.json({ turn_id: 'turn-1' }),
  );
}

describe('consumeTurnEvents 断线续传', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuth.setState({ token: 'test-token', email: 'admin@local', role: 'admin' });
  });

  afterEach(() => {
    useAuth.setState({ token: null, email: null, role: null });
    vi.restoreAllMocks();
  });

  it('首连吐部分帧后断、重连带 last_id 续传，终态后停止且不重复帧', async () => {
    const seenLastIds: (string | null)[] = [];
    let call = 0;
    server.use(
      startHandler(),
      http.get('/api/v1/chat/sessions/s1/events', ({ request }) => {
        call += 1;
        const url = new URL(request.url);
        if (call === 1) {
          // 首连：只有一条正文增量，无终态 → 触发重连
          return sse('id: 100-0\nevent: assistant_delta\ndata: {"message_id":"m1","delta":"你","partial":true}');
        }
        seenLastIds.push(url.searchParams.get('last_id'));
        // 重连：带上 last_id，补发终态
        return sse('id: 101-0\nevent: done\ndata: {"session_id":"s1","reply":"你好"}');
      }),
    );

    const deltas: string[] = [];
    const dones: unknown[] = [];
    const reconnects: number[] = [];
    const cbs: StreamCallbacks = {
      onAssistantDelta: (e) => deltas.push(e.delta),
      onDone: (r) => dones.push(r),
      onReconnecting: (n) => reconnects.push(n),
    };

    await streamMessage('s1', 'hi', cbs, OPTS);

    expect(deltas).toEqual(['你']); // 首连帧只分发一次
    expect(dones).toHaveLength(1);
    expect(reconnects).toEqual([1]);
    expect(seenLastIds).toEqual(['100-0']); // 重连请求带上了断点
    expect(call).toBe(2); // 收到 done 后不再重连
  });

  it('fetch 层网络错误（ECONNRESET）也重连，终态收口', async () => {
    let call = 0;
    server.use(
      startHandler(),
      http.get('/api/v1/chat/sessions/s1/events', () => {
        call += 1;
        if (call === 1) return HttpResponse.error(); // 模拟连接被掐断
        return sse('id: 200-0\nevent: done\ndata: {"session_id":"s1"}');
      }),
    );

    const dones: unknown[] = [];
    const errors: Error[] = [];
    await streamMessage('s1', 'hi', {
      onDone: (r) => dones.push(r),
      onError: (e) => errors.push(e),
    }, OPTS);

    expect(errors).toHaveLength(0); // 传输层抖动不当作回合错误
    expect(dones).toHaveLength(1);
    expect(call).toBe(2);
  });

  it('HTTP 401 不重连，直接 onError 收口', async () => {
    let call = 0;
    server.use(
      startHandler(),
      http.get('/api/v1/chat/sessions/s1/events', () => {
        call += 1;
        return HttpResponse.json({ error: 'unauthorized' }, { status: 401 });
      }),
    );

    const errors: Error[] = [];
    await streamMessage('s1', 'hi', { onError: (e) => errors.push(e) }, OPTS);

    expect(errors).toHaveLength(1);
    expect(call).toBe(1); // 非传输错误不重试
  });
});
