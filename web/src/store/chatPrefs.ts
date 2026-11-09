import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

/** 对话输入区开关偏好（思考 / 联网），跨首页与会话页保持。 */
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
