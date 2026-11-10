// Knowledge 页面测试 — 覆盖「点击操作查看正文 + 编辑保存」链路：
//   1. 内置(vault)文档点「查看」→ 只读查看器拉取并渲染 markdown 正文
//   2. 查看器「复制为组织文档」→ 预填编辑表单 → 保存为新建组织文档
//   3. 组织(manual)文档点「编辑」→ 编辑表单，PATCH 保存
//   4. 检索命中块：展示分道得分，点击结果打开文档并选中命中块
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import KnowledgePage from './Knowledge';
import { server } from '@/test/msw-server';

// client.ts / api/knowledge.ts 只需要 token 取值与 401 兜底；测试里全部打桩。
vi.mock('@/store/auth', () => ({
  useAuth: Object.assign(
    <T,>(selector: (s: { role: string }) => T): T => selector({ role: 'admin' }),
    { getState: () => ({ logout: () => {} }) },
  ),
  getToken: () => null,
  
}));

const vaultDoc = {
  id: '101',
  source_type: 'vault',
  title: 'Host Metric Alerts Runbook',
  url: 'alerts/host-metrics.md',
  path: 'alerts',
  enabled: true,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
};

const vaultContent =
  '# Runbook 正文\n\n第一步：查看告警上下文\n\n' +
  '| Severity | Acknowledge |\n|---|---|\n| SEV1 | < 5 min |\n';

const manualDoc = {
  id: '202',
  source_type: 'manual',
  title: '组织 SOP',
  content: '原有正文',
  path: '',
  enabled: true,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
};

const listURL = '/api/v1/knowledge/docs';
const searchURL = '/api/v1/knowledge/search';

// 检索定位测试用文档：命中块在正文中间，便于断言选中区间
const searchContent =
  '# DNS 排查\n\n先确认解析链路。\n\n第三步：检查 DNS 解析结果\n\n结尾段落。';
const searchDoc = {
  id: '404',
  source_type: 'manual',
  title: 'DNS 排查手册',
  path: '网络/DNS',
  enabled: true,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
};

function useBaseHandlers() {
  server.use(
    http.get(listURL, () =>
      HttpResponse.json({ items: [vaultDoc, manualDoc], total: 2 }),
    ),
    http.get(`${listURL}/101`, () =>
      HttpResponse.json({ ...vaultDoc, content: vaultContent }),
    ),
    http.get(`${listURL}/202`, () =>
      HttpResponse.json({ ...manualDoc }),
    ),
  );
}

describe('KnowledgePage', () => {
  beforeEach(() => {
    // 固定语言，断言中文文案不受运行机器时区/浏览器语言影响。
    localStorage.setItem('ongrid-locale', 'zh-CN');
    useBaseHandlers();
  });

  // 右侧文档列表默认在「组织」作用域；先点侧边栏「内置知识库」切过去。
  async function openBuiltinScope() {
    // ^锚定避免命中「从云端同步内置知识库…」的同步按钮
    await userEvent.click(await screen.findByRole('button', { name: /^内置知识库/ }));
  }

  // 行内动作才是入口：整行点击不再打开查看/编辑。
  // 行内按钮的可访问名带文档标题（「编辑 DNS 排查手册」），按前缀匹配。
  async function clickRowAction(title: string, action: '查看' | '编辑') {
    const row = (await screen.findByText(title)).closest('.group');
    if (!(row instanceof HTMLElement)) throw new Error(`doc row not found: ${title}`);
    await userEvent.click(within(row).getByRole('button', { name: new RegExp(`^${action}`) }));
  }

  it('点击内置文档「查看」打开只读查看器并渲染正文', async () => {
    render(<KnowledgePage />);
    await openBuiltinScope();
    await clickRowAction('Host Metric Alerts Runbook', '查看');

    // 查看器：拉取 GET /knowledge/docs/:id 并渲染 markdown 正文
    expect(await screen.findByText('第一步：查看告警上下文')).toBeInTheDocument();
    // GFM 表格渲染为真 <table>（remark-gfm），而非一行竖线文本
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Severity' })).toBeInTheDocument();
    expect(screen.getByRole('cell', { name: '< 5 min' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /复制为组织文档/ })).toBeInTheDocument();
    // 只读：没有「保存」按钮
    expect(screen.queryByRole('button', { name: '保存' })).not.toBeInTheDocument();
  });

  it('frontmatter 不渲染为标题，元信息以弱化 key/value 展示', async () => {
    server.use(
      http.get(`${listURL}/101`, () =>
        HttpResponse.json({
          ...vaultDoc,
          content: '---\ntitle: Linux Memory Model\ntags: [linux, memory]\n---\n\n正文内容',
        }),
      ),
    );
    render(<KnowledgePage />);
    await openBuiltinScope();
    await clickRowAction('Host Metric Alerts Runbook', '查看');

    expect(await screen.findByText('正文内容')).toBeInTheDocument();
    // 元信息行存在（dt/dd），tags 拆成单个 chip
    expect(screen.getByText('Linux Memory Model')).toBeInTheDocument();
    expect(screen.getByText('linux')).toBeInTheDocument();
    expect(screen.getByText('memory')).toBeInTheDocument();
    // 不再被 setext 语法渲染成 <h2> 粗体标题
    expect(
      screen.queryByRole('heading', { name: /Linux Memory Model/ }),
    ).not.toBeInTheDocument();
  });

  it('复制为组织文档：预填表单并 POST 新建', async () => {
    let createdBody: Record<string, unknown> | null = null;
    server.use(
      http.post(listURL, async ({ request }) => {
        createdBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          ...manualDoc,
          id: '303',
          title: createdBody.title,
        });
      }),
    );

    render(<KnowledgePage />);
    await openBuiltinScope();
    await clickRowAction('Host Metric Alerts Runbook', '查看');
    await screen.findByText('第一步：查看告警上下文');
    await userEvent.click(screen.getByRole('button', { name: /复制为组织文档/ }));

    // 表单预填了原标题与正文
    const titleInput = await screen.findByDisplayValue('Host Metric Alerts Runbook');
    expect(titleInput).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(createdBody).not.toBeNull());
    expect(createdBody).toMatchObject({
      title: 'Host Metric Alerts Runbook',
      // 提交时 trim：fixture 末尾的换行不入库
      content: vaultContent.trim(),
      path: 'alerts',
    });
    // 来源 url（builtin/repo 文件路径）不带入副本
    expect(createdBody).not.toHaveProperty('url');
  });

  it('文档启停：切换开关只 PATCH enabled，并标记已停用', async () => {
    let toggleBody: Record<string, unknown> | null = null;
    server.use(
      http.patch(`${listURL}/202/enabled`, async ({ request }) => {
        toggleBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...manualDoc, enabled: false });
      }),
    );

    render(<KnowledgePage />);
    const toggle = await screen.findByRole('switch', { name: /^参与检索/ });
    await userEvent.click(toggle);

    await waitFor(() => expect(toggleBody).toEqual({ enabled: false }));
    expect(await screen.findByText('已停用')).toBeInTheDocument();
  });

  it('检索命中块：展示分道得分，点击结果打开文档并选中命中块', async () => {
    server.use(
      http.get(searchURL, () =>
        HttpResponse.json({
          items: [
            {
              doc: searchDoc,
              score: 0.0328,
              vector_score: 0.8123,
              keyword_rank: 2,
              chunk_index: 3,
              excerpt: '第三步：检查 DNS 解析结果',
            },
          ],
          total: 1,
        }),
      ),
      http.get(`${listURL}/404`, () =>
        HttpResponse.json({ ...searchDoc, content: searchContent }),
      ),
    );

    render(<KnowledgePage />);
    await userEvent.type(
      await screen.findByPlaceholderText(/语义检索组织知识库/),
      'DNS{Enter}',
    );

    // 结果卡展示向量相似度、关键词排名与 RRF 融合分（标签与数值分属不同节点）
    expect(await screen.findByText(/向量相似度/)).toHaveTextContent(/向量相似度 0\.812/);
    expect(screen.getByText(/关键词排名/)).toHaveTextContent(/关键词排名 #2/);
    expect(screen.getByText('RRF')).toHaveTextContent(/RRF 0\.0328/);

    // 点击结果 → 拉取正文，命中块在正文中被选中
    await userEvent.click(screen.getByRole('button', { name: /DNS 排查手册/ }));
    expect(await screen.findByText('检索命中块 4，已在正文中选中')).toBeInTheDocument();
    // 正文 textarea 的值包含换行，getByDisplayValue 的字符串匹配不做归一化，改用 label 定位
    const body = screen.getByRole('textbox', { name: /内容（markdown）/ });
    expect(body).toHaveValue(searchContent);
    const start = searchContent.indexOf('第三步：检查 DNS 解析结果');
    expect(body).toHaveProperty('selectionStart', start);
    expect(body).toHaveProperty('selectionEnd', start + '第三步：检查 DNS 解析结果'.length);
  });

  it('来源筛选：只列出来源匹配的文档，并把 source_type 带进检索', async () => {
    const uploadDoc = {
      id: '505',
      source_type: 'upload',
      title: '上传的巡检记录',
      path: '运维',
      enabled: true,
      created_at: '2026-06-01T00:00:00Z',
      updated_at: '2026-06-02T00:00:00Z',
    };
    let searchURLSeen = '';
    server.use(
      http.get(listURL, () =>
        HttpResponse.json({ items: [vaultDoc, manualDoc, uploadDoc], total: 3 }),
      ),
      http.get(searchURL, ({ request }) => {
        searchURLSeen = request.url;
        return HttpResponse.json({ items: [], total: 0 });
      }),
    );

    render(<KnowledgePage />);
    // 组织分组下默认列出全部来源：手动与上传都在
    expect(await screen.findByText('组织 SOP')).toBeInTheDocument();
    expect(screen.getByText('上传的巡检记录')).toBeInTheDocument();

    // 切到「上传」：列表只剩上传来源的文档
    await userEvent.click(screen.getByRole('combobox', { name: '来源' }));
    await userEvent.click(await screen.findByRole('option', { name: '上传' }));
    await waitFor(() => expect(screen.queryByText('组织 SOP')).not.toBeInTheDocument());
    expect(screen.getByText('上传的巡检记录')).toBeInTheDocument();

    // 检索沿用同一来源条件
    await userEvent.type(screen.getByPlaceholderText(/语义检索组织知识库/), '巡检{Enter}');
    await waitFor(() => expect(searchURLSeen).toContain('source_type=upload'));
  });

  it('更新时间列：显示完整日期时间并按列排序', async () => {
    const newer = {
      id: '601',
      source_type: 'manual',
      title: '较新的文档',
      path: '',
      enabled: true,
      created_at: '2026-06-01T00:00:00Z',
      updated_at: '2026-08-02T03:04:00Z',
    };
    const older = {
      id: '602',
      source_type: 'manual',
      title: '较旧的文档',
      path: '',
      enabled: true,
      created_at: '2026-05-01T00:00:00Z',
      updated_at: '2026-01-02T03:04:00Z',
    };
    server.use(
      http.get(listURL, () => HttpResponse.json({ items: [older, newer], total: 2 })),
    );

    render(<KnowledgePage />);
    await screen.findByText('较新的文档');

    const bodyRows = () => [...document.querySelectorAll('tbody tr')];
    const firstTitle = () => bodyRows()[0].querySelector('td:nth-child(2)')?.textContent ?? '';

    // 默认按最近更新在前（倒序），与后端默认排序一致
    expect(firstTitle()).toContain('较新的文档');
    // 时间列是完整日期时间，不出现「N 天前」这类相对时间
    expect(document.body.textContent).not.toMatch(/天前|小时前|分钟前/);

    // 点列头切到正序
    await userEvent.click(screen.getByRole('button', { name: /更新时间/ }));
    await waitFor(() => expect(firstTitle()).toContain('较旧的文档'));

    // 再点回倒序
    await userEvent.click(screen.getByRole('button', { name: /更新时间/ }));
    await waitFor(() => expect(firstTitle()).toContain('较新的文档'));
  });

  it('组织文档点击编辑进入表单并 PATCH 保存', async () => {
    let patchedBody: Record<string, unknown> | null = null;
    server.use(
      http.patch(`${listURL}/202`, async ({ request }) => {
        patchedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...manualDoc, ...patchedBody });
      }),
    );

    render(<KnowledgePage />);
    await clickRowAction('组织 SOP', '编辑');

    const body = await screen.findByDisplayValue('原有正文');
    await userEvent.clear(body);
    await userEvent.type(body, '更新后的正文');
    await userEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(patchedBody).not.toBeNull());
    expect(patchedBody).toMatchObject({ title: '组织 SOP', content: '更新后的正文' });
  });
});
