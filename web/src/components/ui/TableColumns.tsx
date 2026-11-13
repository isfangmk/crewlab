import { cn } from '@/lib/cn';

// 分页列表的列宽口径：所有列表页共用同一套档位，宽度不再各页各写。
//
// 为什么要固定档位：同一类信息在不同页面散落着 5.5rem / 6rem / 7rem / 7.5rem
// 这类随手写的宽度，扫两页就发现「同样一列这里宽那里窄」，横向对比读不下去。
// 收成一套档位后，改一次宽度全站跟着变。
//
// 档位怎么分：
// - 主内容列（名称、标题、描述）：flex，吃掉剩余宽度；
// - 标识类列（ID、key、版本、状态胶囊）：固定窄档；
// - 时间列：统一 11rem，完整日期时间放得下又不浪费；
// - 操作列：按最长按钮文案给固定档，图标按钮用最窄档。
export const COL = {
  /** 主内容列：吃掉剩余宽度。配 table-fixed 使用。 */
  main: 'w-full max-w-0',
  /** 标识符：等宽 ID / key / 版本号。 */
  id: 'w-[13rem]',
  /** 短标识：状态、类别、数量这类胶囊。 */
  tag: 'w-[7rem]',
  /** 人 / 触发者这类邮箱或名字。 */
  person: 'w-[13rem]',
  /** 完整日期时间。 */
  time: 'w-[11rem]',
  /** 详情文本：路径、URL、动作名。 */
  detail: 'w-[12rem]',
  /** 操作列：带文字按钮（导出 / 编辑 / 删除）。 */
  actions: 'w-[13rem]',
  /** 操作列：纯图标按钮。 */
  actionsIcon: 'w-[5.5rem]',
  /** 序号列。 */
  index: 'w-[3rem]',
  /** 拖拽把手列。 */
  handle: 'w-[1.5rem]',
} as const;

/**
 * 表格容器的统一类名：固定布局 + 最小宽度，避免窄屏把列挤变形。
 */
export function tableLayout(minWidthRem: number) {
  return cn('og-action-table w-full text-sm', 'table-fixed', `min-w-[${minWidthRem}rem]`);
}
