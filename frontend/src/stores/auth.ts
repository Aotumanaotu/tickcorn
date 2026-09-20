import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api, bindAuth } from '@/api/client'
import type { User } from '@/api/types'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const token = ref<string>('')
  const booted = ref(false)
  let bootPromise: Promise<void> | null = null

  const isAuthenticated = computed(() => user.value !== null)
  const role = computed(() => user.value?.role ?? '')
  const permissions = computed<ReadonlySet<string>>(
    () => new Set(user.value?.permissions ?? []),
  )

  function can(perm: string): boolean {
    return permissions.value.has(perm)
  }

  function applyToken(t: string, u: User): void {
    token.value = t
    user.value = u
  }

  function clearSession(): void {
    token.value = ''
    user.value = null
  }

  async function login(username: string, password: string): Promise<void> {
    const res = await api.login(username, password)
    applyToken(res.access_token, res.user)
  }

  /** Restore session via the HttpOnly refresh cookie (call once at boot). */
  function boot(): Promise<void> {
    if (bootPromise) return bootPromise
    bootPromise = (async () => {
      try {
        const res = await api.refresh()
        applyToken(res.access_token, res.user)
      } catch {
        clearSession()
      } finally {
        booted.value = true
      }
    })()
    return bootPromise
  }

  async function logout(): Promise<void> {
    try {
      await api.logout()
    } catch {
      /* best effort — clear locally regardless */
    }
    clearSession()
  }

  // Wire the API client to this store's in-memory token.
  bindAuth(
    () => token.value || null,
    () => clearSession(),
    (res) => applyToken(res.access_token, res.user),
  )

  return {
    user,
    booted,
    isAuthenticated,
    role,
    login,
    boot,
    logout,
    can,
  }
})
