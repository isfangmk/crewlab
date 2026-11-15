// 检索试跑页测试 — 覆盖「试调旋钮真实生效」链路：
//   1. RRF 阈值覆盖 / 来源组 / 来源类型透传进检索请求，结果表展示分道得分
//   2. 按助理范围试跑：取助理 knowledge_scope 转成 scope 参数
//   3. 点命中结果开只读预览并选中命中块
//   4. 阈值非法显式报错，不发请求
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import KnowledgePlaygroundPage from './KnowledgePlayground';
import { server } from '@/test/msw-server';

// client.ts / api/*.ts 只需要 token 取值与 401 兜底；测试里全部打桩。
vi.mock('@/store/auth', () => ({
  useAuth: Object.assign(
    <T,>(selector: (s: { role: string }) => T): T => selector({ role: 'admin' }),
    { getState: () => ({ logout: () => {} }) },
  ),
  getToken: () => null,
}));

const searchURL = '/api/v1/knowledge/search';
const docsURL = '/api/v1/knowledge/docs';
const agentsURL = '/api/v1/agents';
const scopePreviewURL = '/api/v1/knowledge/scope-preview';

// 命中块在正文中间，便于断言选中区间
const docContent = '# DNS 排查\n\n先确认解析链路。\n\n第三步：检查 DNS 解析结果\n\n结尾段落。';
const excerpt = '第三步：检查 DNS 解析结果';

const hitDoc = {
  id: '404',
  source_type: 'manual',
  title: 'DNS 排查手册',
  path: '网络/DNS',
  enabled: true,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
};

const hit = {
  doc: hitDoc,
  score: 0.0325,
  vector_score: 0.812,
  keyword_rank: 2,
  chunk_index: 3,
  excerpt,
};

const opsAgent = {
  name: 'ops',
  display_name: '运维助理',
  knowledge_scope: { paths: ['ops'], doc_ids: ['doc-1'] },
};

function useBaseHandlers(searchCalls: string[]) {
  server.use(
    http.get(agentsURL, () => HttpResponse.json({ items: [opsAgent], total: 1 })),
    http.post(scopePreviewURL, () =>
      HttpResponse.json({ doc_count: 2, matched_paths: [{ path: 'ops', count: 2 }], unknown_paths: [], unknown_doc_ids: [] }),
    ),
    http.get(searchURL, ({ request }) => {
      searchCalls.push(request.url);
      return HttpResponse.json({ items: [hit], total: 1 });
    }),
    http.get(`${docsURL}/404`, () => HttpResponse.json({ ...hitDoc, content: docContent })),
  );
}

describe('KnowledgePlaygroundPage', () => {
  let searchCalls: string[];

  beforeEach(() => {
    // 固定语言，断言中文文案不受运行机器时区/浏览器语言影响。
    localStorage.setItem('ongrid-locale', 'zh-CN');
    searchCalls = [];
    useBaseHandlers(searchCalls);
  });

  it('试调旋钮透传进检索请求，结果表展示分道得分', async () => {
    const user = userEvent.setup();
    render(<KnowledgePlaygroundPage />);

    await user.type(screen.getByRole('searchbox', { name: '语义检索' }), 'DNS 解析');
    await user.type(screen.getByRole('spinbutton', { name: 'RRF 阈值覆盖' }), '0.02');
    await user.click(screen.getByRole('combobox', { name: '来源组' }));
    await user.click(await screen.findByRole('option', { name: '组织' }));
    await user.click(screen.getByRole('button', { name: '检索' }));

    expect(await screen.findByText('DNS 排查手册')).toBeInTheDocument();
    const url = new URL(searchCalls[0]);
    expect(url.searchParams.get('q')).toBe('DNS 解析');
    expect(url.searchParams.get('min_score')).toBe('0.02');
    expect(url.searchParams.get('builtin')).toBe('false');
    // 分数栏三通道口径与知识库页一致（RRF 分 / 向量相似度 / 关键词排名）
    expect(screen.getByText('0.0325')).toBeInTheDocument();
    expect(screen.getByText('0.812')).toBeInTheDocument();
    expect(screen.getByText('#2')).toBeInTheDocument();
  });

  it('按助理范围试跑：knowledge_scope 转成 scope 参数', async () => {
    const user = userEvent.setup();
    render(<KnowledgePlaygroundPage />);

    await user.click(screen.getByRole('combobox', { name: '范围模式' }));
    await user.click(await screen.findByRole('option', { name: '按助理范围' }));
    await user.click(screen.getByRole('combobox', { name: '助理' }));
    await user.click(await screen.findByRole('option', { name: '运维助理（ops）' }));
    await user.type(screen.getByRole('searchbox', { name: '语义检索' }), '磁盘告警');
    await user.click(screen.getByRole('button', { name: '检索' }));

    await waitFor(() => expect(searchCalls.length).toBe(1));
    const url = new URL(searchCalls[0]);
    expect(url.searchParams.getAll('scope_path')).toEqual(['ops']);
    expect(url.searchParams.getAll('scope_doc')).toEqual(['doc-1']);
  });

  it('点命中结果开只读预览并选中命中块', async () => {
    const user = userEvent.setup();
    render(<KnowledgePlaygroundPage />);

    await user.type(screen.getByRole('searchbox', { name: '语义检索' }), 'DNS');
    await user.click(screen.getByRole('button', { name: '检索' }));
    await user.click(await screen.findByRole('button', { name: 'DNS 排查手册' }));

    expect(await screen.findByText(/检索命中块 4，已在正文中选中/)).toBeInTheDocument();
    const box = await screen.findByRole('textbox', { name: '文档正文' }) as HTMLTextAreaElement;
    await waitFor(() => {
      const start = docContent.indexOf(excerpt);
      expect(box.selectionStart).toBe(start);
      expect(box.selectionEnd).toBe(start + excerpt.length);
    });
  });

  it('阈值非法显式报错，不发检索请求', async () => {
    const user = userEvent.setup();
    render(<KnowledgePlaygroundPage />);

    await user.type(screen.getByRole('searchbox', { name: '语义检索' }), 'DNS');
    await user.type(screen.getByRole('spinbutton', { name: 'RRF 阈值覆盖' }), '-1');
    await user.click(screen.getByRole('button', { name: '检索' }));

    expect(await screen.findByText('RRF 阈值必须是 >= 0 的数字')).toBeInTheDocument();
    expect(searchCalls).toHaveLength(0);
  });
});
