// Tools 页面测试 — 分组标题与成员来自后端返回的 groups，前端不再写死分类。
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it } from 'vitest';

import ToolsPage from './Tools';
import { server } from '@/test/msw-server';

const tool = {
  name: 'query_knowledge',
  group: 'backend-group',
  description: '语义检索组织知识库',
  risk: 'safe',
  mount: 'always',
  params: [],
};

function renderPage() {
  return render(
    <MemoryRouter>
      <ToolsPage />
    </MemoryRouter>,
  );
}

describe('ToolsPage 分组', () => {
  beforeEach(() => {
    localStorage.setItem('ongrid-locale', 'zh-CN');
    server.use(
      http.get('/api/v1/tools/builtin', () =>
        HttpResponse.json({
          items: [tool],
          groups: [
            { key: 'backend-group', title: '后端返回的分组', title_en: 'Backend group' },
          ],
          total: 1,
        }),
      ),
    );
  });

  it('按后端分组渲染标题、成员和折叠', async () => {
    renderPage();

    expect(await screen.findByText('后端返回的分组')).toBeInTheDocument();
    expect(screen.getByText('query_knowledge')).toBeInTheDocument();

    fireEvent.click(screen.getByText('后端返回的分组'));
    expect(screen.queryByText('query_knowledge')).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('后端返回的分组'));
    expect(screen.getByText('query_knowledge')).toBeInTheDocument();
  });

  it('英文界面使用后端 title_en', async () => {
    localStorage.setItem('ongrid-locale', 'en-US');
    renderPage();

    expect(await screen.findByText('Backend group')).toBeInTheDocument();
  });
});
