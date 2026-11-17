// 图表锚点解析：按 [[图:关键词]] 把图表插到正文对应段落，未匹配的兜底末尾。
import { describe, expect, it } from 'vitest';
import { segmentBodyWithCharts } from '@/lib/chartAnchors';

const arts = [
  { title: '支付异常状态码速率（近1h）' },
  { title: '各环节水位与告警阈值对比' },
  { title: '慢查询速率趋势' },
];

describe('segmentBodyWithCharts', () => {
  it('把锚点替换为标题命中的图表段', () => {
    const segs = segmentBodyWithCharts('先看速率。\n[[图:异常状态码速率]]\n再看水位。', arts);
    expect(segs.map((s) => s.type)).toEqual(['text', 'chart', 'text', 'chart', 'chart']);
    const charts = segs.filter((s) => s.type === 'chart');
    expect(charts[0]).toMatchObject({ artifact: { title: '支付异常状态码速率（近1h）' } });
  });

  it('多个锚点按出现顺序各取一个未用产出物', () => {
    const segs = segmentBodyWithCharts(
      'A[[图:水位]]B[[图:慢查询]]C',
      arts,
    );
    const charts = segs.filter((s) => s.type === 'chart');
    // 两个锚点各取一张，未用的第 1 张兜底到末尾
    expect(charts.map((s: any) => s.artifact.title)).toEqual([
      '各环节水位与告警阈值对比',
      '慢查询速率趋势',
      '支付异常状态码速率（近1h）',
    ]);
  });

  it('关键词与标题词序/间隔不同也能宽松匹配（子序列）', () => {
    const segs = segmentBodyWithCharts('看拓扑。[[图:拓扑边错误率]]完', [
      { title: '链路拓扑各边错误率与调用量' },
    ]);
    const charts = segs.filter((s) => s.type === 'chart');
    expect(charts).toHaveLength(1);
    expect(charts[0]).toMatchObject({ artifact: { title: '链路拓扑各边错误率与调用量' } });
    // 锚点不再残留在文本里
    const text = segs.map((s) => (s.type === 'text' ? s.text : '')).join('');
    expect(text).not.toContain('[[图');
  });

  it('匹配不到时保留锚点原文，不吞内容', () => {
    const segs = segmentBodyWithCharts('无图段落[[图:不存在的图]]结束', []);
    const text = segs.map((s) => (s.type === 'text' ? s.text : '')).join('');
    expect(text).toBe('无图段落[[图:不存在的图]]结束');
    expect(segs.every((s) => s.type === 'text')).toBe(true);
  });

  it('没有锚点时全部产出物按原顺序追加末尾', () => {
    const segs = segmentBodyWithCharts('纯文字正文', arts);
    expect(segs[0]).toEqual({ type: 'text', text: '纯文字正文' });
    expect(segs.slice(1).map((s: any) => s.artifact.title)).toEqual(arts.map((a) => a.title));
  });

  it('空正文只有产出物段', () => {
    const segs = segmentBodyWithCharts('', arts);
    expect(segs.every((s) => s.type === 'chart')).toBe(true);
    expect(segs).toHaveLength(3);
  });
});
