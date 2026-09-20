import { defineStore } from 'pinia'
import { ref } from 'vue'

export type ThemeName = 'dark' | 'light'

const STORAGE_KEY = 'mt-theme'

function currentDomTheme(): ThemeName {
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'
}

export const useThemeStore = defineStore('theme', () => {
  const theme = ref<ThemeName>(currentDomTheme())

  function set(next: ThemeName): void {
    theme.value = next
    document.documentElement.dataset.theme = next
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      /* storage unavailable — theme stays for this session only */
    }
  }

  function toggle(): void {
    set(theme.value === 'dark' ? 'light' : 'dark')
  }

  /** Sync store with whatever the anti-FOUC script already applied. */
  function init(): void {
    theme.value = currentDomTheme()
  }

  return { theme, init, set, toggle }
})
