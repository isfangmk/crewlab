// ChartMarkdown — 在对话正文里把 ```echarts 围栏渲染成图表。
//
// 模型在回答里用围栏给出 ECharts option，例如：
//
//   ```echarts
//   {"xAxis": {...}, "series": [...]}
//   ```
//
// 流式（streaming）时渐进渲染：围栏内容还是半截 JSON 就先修复出
// 「当前最大完整前缀」画图，数据随 token 逐步补全，不再刷原始 JSON；
// 还没有可画数据（只有标题/坐标系）时保持骨架，不挂空画布。
// 非流式保持严格解析：围栏内容解析失败时退回普通代码块，不吞掉内容。
//
// 只接管 pre（围栏的容器）：code 保持默认渲染，
// 这样非图表围栏的样式完全不受影响。

import { useMemo, type ReactNode } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChartCard, ChartCardSkeleton } from '@/components/ChartCard';
import {
  chartTitleHint,
  hasDrawableData,
  isChartFence,
  parseChartSpec,
  parseChartSpecStreaming,
} from '@/lib/chartSpec';
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

function ChartFence({ text, streaming }: { text: string; streaming: boolean }) {
  const spec = useMemo(
    () => (streaming ? parseChartSpecStreaming(text) : parseChartSpec(text)),
    [text, streaming],
  );
  // 流式中要等「有可画数据」才上画布：只有标题/坐标系的空画布
  // 一直挂着比骨架更像图表坏了；数据一到就挂载并随 token 生长
  if (spec !== null && (!streaming || hasDrawableData(spec))) {
    return <ChartCard spec={spec} />;
  }
  if (streaming) {
    return <ChartCardSkeleton title={spec !== null ? chartTitleHint(spec) : ''} />;
  }
  // 不是合法图表：按普通代码块展示，内容不丢
  return <CodeBlock>{text}</CodeBlock>;
}

export function ChartMarkdown({
  content,
  streaming = false,
}: {
  content: string;
  streaming?: boolean;
}) {
  // 组件表按 streaming 重建：ChartFence 需要知道是不是流式中
  const components = useMemo<Components>(
    () => ({
      pre({ children }) {
        const info = fenceInfo(children);
        if (info !== null) {
          return <ChartFence text={info.text} streaming={streaming} />;
        }
        return <>{children}</>;
      },
    }),
    [streaming],
  );
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {content}
    </ReactMarkdown>
  );
}
