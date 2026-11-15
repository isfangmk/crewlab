// 建议问题块解析：与 backend/app/adk_runtime/agent_factory.py 的 FOLLOWUPS_INSTRUCTION 同规约。
import { describe, expect, it } from 'vitest';

import { parseFollowups } from './followups';

describe('parseFollowups', () => {
  it('剥离末尾建议块并解析问题', () => {
    const { body, questions } = parseFollowups(
      '回答正文。\n\n```suggestions\n下一步查上周的工时\n导出本周工时为表格\n按项目汇总本月工时\n```',
    );
    expect(body).toBe('回答正文。');
    expect(questions).toEqual(['下一步查上周的工时', '导出本周工时为表格', '按项目汇总本月工时']);
  });

  it('无建议块时正文原样', () => {
    const content = '普通回答，没有建议块。';
    expect(parseFollowups(content)).toEqual({ body: content, questions: [] });
  });

  it('去列表序号前缀并裁到 3 条', () => {
    const { questions } = parseFollowups(
      '答\n```suggestions\n1. 问题一\n- 问题二\n• 问题三\n问题四\n```',
    );
    expect(questions).toEqual(['问题一', '问题二', '问题三']);
  });

  it('块前后空白与空行容错', () => {
    const { body, questions } = parseFollowups('答\n\n```suggestions\n\n问题一\n\n```\n\n');
    expect(body).toBe('答');
    expect(questions).toEqual(['问题一']);
  });

  it('非末尾的普通代码围栏不误伤', () => {
    const content = '先看代码：\n```python\nprint(1)\n```\n以上。';
    expect(parseFollowups(content)).toEqual({ body: content, questions: [] });
  });

  it('未闭合的建议块不泄漏成正文（流式中途 / 打断）', () => {
    const { body, questions } = parseFollowups('答。\n\n```suggestions\n帮我查');
    expect(body).toBe('答。');
    expect(questions).toEqual([]);
  });
});
