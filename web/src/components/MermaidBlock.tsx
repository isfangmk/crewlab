// MermaidBlock — 把 ```mermaid 围栏渲染成图（知识库文档查看器用）。
//
// mermaid 体积不小，走动态 import 按需加载；解析失败退回源码展示，
// 内容不丢。主题对齐产品暗色：调色板靛蓝为主色。

import { useEffect, useState } from 'react';
import { CodeBlock } from '@/components/ui';

type MermaidModule = {
  initialize: (config: Record<string, unknown>) => void;
  render: (id: string, code: string) => Promise<{ svg: string }>;
};

let mermaidPromise: Promise<MermaidModule> | null = null;
// render 的元素 id 必须全文档唯一
let mermaidSeq = 0;

/** 按需加载 mermaid 并初始化主题；同一页面只加载一次。 */
function loadMermaid(): Promise<MermaidModule> {
  if (mermaidPromise === null) {
    mermaidPromise = import('mermaid').then((mod) => {
      const mermaid = (mod as { default: MermaidModule }).default;
      mermaid.initialize({
        startOnLoad: false,
        theme: 'dark',
        themeVariables: {
          primaryColor: '#6366f1',
          primaryBorderColor: '#818cf8',
          primaryTextColor: '#e4e4e7',
          lineColor: '#71717a',
          textColor: '#e4e4e7',
          fontSize: '13px',
        },
      });
      return mermaid;
    });
  }
  return mermaidPromise;
}

export function MermaidBlock({ code }: { code: string }) {
  const [svg, setSvg] = useState('');
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const id = `mermaid-graph-${++mermaidSeq}`;
    let cancelled = false;
    loadMermaid()
      .then((mermaid) => mermaid.render(id, code))
      .then((result) => {
        if (!cancelled) setSvg(result.svg);
      })
      .catch(() => {
        // 解析失败按源码展示：图没画出来但内容不丢
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [code]);

  if (failed) return <CodeBlock>{code}</CodeBlock>;
  if (svg === '') {
    return <div className="my-2 h-24 animate-pulse rounded-lg border border-zinc-800/40 bg-zinc-900/20" />;
  }
  return (
    <div
      className="my-2 overflow-x-auto rounded-lg border border-zinc-800/40 bg-zinc-900/40 px-3 py-2"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
