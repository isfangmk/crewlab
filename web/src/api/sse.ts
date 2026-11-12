// SSE 帧读取：把响应体按帧切开交给调用方。
//
// 聊天流、审批流、工作流事件流共用这一份实现：帧格式（event / data 行）、
// 换行归一化与残帧处理都只在这里做一遍。

import { ApiError } from '@/api/client';

export type SseFrame = {
  event: string;
  data: string;
};

/** 响应不是成功状态时，按统一错误结构抛出 ApiError。 */
export async function assertSseOk(res: Response): Promise<void> {
  if (res.ok) return;
  let parsed: unknown = null;
  try {
    parsed = res.headers.get('content-type')?.includes('application/json')
      ? await res.json()
      : await res.text();
  } catch {
    parsed = null;
  }
  let msg = `HTTP ${res.status}`;
  let code: string | undefined;
  if (parsed && typeof parsed === 'object') {
    const obj = parsed as Record<string, unknown>;
    if (typeof obj.error === 'string') msg = obj.error;
    else if (typeof obj.detail === 'string') msg = obj.detail;
    if (typeof obj.code === 'string') code = obj.code;
  } else if (typeof parsed === 'string' && parsed.trim() !== '') {
    const text = parsed.trim();
    msg = `HTTP ${res.status}: ${text.length > 200 ? `${text.slice(0, 200)}…` : text}`;
  }
  throw new ApiError(msg, res.status, code, parsed);
}

/** 解析一帧：返回事件名与数据文本；注释行与心跳帧跳过。 */
export function parseSseFrame(raw: string): SseFrame | null {
  let event = 'message';
  const dataLines: string[] = [];
  for (const line of raw.split('\n')) {
    if (line === '' || line.startsWith(':')) continue;
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join('\n') };
}

/**
 * 逐帧读取 SSE 响应体。
 *
 * onFrame 返回 false 表示不再需要后续帧，读取随即停止并释放连接；
 * onFrame 可以是异步函数，需要按帧节流时用它让出主线程。
 */
export async function readSseFrames(
  res: Response,
  onFrame: (frame: SseFrame) => boolean | void | Promise<boolean | void>,
): Promise<void> {
  await assertSseOk(res);
  if (res.body === null) throw new ApiError('SSE 响应没有响应体', res.status);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  // 收到终止帧后不再继续读，但要让底层连接正常收尾
  let stopped = false;

  const handle = async (raw: string): Promise<boolean> => {
    const frame = parseSseFrame(raw);
    if (frame === null) return true;
    return (await onFrame(frame)) !== false;
  };

  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n').replace(/\r/g, '\n');

      let sep = buf.indexOf('\n\n');
      while (sep >= 0) {
        const raw = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        if (!(await handle(raw))) {
          stopped = true;
          break;
        }
        sep = buf.indexOf('\n\n');
      }
      if (stopped) break;
    }
    // 收尾时缓冲区里可能还剩半帧（服务端没有补空行），不能直接丢掉
    if (!stopped && buf.trim() !== '') await handle(buf);
  } finally {
    if (stopped) {
      await reader.cancel().catch(() => undefined);
    }
    reader.releaseLock();
  }
}
