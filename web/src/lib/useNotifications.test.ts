// 全局通知 → window 事件：新审批单要发 APPROVAL_CREATED_EVENT，
// 侧栏 badge 与审批页据此重拉；审批已决仍走既有 chatLive 通道并额外刷 badge。

import { renderHook, waitFor } from '@testing-library/react';
import { http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  APPROVAL_CREATED_EVENT,
  APPROVAL_DECIDED_EVENT,
  useNotifications,
} from './useNotifications';
import { CHAT_LIVE_EVENT } from './chatLiveBus';
import { useAuth } from '@/store/auth';
import { server } from '@/test/msw-server';

function sseResponse(body: string): Response {
  return new Response(new TextEncoder().encode(body), {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  });
}

describe('useNotifications 审批事件分发', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuth.setState({ token: 'test-token', email: 'admin@local', role: 'admin' });
  });

  afterEach(() => {
    useAuth.setState({ token: null, email: null, role: null });
  });

  it('approval_created → 发审批专属事件（不泛化 chat refetch）', async () => {
    server.use(
      http.get('/api/v1/events', () =>
        sseResponse(
          'id: 10-0\nevent: approval_created\ndata: {"approval_id":"a1","session_id":"s1"}\n\n',
        ),
      ),
    );
    const onCreated = vi.fn();
    const onRefetch = vi.fn();
    window.addEventListener(APPROVAL_CREATED_EVENT, onCreated);
    window.addEventListener('ongrid:chat-refetch', onRefetch);

    const { unmount } = renderHook(() => useNotifications());
    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
    const detail = (onCreated.mock.calls[0][0] as CustomEvent).detail;
    expect(detail).toMatchObject({ approval_id: 'a1', session_id: 's1' });
    // 不复用 CHAT_REFETCH_EVENT，避免语义泛化。
    expect(onRefetch).not.toHaveBeenCalled();

    unmount();
    window.removeEventListener(APPROVAL_CREATED_EVENT, onCreated);
    window.removeEventListener('ongrid:chat-refetch', onRefetch);
  });

  it('approval_decided → 仍发 chatLive + 额外刷 badge', async () => {
    server.use(
      http.get('/api/v1/events', () =>
        sseResponse(
          'id: 11-0\nevent: approval_decided\ndata: {"approval_id":"a2","status":"executed","session_id":"s2"}\n\n',
        ),
      ),
    );
    const onLive = vi.fn();
    const onDecided = vi.fn();
    window.addEventListener(CHAT_LIVE_EVENT, onLive);
    window.addEventListener(APPROVAL_DECIDED_EVENT, onDecided);

    const { unmount } = renderHook(() => useNotifications());
    await waitFor(() => expect(onDecided).toHaveBeenCalled());
    expect(
      onLive.mock.calls.some(
        (call) => (call[0] as CustomEvent).detail?.type === 'approval_decided',
      ),
    ).toBe(true);

    unmount();
    window.removeEventListener(CHAT_LIVE_EVENT, onLive);
    window.removeEventListener(APPROVAL_DECIDED_EVENT, onDecided);
  });

  it('无 token 时不建立连接', async () => {
    useAuth.setState({ token: null, email: null, role: null });
    const handler = vi.fn();
    server.use(http.get('/api/v1/events', handler));

    const { unmount } = renderHook(() => useNotifications());
    await new Promise((r) => setTimeout(r, 50));
    expect(handler).not.toHaveBeenCalled();
    unmount();
  });
});
