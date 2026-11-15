// YAML frontmatter 的解析：只为「展示」服务。
// md 文档若以 --- 包裹的元信息块开头（title/tags/...），直接交给
// ReactMarkdown 会被当成 setext 标题渲染成粗体大字 —— 业界通行做法
// （GitHub/Obsidian）是把 frontmatter 从正文剥离、单独弱化展示。
// 解析走 js-yaml（标准 YAML 语义）；嵌套结构等超出展示需求的形态返回
// null，调用方按原文渲染，保证不弄坏内容。

import { load } from 'js-yaml';

export interface ParsedFrontmatter {
  /** 保序的 key/value 列表；数组值（tags 等）解析为 string[] */
  meta: [string, string | string[]][];
  /** 剥离 frontmatter 后的正文 */
  body: string;
}

function isScalar(value: unknown): boolean {
  return (
    value === null ||
    typeof value === 'string' ||
    typeof value === 'number' ||
    typeof value === 'boolean'
  );
}

/**
 * 解析文档开头的 YAML frontmatter。无 frontmatter、YAML 非法、
 * 或值为嵌套结构（超出展示需求）时返回 null —— 调用方按原文渲染。
 */
export function splitFrontmatter(md: string): ParsedFrontmatter | null {
  const m = /^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/.exec(md);
  if (!m) return null;

  let parsed: unknown;
  try {
    parsed = load(m[1]);
  } catch {
    return null;
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return null;
  }

  const meta: [string, string | string[]][] = [];
  for (const [key, value] of Object.entries(parsed)) {
    if (isScalar(value)) {
      meta.push([key, String(value ?? '')]);
    } else if (Array.isArray(value) && value.every(isScalar)) {
      meta.push([key, value.map((v) => String(v ?? ''))]);
    } else {
      // 嵌套结构等超出展示需求：放弃解析，按原文渲染
      return null;
    }
  }
  if (meta.length === 0) return null;
  return { meta, body: md.slice(m[0].length) };
}

/** 仅取正文（搜索预览等场景用），无 frontmatter 时原样返回 */
export function stripFrontmatter(md: string): string {
  return splitFrontmatter(md)?.body ?? md;
}
