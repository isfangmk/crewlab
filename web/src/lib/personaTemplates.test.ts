// 人设模板必须四块齐全：只填正文的模板等于让用户以为配好了。
import { describe, expect, it } from 'vitest';

import { OUTPUT_FORMATS } from '@/api/agents';
import { PERSONA_TEMPLATES } from './personaTemplates';

describe('personaTemplates', () => {
  it('每条模板都有正文、关键提醒、合法输出格式与至少一条示例', () => {
    expect(PERSONA_TEMPLATES.length).toBeGreaterThan(0);
    for (const tpl of PERSONA_TEMPLATES) {
      expect(tpl.id).not.toBe('');
      expect(tpl.zh).not.toBe('');
      expect(tpl.en).not.toBe('');
      expect(tpl.prompt.trim()).not.toBe('');
      expect(tpl.criticalReminder.trim()).not.toBe('');
      expect(OUTPUT_FORMATS).toContain(tpl.outputFormat);
      expect(tpl.examples.length).toBeGreaterThan(0);
      for (const example of tpl.examples) {
        expect(example.input.trim()).not.toBe('');
        expect(example.output.trim()).not.toBe('');
      }
    }
  });

  it('模板 id 唯一', () => {
    const ids = PERSONA_TEMPLATES.map((tpl) => tpl.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
