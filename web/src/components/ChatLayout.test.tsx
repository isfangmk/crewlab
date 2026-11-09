import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { ChatLayout } from './ChatLayout';

describe('ChatLayout', () => {
  it('只渲染子路由，不再展示会话侧栏', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route element={<ChatLayout />}>
            <Route path="/" element={<main>Home content</main>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText('Home content')).toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: '会话列表' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '搜索会话' })).not.toBeInTheDocument();
  });
});
