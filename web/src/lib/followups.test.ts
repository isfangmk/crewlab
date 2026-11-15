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
    // 最后一行没写完（无换行收尾），不闪现半句话
    expect(questions).toEqual([]);
  });

  it('随流渐进：写完的行先出题，收尾不再整卡突现', () => {
    // 第一行刚写完（有换行收尾）：只出第一题
    expect(parseFollowups('答。\n```suggestions\n查上周的工时\n').questions).toEqual([
      '查上周的工时',
    ]);
    // 第二行写完：两题；第三行还在写（无换行）不计
    expect(parseFollowups('答。\n```suggestions\n查上周的工时\n导出为表格\n按项目汇总').questions).toEqual([
      '查上周的工时',
      '导出为表格',
    ]);
    // 围栏闭合：与渐进结果一致，不跳变
    expect(parseFollowups('答。\n```suggestions\n查上周的工时\n导出为表格\n```').questions).toEqual([
      '查上周的工时',
      '导出为表格',
    ]);
  });

  it('块在正文开头时同样渐进', () => {
    const { body, questions } = parseFollowups('```suggestions\n问题一\n');
    expect(body).toBe('');
    expect(questions).toEqual(['问题一']);
  });
});
