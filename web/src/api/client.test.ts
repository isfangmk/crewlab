// Content-Disposition 文件名解析：真实响应头形态（Starlette 行为）逐例验证。
import { describe, expect, it } from 'vitest';
import { parseContentDisposition } from './client';

describe('parseContentDisposition', () => {
  it('解析 RFC5987 的 filename*（非 ASCII 文件名，Starlette 对中文只发这种）', () => {
    const cd = "attachment; filename*=utf-8''%E7%BD%91%E7%BB%9C%E8%AE%BE%E5%A4%87%E5%B7%A1%E6%A3%80%E6%89%8B%E5%86%8C.md";
    expect(parseContentDisposition(cd)).toBe('网络设备巡检手册.md');
  });

  it('解析纯 ASCII 的 filename="…" 形式', () => {
    expect(parseContentDisposition('attachment; filename="report.pdf"')).toBe('report.pdf');
  });

  it('两种形式并存时优先 filename*（含编码的才是真名）', () => {
    const cd = "attachment; filename=\"fallback.txt\"; filename*=utf-8''%E5%B7%A1%E6%A3%80.txt";
    expect(parseContentDisposition(cd)).toBe('巡检.txt');
  });

  it('头缺失或两种形式都不在时回退 download', () => {
    expect(parseContentDisposition('')).toBe('download');
    expect(parseContentDisposition('attachment')).toBe('download');
  });

  it('filename* 编码串损坏时原样返回，不让下载整体失败', () => {
    expect(parseContentDisposition("attachment; filename*=utf-8''%ZZ%.md")).toBe('%ZZ%.md');
  });
});
