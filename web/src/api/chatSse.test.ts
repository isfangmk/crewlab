// consumeSseResponse 回归：隐藏/离屏页 rAF 不触发时，读帧循环不得饿死。
// 症状：fetch 已 200 但正文永不消费，回答永远卡在「正在分析」。

import { describe, expect, it, vi } from 'vitest';

import { consumeSseResponse } from './chat';

/** 造一个一次性吐完的 SSE 响应体（同 sse.test.ts 的夹具形状）。 */
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

describe('consumeSseResponse', () => {
  it('rAF 不触发（隐藏页）时仍能读完所有帧', async () => {
    vi.stubGlobal('requestAnimationFrame', () => 0);
    try {
      const deltas: string[] = [];
      let done = false;
      await consumeSseResponse(
        sseResponse([
          'event: assistant_delta\ndata: {"message_id":"m1","delta":"你好","partial":true}\n\n',
          'event: assistant_delta\ndata: {"message_id":"m1","delta":"世界","partial":false}\n\n',
          'event: done\ndata: {"session_id":"s1","usage":{},"iterations":1}\n\n',
        ]),
        {
          onAssistantDelta: (e) => deltas.push(e.delta),
          onDone: () => {
            done = true;
          },
        },
      );
      expect(deltas).toEqual(['你好', '世界']);
      expect(done).toBe(true);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
