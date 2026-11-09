import { Outlet } from 'react-router-dom';

/** 对话区外壳：会话列表已迁到独立「会话」页，这里只渲染子路由。 */
export function ChatLayout() {
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      <Outlet />
    </div>
  );
}
