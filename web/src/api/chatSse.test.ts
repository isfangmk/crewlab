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

  it('error 帧无 onError 时抛出真实错误消息（不再静默吞掉）', async () => {
    const realMessage = 'context length exceeded: requested 1571372 > 1048576';
    const consume = consumeSseResponse(
      sseResponse([
        'event: assistant_delta\ndata: {"message_id":"m1","delta":"半句话","partial":true}\n\n',
        `event: error\ndata: {"error":"${realMessage}"}\n\n`,
      ]),
      { onAssistantDelta: () => {} }, // 故意不传 onError（审批续跑就是这种消费者）
    );
    await expect(consume).rejects.toThrow(realMessage);
  });

  it('error 帧有 onError 时交给回调且不再抛出', async () => {
    const realMessage = '模型调用失败';
    const errors: string[] = [];
    await expect(
      consumeSseResponse(
        sseResponse([`event: error\ndata: {"error":"${realMessage}"}\n\n`]),
        { onError: (err) => errors.push(err.message) },
      ),
    ).resolves.toBeUndefined();
    expect(errors).toEqual([realMessage]);
  });
});
