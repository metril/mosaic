import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export type Theme = 'dark' | 'light'

interface ThemeState {
  theme: Theme
  setTheme: (t: Theme) => void
  toggle: () => void
}

/** Reflect the active theme onto <html data-theme> so the token overrides
 *  apply. Called on every change and once at boot. */
function apply(theme: Theme) {
  document.documentElement.setAttribute('data-theme', theme)
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set, get) => ({
      theme: 'dark',
      setTheme: (theme) => {
        apply(theme)
        set({ theme })
      },
      toggle: () => get().setTheme(get().theme === 'dark' ? 'light' : 'dark'),
    }),
    {
      name: 'screensaver-theme',
      onRehydrateStorage: () => (state) => {
        if (state) apply(state.theme)
      },
    },
  ),
)

/** Apply the persisted theme as early as possible (before first paint of the
 *  app tree). Safe to call once from main.tsx. */
export function initTheme() {
  apply(useThemeStore.getState().theme)
}
