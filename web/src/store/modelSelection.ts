import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type { ModelSelection } from '@/components/ChatInput';

// 全局模型偏好（localStorage）。会话绑定模型仍写在后端 chat_sessions。
// selected 为 null 时调用方回落到模型目录默认值。
type ModelSelectionState = {
  selected: ModelSelection | null;
  setSelected(m: ModelSelection | null): void;
};

export const useModelSelection = create<ModelSelectionState>()(
  persist(
    (set) => ({
      selected: null,
      setSelected: (selected) => set({ selected }),
    }),
    {
      name: 'ongrid.model-selection',
      storage: createJSONStorage(() => localStorage),
    }
  )
);
