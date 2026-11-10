// SourcesBlock 测试 — 引用来源的抽取口径与统一来源区渲染。
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import {
  SourcesSection,
  collectToolSources,
  extractSources,
  mergeSources,
} from './SourcesBlock';
import { setLocale } from '@/i18n/locale';

afterEach(() => {
  cleanup();
  setLocale('zh-CN');
});

describe('extractSources', () => {
  it('知识库命中带文档链接 / 路径 / 块序号 / 摘录', () => {
    const items = extractSources('query_knowledge', {
      items: [
        {
          doc_id: 'doc-1',
          title: '雷达校准手册',
          path: '运维/手册',
          chunk_index: 2,
          score: 0.031,
          excerpt: '校准前先对雷达做零点漂移检查。',
        },
      ],
      total: 1,
    });

    expect(items).toHaveLength(1);
    expect(items[0].kind).toBe('knowledge');
    expect(items[0].href).toBe('/knowledge?doc=doc-1');
    expect(items[0].path).toBe('运维/手册');
    expect(items[0].chunkIndex).toBe(2);
    expect(items[0].score).toBe(0.031);
    expect(items[0].excerpt).toBe('校准前先对雷达做零点漂移检查。');
  });

  it('网页搜索命中取 url / snippet / rerank_score', () => {
    const items = extractSources('web_search', {
      results: [
        {
          title: 'DeepSeek Search API',
          url: 'https://example.com/search',
          snippet: '官方搜索接口说明。',
          summary: '更长的摘要。',
          date: '2026-01-01',
          rerank_score: 0.87,
        },
      ],
    });

    expect(items).toHaveLength(1);
    expect(items[0].kind).toBe('web');
    expect(items[0].href).toBe('https://example.com/search');
    expect(items[0].excerpt).toBe('官方搜索接口说明。');
    expect(items[0].score).toBe(0.87);
    expect(items[0].chunkIndex).toBeUndefined();
  });

  it('非检索类工具即使返回 items 也不产生来源', () => {
    expect(extractSources('query_devices', { items: [{ title: 'edge-1' }] })).toEqual([]);
  });

  it('形状不符时返回空数组而不是抛错', () => {
    expect(extractSources('query_knowledge', 'not-json')).toEqual([]);
    expect(extractSources('query_knowledge', { items: [{ title: '' }, null, 1] })).toEqual([]);
  });
});

describe('mergeSources / collectToolSources', () => {
  it('同一文档在多次检索里只保留一条', () => {
    const hit = {
      doc_id: 'doc-1',
      title: '雷达校准手册',
      chunk_index: 0,
      excerpt: '摘录',
    };
    const merged = mergeSources([
      extractSources('query_knowledge', { items: [hit] }),
      extractSources('query_knowledge', { items: [hit, { ...hit, doc_id: 'doc-2', title: '另一篇' }] }),
    ]);
    expect(merged.map((s) => s.title)).toEqual(['雷达校准手册', '另一篇']);
  });

  it('聚合一次回答里的知识库与网页来源', () => {
    const merged = collectToolSources([
      {
        name: 'query_knowledge',
        result: { items: [{ doc_id: 'doc-1', title: '手册', chunk_index: 0, excerpt: 'a' }] },
      },
      {
        name: 'web_search',
        result: { results: [{ title: '官网', url: 'https://example.com', snippet: 'b' }] },
      },
    ]);
    expect(merged).toHaveLength(2);
    expect(merged.map((s) => s.kind)).toEqual(['knowledge', 'web']);
  });

  it('没有工具调用时不产生来源', () => {
    expect(collectToolSources(undefined)).toEqual([]);
  });
});

describe('SourcesSection', () => {
  it('按序号渲染标题链接、块序号与摘录', () => {
    render(
      <SourcesSection
        sources={extractSources('query_knowledge', {
          items: [
            { doc_id: 'doc-1', title: '雷达校准手册', path: '运维/手册', chunk_index: 1, excerpt: '零点漂移检查' },
            { doc_id: 'doc-2', title: '值班 SOP', chunk_index: 0, excerpt: '交接清单' },
          ],
        })}
      />,
    );

    expect(screen.getByRole('region', { name: '来源' })).toBeInTheDocument();
    expect(screen.getByText('[1]')).toBeInTheDocument();
    expect(screen.getByText('[2]')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '雷达校准手册' })).toHaveAttribute('href', '/knowledge?doc=doc-1');
    expect(screen.getByText('第 2 块')).toBeInTheDocument();
    expect(screen.getByText('运维/手册')).toBeInTheDocument();
    expect(screen.getByText('零点漂移检查')).toBeInTheDocument();
  });

  it('暂无来源时不渲染空区块', () => {
    const { container } = render(<SourcesSection sources={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
