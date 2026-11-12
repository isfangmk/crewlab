// SSE 帧读取：分帧、换行归一化、残帧处理与终止信号。
//
// 这些是所有流式接口共用的底层能力，出错会让聊天流、审批流、工作流事件一起出问题。

import { describe, expect, it } from 'vitest';

import { parseSseFrame, readSseFrames, type SseFrame } from '@/api/sse';

/** 造一个分块可控的 SSE 响应体。 */
function sseResponse(chunks: string[], init?: ResponseInit): Response {
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
    ...init,
  });
}

describe('parseSseFrame', () => {
  it('拆出事件名与数据', () => {
    const frame = parseSseFrame('event: node_finished\ndata: {"a":1}');
    expect(frame).toEqual({ event: 'node_finished', data: '{"a":1}' });
  });

  it('合并多行 data', () => {
    const frame = parseSseFrame('event: x\ndata: 第一行\ndata: 第二行');
    expect(frame?.data).toBe('第一行\n第二行');
  });

  it('跳过注释行与心跳帧', () => {
    expect(parseSseFrame(': keep-alive')).toBeNull();
  });

  it('没有 data 行时不算一帧', () => {
    expect(parseSseFrame('event: only')).toBeNull();
  });

  it('默认事件名是 message', () => {
    expect(parseSseFrame('data: 1')?.event).toBe('message');
  });
});

describe('readSseFrames', () => {
  it('按帧依次回调', async () => {
    const received: SseFrame[] = [];
    await readSseFrames(
      sseResponse(['event: a\ndata: 1\n\nevent: b\ndata: 2\n\n']),
      (frame) => {
        received.push(frame);
      },
    );
    expect(received.map((f) => f.event)).toEqual(['a', 'b']);
    expect(received.map((f) => f.data)).toEqual(['1', '2']);
  });

  it('跨块拼接半帧', async () => {
    const received: SseFrame[] = [];
    await readSseFrames(
      sseResponse(['event: a\nda', 'ta: {"x":1}\n\n']),
      (frame) => {
        received.push(frame);
      },
    );
    expect(received).toHaveLength(1);
    expect(received[0].data).toBe('{"x":1}');
  });

  it('归一化 CRLF 换行', async () => {
    const received: SseFrame[] = [];
    await readSseFrames(
      sseResponse(['event: a\r\ndata: 1\r\n\r\n']),
      (frame) => {
        received.push(frame);
      },
    );
    expect(received).toHaveLength(1);
    expect(received[0].event).toBe('a');
  });

  it('流结束时补上没写空行的残帧', async () => {
    const received: SseFrame[] = [];
    await readSseFrames(
      sseResponse(['event: a\ndata: 1']),
      (frame) => {
        received.push(frame);
      },
    );
    expect(received).toHaveLength(1);
    expect(received[0].data).toBe('1');
  });

  it('回调返回 false 时停止读取', async () => {
    const received: SseFrame[] = [];
    await readSseFrames(
      sseResponse(['event: a\ndata: 1\n\nevent: b\ndata: 2\n\nevent: c\ndata: 3\n\n']),
      (frame) => {
        received.push(frame);
        return frame.event !== 'b';
      },
    );
    expect(received.map((f) => f.event)).toEqual(['a', 'b']);
  });

  it('响应不是成功状态时抛出 ApiError', async () => {
    await expect(
      readSseFrames(
        new Response(JSON.stringify({ error: '运行不存在' }), {
          status: 404,
          headers: { 'content-type': 'application/json' },
        }),
        () => undefined,
      ),
    ).rejects.toThrow('运行不存在');
  });
});
