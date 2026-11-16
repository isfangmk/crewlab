import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

/** 首页输入区开关默认值（思考 / 联网），随建会话写入会话数据；
 *  会话内的开关以会话数据为准（换页面打开即同步），不再共用这份默认值。 */
type ChatPrefsState = {
  thinkingEnabled: boolean;
  webSearchEnabled: boolean;
  setThinkingEnabled(v: boolean): void;
  setWebSearchEnabled(v: boolean): void;
};

export const useChatPrefs = create<ChatPrefsState>()(
  persist(
    (set) => ({
      thinkingEnabled: false,
      webSearchEnabled: false,
      setThinkingEnabled: (thinkingEnabled) => set({ thinkingEnabled }),
      setWebSearchEnabled: (webSearchEnabled) => set({ webSearchEnabled }),
    }),
    {
      name: 'ongrid.chat-prefs',
      storage: createJSONStorage(() => localStorage),
    },
  ),
);
