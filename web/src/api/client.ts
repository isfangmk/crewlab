import { getToken, useAuth } from '@/store/auth';
import { getLocale } from '@/i18n/locale';

export class ApiError extends Error {
  status: number;
  code?: string;
  payload?: unknown;
  constructor(message: string, status: number, code?: string, payload?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.payload = payload;
  }
}

type RequestOpts = {
  signal?: AbortSignal;
  headers?: Record<string, string>;
  noAuth?: boolean;
};

const BASE = '/api/v1';

export async function request<T = unknown>(
  method: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH',
  path: string,
  body?: unknown,
  opts: RequestOpts = {}
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: 'application/json',
    // UI 语言；后端对话回合据此决定模型输出语言（zh / en）。
    'Accept-Language': getLocale(),
    ...(opts.headers ?? {}),
  };

  if (!opts.noAuth) {
    const token = getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;
  }

  let payload: BodyInit | undefined;
  if (body !== undefined && body !== null) {
    if (body instanceof FormData) {
      // FormData 由浏览器自行设置 multipart boundary，不要改成 JSON。
      payload = body;
    } else {
      headers['Content-Type'] = 'application/json';
      payload = JSON.stringify(body);
    }
  }

  const url = path.startsWith('http') ? path : `${BASE}${path.startsWith('/') ? path : `/${path}`}`;

  let res: Response;
  try {
    res = await fetch(url, { method, headers, body: payload, signal: opts.signal });
  } catch (err) {
    if ((err as Error).name === 'AbortError') throw err;
    throw new ApiError((err as Error).message || 'Network error', 0);
  }

  const ct = res.headers.get('content-type') ?? '';
  let parsed: unknown = null;
  if (ct.includes('application/json')) {
    try {
      parsed = await res.json();
    } catch {
      parsed = null;
    }
  } else {
    const text = await res.text();
    parsed = text ? text : null;
  }

  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    let code: string | undefined;
    if (parsed && typeof parsed === 'object') {
      const obj = parsed as Record<string, unknown>;
      if (typeof obj.error === 'string') msg = obj.error;
      else if (typeof obj.message === 'string') msg = obj.message;
      else if (typeof obj.detail === 'string') msg = obj.detail;
      if (typeof obj.code === 'string') code = obj.code;
    } else if (typeof parsed === 'string' && parsed.trim()) {
      // 非 JSON 错误正文：截取一段展示，避免只显示 HTTP 状态码。
      const text = parsed.trim();
      msg = `HTTP ${res.status}: ${text.length > 200 ? text.slice(0, 200) + '…' : text}`;
    }
    if (res.status === 401 && !opts.noAuth) {
      // 单管理员固定 token，无 refresh；401 即会话失效，直接登出。
      useAuth.getState().logout();
    }
    throw new ApiError(msg, res.status, code, parsed);
  }

  return parsed as T;
}

export type DownloadedFile = { blob: Blob; filename: string };

// 导出类接口返回文件流而不是 JSON：单独走一次 fetch，错误仍按统一 ApiError 抛出。
export async function requestFile(
  path: string,
  query?: URLSearchParams
): Promise<DownloadedFile> {
  const token = getToken();
  const qs = query?.toString() ?? '';
  const url = `${BASE}${path.startsWith('/') ? path : `/${path}`}${qs ? `?${qs}` : ''}`;

  let res: Response;
  try {
    res = await fetch(url, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
  } catch (err) {
    throw new ApiError((err as Error).message || 'Network error', 0);
  }

  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    let code: string | undefined;
    if ((res.headers.get('content-type') ?? '').includes('application/json')) {
      try {
        const obj = (await res.json()) as Record<string, unknown>;
        if (typeof obj.detail === 'string') msg = obj.detail;
        else if (typeof obj.error === 'string') msg = obj.error;
        if (typeof obj.code === 'string') code = obj.code;
      } catch {
        // 响应体不是合法 JSON：保留 HTTP 状态码提示。
      }
    }
    throw new ApiError(msg, res.status, code);
  }

  const disposition = res.headers.get('content-disposition') ?? '';
  return { blob: await res.blob(), filename: parseContentDisposition(disposition) };
}

/** 解析 Content-Disposition 里的下载文件名。
 *
 *  非 ASCII 文件名只有 RFC 5987 的 filename*=utf-8''… 形式（Starlette 行为），
 *  纯 ASCII 时才是 filename="…"；两种都解，中文经 decodeURIComponent 还原。
 *  头缺失或两种形式都不在时回退 'download'。
 */
export function parseContentDisposition(disposition: string): string {
  const star = /filename\*=utf-8''([^;]+)/i.exec(disposition);
  const plain = /filename="([^"]+)"/.exec(disposition);
  if (star) {
    try {
      return decodeURIComponent(star[1]);
    } catch {
      // 编码串损坏时退回原样，不让下载整体失败
      return star[1];
    }
  }
  return plain ? plain[1] : 'download';
}

