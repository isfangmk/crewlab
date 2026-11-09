import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

export type Session = {
  access_token: string;
  role: string;
  email: string;
};

export type AuthState = {
  token: string | null;
  email: string | null;
  role: string | null;
  setSession(s: Session): void;
  logout(): void;
};

export const useAuth = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      email: null,
      role: null,
      setSession: (s) =>
        set({
          token: s.access_token,
          role: s.role,
          email: s.email,
        }),
      logout: () => set({ token: null, email: null, role: null }),
    }),
    {
      name: 'ongrid.auth',
      storage: createJSONStorage(() => localStorage),
    }
  )
);

export function getToken(): string | null {
  return useAuth.getState().token;
}
