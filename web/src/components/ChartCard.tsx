// ChartCard — 在对话气泡里渲染 ECharts 图表。
//
// ECharts 体积不小（约 1MB），所以组件走 React.lazy 动态加载，
// 只有真的出现图表时才会拉这个 chunk。

import { useEffect, useRef } from 'react';
import { useI18n } from '@/i18n/locale';
import { cn } from '@/lib/cn';
import type { ChartSpec } from '@/lib/chartSpec';

type EChartsInstance = {
  setOption: (option: Record<string, unknown>, notMerge?: boolean) => void;
  resize: () => void;
  dispose: () => void;
};

type EChartsModule = {
  init: (dom: HTMLElement, theme?: string) => EChartsInstance;
};

export const CHART_HEIGHT_CLASS = 'h-[18rem]';

let echartsPromise: Promise<EChartsModule> | null = null;

/** 按需加载 echarts；同一页面只加载一次。 */
async function loadECharts(): Promise<EChartsModule> {
  if (echartsPromise === null) {
    echartsPromise = import('echarts').then((mod) => mod as unknown as EChartsModule);
  }
  return echartsPromise;
}

// option 更新的节流间隔：流式逐 token 出 JSON 时不能每个字都打到 echarts
const OPTION_UPDATE_MS = 250;

export function ChartCanvas({
  option,
  className,
}: {
  option: Record<string, unknown>;
  className?: string;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<EChartsInstance | null>(null);
  const optionRef = useRef(option);

  // 卸载时销毁；实例本身跨 option 更新复用，避免流式时反复销毁重建
  useEffect(() => {
    return () => {
      chartRef.current?.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    optionRef.current = option;
    const host = hostRef.current;
    if (host === null) return;

    if (chartRef.current === null) {
      // 首帧立即初始化；隐藏态（宽高为 0）拿不到画布，等下次 option 变更再试
      if (host.clientWidth === 0 || host.clientHeight === 0) return;
      void loadECharts().then((echarts) => {
        // 卸载（ref 归 null）或已初始化过则跳过
        if (hostRef.current === null || chartRef.current !== null) return;
        const chart = echarts.init(host, undefined);
        chartRef.current = chart;
        chart.setOption(optionRef.current, true);
      });
      return;
    }

    // 已有实例：节流合并成最后一次 setOption（notMerge 全量替换）
    const timer = window.setTimeout(() => {
      chartRef.current?.setOption(option, true);
    }, OPTION_UPDATE_MS);
    return () => window.clearTimeout(timer);
  }, [option]);

  // 容器尺寸变化（侧栏展开 / 窗口缩放）时重画
  useEffect(() => {
    const host = hostRef.current;
    if (host === null) return;
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => chartRef.current?.resize());
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  return <div ref={hostRef} className={cn('w-full', CHART_HEIGHT_CLASS, className)} />;
}

export function ChartCard({ spec, className }: { spec: ChartSpec; className?: string }) {
  const { tr } = useI18n();
  return (
    <figure
      className={cn(
        'w-full overflow-hidden rounded-lg border border-zinc-800/60 bg-zinc-900/40',
        className,
      )}
    >
      {spec.title !== '' && (
        <figcaption className="border-b border-zinc-800/60 px-3 py-2 text-[12px] font-medium text-zinc-200">
          {spec.title}
        </figcaption>
      )}
      <div className="p-2">
        <ChartCanvas option={spec.option} />
      </div>
      {spec.summary !== '' && (
        <p className="border-t border-zinc-800/60 px-3 py-2 text-[11px] leading-relaxed text-zinc-500">
          {tr('结论：', 'Summary: ')}
          {spec.summary}
        </p>
      )}
    </figure>
  );
}
