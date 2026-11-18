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
  getDataURL: (opts: {
    type: 'png';
    pixelRatio: number;
    backgroundColor: string;
  }) => string;
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

/**
 * 把 ECharts option 导出为 PNG Blob（离屏渲染，用于产物下载与 ZIP 打包）。
 *
 * 复用与 ChartCanvas 相同的 loadECharts + 主题，保证导出图与预览一致；
 * 离屏容器必须挂到 DOM 且有实际尺寸（echarts 依据容器宽高布局），
 * 不能用 display:none。无论成功失败都 dispose 并移除容器。
 */
export async function chartOptionToPngBlob(
  option: Record<string, unknown>,
): Promise<Blob> {
  const echarts = await loadECharts();
  const host = document.createElement('div');
  host.style.position = 'fixed';
  host.style.left = '-10000px';
  host.style.top = '0';
  host.style.width = '800px';
  host.style.height = '500px';
  host.style.opacity = '0';
  host.style.pointerEvents = 'none';
  document.body.appendChild(host);
  let chart: EChartsInstance | null = null;
  try {
    chart = echarts.init(host, CHART_THEME_NAME);
    chart.setOption(option, true);
    // 等一帧让 echarts 完成首屏绘制，getDataURL 才有内容。
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    const dataUrl = chart.getDataURL({
      type: 'png',
      pixelRatio: 2,
      backgroundColor: '#ffffff',
    });
    const resp = await fetch(dataUrl);
    return await resp.blob();
  } finally {
    chart?.dispose();
    host.remove();
  }
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
