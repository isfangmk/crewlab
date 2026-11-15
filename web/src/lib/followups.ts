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

function toQuestions(block: string): string[] {
  return block
    .split('\n')
    .map((line) => line.trim().replace(/^([-*•]\s+|\d+[.、)]\s+)/, '').trim())
    .filter((line) => line !== '')
    .slice(0, 3);
}

export function parseFollowups(content: string): Followups {
  const match = SUGGESTIONS_BLOCK.exec(content);
  if (match) {
    return { body: content.slice(0, match.index).trimEnd(), questions: toQuestions(match[1]) };
  }
  // 未闭合的建议块（流式中途或回答被打断）：整段从正文隐藏，避免围栏泄漏成代码块；
  // 已写完的行随流渐进出题，收尾不再整卡突现
  const openIdx = content.indexOf('\n```suggestions');
  let tail: string | null = null;
  if (openIdx >= 0) tail = content.slice(openIdx + '\n```suggestions'.length);
  else if (content.startsWith('```suggestions')) tail = content.slice('```suggestions'.length);
  if (tail !== null) {
    // 最后一行没写完（没有换行收尾）不计，避免半句话闪一下又变
    const complete = tail.endsWith('\n') ? tail : tail.slice(0, tail.lastIndexOf('\n') + 1);
    return {
      body: (openIdx >= 0 ? content.slice(0, openIdx) : '').trimEnd(),
      questions: toQuestions(complete),
    };
  }
  return { body: content, questions: [] };
}
