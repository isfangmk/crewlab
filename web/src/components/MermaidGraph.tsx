// MermaidGraph — 共享 Mermaid 图渲染组件。
//
// 任务图拓扑、知识库图表等统一走这里：动态加载 mermaid（按需 chunk），
// 主题跟随明暗模式，配色走项目语义 token；渲染失败时退回源码展示，内容不丢。
//
// 单个图用 mermaid.render 渲染到容器；与 Knowledge 页 markdown 围栏里的
// rehype-mermaid 批处理路径（pre.mermaid）互不影响。

import { useEffect, useId, useRef, useState } from 'react';
import { useThemeMode } from '@/store/mode';
import { cn } from '@/lib/cn';

type MermaidModule = {
  initialize: (config: Record<string, unknown>) => void;
  render: (id: string, definition: string) => Promise<{ svg: string; bindFunctions?: (element: Element) => void }>;
};

let mermaidPromise: Promise<MermaidModule> | null = null;

/** 按需加载 mermaid；同一页面只加载一次。 */
function loadMermaid(): Promise<MermaidModule> {
  if (mermaidPromise === null) {
    mermaidPromise = import('mermaid').then((mod) => mod.default as unknown as MermaidModule);
  }
  return mermaidPromise;
}

/** 语义 token 色（与 tailwind config --accent/--ok/--warn/--danger 一致）。 */
const TOKENS = {
  accent: '#8c6df0',
  accentFg: '#ffffff',
  nodeBg: '#18181b',
  nodeBorder: '#8c6df0',
  text: '#e4e4e7',
  line: '#71717a',
  ok: '#10b981',
  warn: '#f59e0b',
  danger: '#ef4444',
  info: '#30a6d0',
} as const;

export function MermaidGraph({
  definition,
  className,
  ariaLabel,
  onNodeClick,
  callbackName,
}: {
  definition: string;
  className?: string;
  ariaLabel?: string;
  onNodeClick?: (nodeUid: string) => void;
  /** click 指令引用的全局回调名；传 onNodeClick 时必填，且与 definition 里的名字一致。 */
  callbackName?: string;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const { resolved } = useThemeMode();
  const reactId = useId();
  const [failed, setFailed] = useState(false);
  // mermaid.render 的 id 必须是合法 CSS 选择器片段；useId 含冒号，过滤掉。
  const renderId = `mermaid-${reactId.replace(/[^a-zA-Z0-9_-]/g, '')}`;
  // click 回调挂到 window 上，随组件卸载清理；名字由调用方保证与 definition 一致。
  const clickRef = useRef(onNodeClick);
  clickRef.current = onNodeClick;

  useEffect(() => {
    if (!onNodeClick || !callbackName) return;
    const w = window as unknown as Record<string, unknown>;
    w[callbackName] = (uid: string) => clickRef.current?.(uid);
    return () => {
      delete w[callbackName];
    };
  }, [callbackName, onNodeClick]);

  useEffect(() => {
    let cancelled = false;
    setFailed(false);
    const host = hostRef.current;
    if (!host || definition.trim() === '') return;

    void loadMermaid()
      .then((mermaid) => {
        mermaid.initialize({
          startOnLoad: false,
          // click call 需要 loose：严格模式禁用函数调用，节点点击将失效。
          securityLevel: 'loose',
          theme: 'base',
          themeVariables: {
            darkMode: resolved === 'dark',
            background: 'transparent',
            primaryColor: TOKENS.accent,
            primaryTextColor: TOKENS.accentFg,
            primaryBorderColor: TOKENS.nodeBorder,
            lineColor: TOKENS.line,
            secondaryColor: TOKENS.nodeBg,
            tertiaryColor: TOKENS.nodeBg,
            fontSize: '12px',
            fontFamily: 'ui-sans-serif, system-ui, sans-serif',
          },
        });
        return mermaid.render(renderId, definition);
      })
      .then(({ svg, bindFunctions }) => {
        if (cancelled || !hostRef.current) return;
        hostRef.current.innerHTML = svg;
        bindFunctions?.(hostRef.current);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [definition, resolved, renderId]);

  if (failed) {
    return (
      <pre
        className={cn(
          'overflow-x-auto rounded-lg border border-zinc-800 bg-zinc-950/60 p-2 text-[10px] leading-relaxed text-zinc-300',
          className,
        )}
      >
        {definition}
      </pre>
    );
  }

  return (
    <div
      ref={hostRef}
      role="img"
      aria-label={ariaLabel}
      className={cn('mermaid-host flex justify-center overflow-x-auto', className)}
    />
  );
}
