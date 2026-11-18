// 消息搜索 API：路径/编码与返回结构。

import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { searchMessages } from './chat';
import { useAuth } from '@/store/auth';
import { server } from '@/test/msw-server';

describe('searchMessages', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuth.setState({ token: 't', email: 'a', role: 'admin' });
  });
  afterEach(() => { useAuth.setState({ token: null, email: null, role: null }); });

  it('带 q/limit 请求并透出命中片段', async () => {
    let path = '';
    server.use(
      http.get('/api/v1/chat/messages/search', ({ request }) => {
        path = request.url.toString();
        return HttpResponse.json({
          items: [
            {
              session_id: 's1',
              session_title: '会话一',
              message_id: 'm1',
              role: 'assistant',
              snippet: '命中内容',
            },
          ],
          total: 1,
        });
      }),
    );
    const res = await searchMessages('100% 关键', 5);
    expect(path).toContain('/api/v1/chat/messages/search?');
    expect(path).toContain('q=100%25%20%E5%85%B3%E9%94%AE');
    expect(path).toContain('limit=5');
    expect(res.items[0]).toMatchObject({ message_id: 'm1', session_id: 's1' });
  });
});
