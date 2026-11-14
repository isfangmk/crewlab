// 画布颜色令牌：点阵、连线与强调色只在这里定义，浅色/深色各一份。
// 节点与连线组件都从这里取色，避免各文件各写一遍十六进制值。

export const DOT_COLOR = { light: '#cbd5e1', dark: '#27272a' } as const;

/** 连线与内置箭头 marker 的颜色；浅色画布上比点阵更深。 */
export const EDGE_COLOR = { light: '#cbd5e1', dark: '#3f3f46' } as const;

/** 连线悬停 / 选中 / 拖拽预览时的强调色。 */
export const EDGE_ACTIVE_COLOR = '#6366f1';
