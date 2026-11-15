// ChartCanvas — 按统一主题渲染 ECharts 画布。
//
// ECharts 体积不小（约 1MB），组件走 React.lazy 动态加载，
// 只有真的出现图表时才会拉这个 chunk。画布一律以 chartTheme 注册的
// 主题初始化：图表结构（chart_spec）零配色，颜色只在主题里。
//
// 图表来自 run_python chart() 产出物：payload 在执行完即固定，
// 不做流式更新，option 变更即全量重绘。

import { useEffect, useRef } from 'react';
import { cn } from '@/lib/cn';
import { CHART_THEME, CHART_THEME_NAME } from '@/lib/chartTheme';

type EChartsInstance = {
  setOption: (option: Record<string, unknown>, notMerge?: boolean) => void;
  resize: () => void;
  dispose: () => void;
};

type EChartsModule = {
  init: (dom: HTMLElement, theme?: string) => EChartsInstance;
  registerTheme: (name: string, theme: Record<string, unknown>) => void;
};

export const CHART_HEIGHT_CLASS = 'h-[18rem]';

let echartsPromise: Promise<EChartsModule> | null = null;

/** 按需加载 echarts 并注册主题；同一页面只加载一次。 */
function loadECharts(): Promise<EChartsModule> {
  if (echartsPromise === null) {
    echartsPromise = import('echarts').then((mod) => {
      const echarts = mod as unknown as EChartsModule;
      echarts.registerTheme(CHART_THEME_NAME, CHART_THEME);
      return echarts;
    });
  }
  return echartsPromise;
}

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

  // 卸载时销毁；实例本身跨 option 变更复用
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
      void loadECharts().then((echarts) => {
        // 卸载（ref 归 null）或已初始化过则跳过
        if (hostRef.current === null || chartRef.current !== null) return;
        const chart = echarts.init(hostRef.current, CHART_THEME_NAME);
        chartRef.current = chart;
        chart.setOption(optionRef.current, true);
      });
      return;
    }
    chartRef.current.setOption(option, true);
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
