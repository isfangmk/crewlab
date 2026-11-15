// 建议问题块：与 backend/app/adk_runtime/agent_factory.py 的 FOLLOWUPS_INSTRUCTION 同规约——
// 回答末尾的 ```suggestions 围栏块，一行一个提问。该块是界面渲染用的元数据，
// 不算正文：渲染前剥离，历史回放同样解析（正文存储保持模型原话）。

export type Followups = {
  /** 剥离建议块后的正文 */
  body: string;
  /** 1-3 条建议问题 */
  questions: string[];
};

const SUGGESTIONS_BLOCK = /(?:^|\n)```suggestions[ \t]*\n([\s\S]*?)```[ \t]*\s*$/;

export function parseFollowups(content: string): Followups {
  const match = SUGGESTIONS_BLOCK.exec(content);
  if (match) {
    const questions = match[1]
      .split('\n')
      .map((line) => line.trim().replace(/^([-*•]\s+|\d+[.、)]\s+)/, '').trim())
      .filter((line) => line !== '')
      .slice(0, 3);
    return { body: content.slice(0, match.index).trimEnd(), questions };
  }
  // 未闭合的建议块（流式中途或回答被打断）：整段从正文隐藏，避免围栏泄漏成代码块
  const openIdx = content.indexOf('\n```suggestions');
  if (openIdx >= 0) return { body: content.slice(0, openIdx).trimEnd(), questions: [] };
  if (content.startsWith('```suggestions')) return { body: '', questions: [] };
  return { body: content, questions: [] };
}
