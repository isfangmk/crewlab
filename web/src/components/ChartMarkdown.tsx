// ChartMarkdown — 在对话正文里把 ```echarts 围栏渲染成图表。
//
// 模型在回答里用围栏给出 ECharts option，例如：
//
//   ```echarts
//   {"xAxis": {...}, "series": [...]}
//   ```
//
// 围栏内容解析失败时退回普通代码块，不吞掉内容。
//
// 只接管 pre（围栏的容器）：code 保持默认渲染，
// 这样非图表围栏的样式完全不受影响。

import { useMemo, type ReactNode } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChartCard } from '@/components/ChartCard';
import { isChartFence, parseChartSpec } from '@/lib/chartSpec';
import { CodeBlock } from '@/components/ui';

type FenceChild = {
  props?: { className?: string; children?: unknown };
};

/** 从 pre 的子节点里取出围栏信息；不是图表围栏返回 null。 */
function fenceInfo(children: ReactNode): { className?: string; text: string } | null {
  const child = Array.isArray(children) ? children[0] : children;
  if (!child || typeof child !== 'object') return null;
  const props = (child as FenceChild).props;
  if (props === undefined || props === null) return null;
  if (!isChartFence(props.className)) return null;
  const text = String(props.children ?? '').replace(/\n$/, '');
  return { className: props.className, text };
}

function ChartFence({ text }: { text: string }) {
  const spec = useMemo(() => parseChartSpec(text), [text]);
  if (spec === null) {
    // 不是合法图表：按普通代码块展示，内容不丢
    return <CodeBlock>{text}</CodeBlock>;
  }
  return <ChartCard spec={spec} />;
}

/** 正文渲染用的组件表：只接管图表围栏，其余交给默认渲染。 */
const components: Components = {
  pre({ children }) {
    const info = fenceInfo(children);
    if (info !== null) {
      return <ChartFence text={info.text} />;
    }
    return <>{children}</>;
  },
};

export function ChartMarkdown({ content }: { content: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {content}
    </ReactMarkdown>
  );
}
