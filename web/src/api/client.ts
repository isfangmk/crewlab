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

