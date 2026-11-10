// 长会话折叠的判定口径集中在这里：页面只负责渲染，阈值和边界由测试固定。
//
// 折叠是纯展示行为——消息仍全量拉取，展开不触发请求，导出/搜索不受影响。
// 阈值 60 / 保留 40：短会话整段铺开更易读；超过 60 条后首屏只需最近 40 条，
// 再往上滚可以点「展开更早」拿回完整历史。
export const COLLAPSE_THRESHOLD = 60;
export const COLLAPSE_KEEP = 40;

/**
 * 计算被折叠的早期消息条数。
 *
 * - 条数不超过阈值：不折叠（返回 0），页面上不出现任何折叠按钮。
 * - 已展开：返回 0。
 * - 其余：隐藏 total - keep 条，至少保留最新 keep 条。
 */
export function collapsedMessageCount(
  total: number,
  showAll: boolean,
  threshold: number = COLLAPSE_THRESHOLD,
  keep: number = COLLAPSE_KEEP,
): number {
  if (!Number.isInteger(total) || total < 0) {
    throw new Error(`消息条数必须是非负整数: ${total}`);
  }
  if (!Number.isInteger(keep) || keep < 1) {
    throw new Error(`保留条数必须是正整数: ${keep}`);
  }
  if (keep > threshold) {
    throw new Error(`保留条数不能大于折叠阈值: keep=${keep} threshold=${threshold}`);
  }
  if (showAll || total <= threshold) return 0;
  return Math.max(0, total - keep);
}
