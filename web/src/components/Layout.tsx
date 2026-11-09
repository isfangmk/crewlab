import { Suspense, useEffect } from 'react';
import { Outlet } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { CommandPalette } from './CommandPalette';
import { useUi } from '@/store/ui';
import { useSystemTimezone } from '@/store/systemTimezone';
import { tr as trInline } from '@/i18n/locale';

export function Layout() {
  const paletteOpen = useUi((s) => s.paletteOpen);
  const setPaletteOpen = useUi((s) => s.setPaletteOpen);
  const refreshTimezone = useSystemTimezone((s) => s.refresh);
  const tzLoaded = useSystemTimezone((s) => s.loaded);
  const tzError = useSystemTimezone((s) => s.error);

  useEffect(() => {
    void refreshTimezone();
  }, [refreshTimezone]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const ctrl = e.ctrlKey || e.metaKey;
      if (!ctrl) return;
      if (e.key === 'p' || e.key === 'P') {
        e.preventDefault();
        setPaletteOpen(!useUi.getState().paletteOpen);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [setPaletteOpen]);

  return (
    <div className="flex h-screen min-h-0 w-screen min-w-0 overflow-hidden bg-zinc-950 text-zinc-100">
      <Sidebar />
      <div className="flex min-h-0 min-w-0 flex-1 overflow-hidden">
        {tzError !== null ? (
          <main className="flex flex-1 items-center justify-center px-6 text-sm text-red-400">
            {trInline('系统时区加载失败：', 'Failed to load system timezone: ')}
            {tzError}
          </main>
        ) : !tzLoaded ? (
          <MainLoading />
        ) : (
          <Suspense fallback={<MainLoading />}>
            <Outlet />
          </Suspense>
        )}
      </div>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
}

function MainLoading() {
  // Suspense 占位用无 hook 的 trInline，避免首屏闪一下时订阅 i18n。
  const text = trInline('加载中…', 'Loading…');
  return (
    <main className="flex flex-1 items-center justify-center text-sm text-zinc-500">
      {text}
    </main>
  );
}
