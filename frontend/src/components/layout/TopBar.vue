<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ConnectionBadge from '@/components/common/ConnectionBadge.vue'
import { useAuthStore } from '@/stores/auth'
import { useSystemStore } from '@/stores/system'
import { useThemeStore } from '@/stores/theme'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const theme = useThemeStore()
const system = useSystemStore()

const menuOpen = ref(false)

const pageTitle = computed(
  () => (route.meta.title as string | undefined) ?? 'MICROTERM',
)
const isDev = import.meta.env.DEV

async function handleLogout(): Promise<void> {
  menuOpen.value = false
  await auth.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <header class="topbar">
    <h1 class="page-title">{{ pageTitle }}</h1>

    <div class="topbar-right">
      <ConnectionBadge v-if="isDev" label="DEV" tone="warn" />
      <ConnectionBadge v-if="system.simulate" label="SIM" tone="warn" />

      <div v-if="system.isLive" class="live" title="Live market data">
        <span class="live-dot" />
        <span class="live-label">LIVE</span>
      </div>
      <div v-else class="live live-off" title="Gateway not streaming">
        <span class="live-dot off" />
        <span class="live-label">OFFLINE</span>
      </div>

      <button
        class="icon-btn"
        type="button"
        :aria-label="theme.theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'"
        @click="theme.toggle()"
      >
        <svg
          v-if="theme.theme === 'dark'"
          viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
          stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
        >
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
        <svg
          v-else
          viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
          stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
        >
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
        </svg>
      </button>

      <div class="user-menu">
        <button class="user-btn" type="button" @click="menuOpen = !menuOpen">
          <span class="avatar">{{ auth.user?.username.slice(0, 1).toUpperCase() ?? '?' }}</span>
          <span class="username">{{ auth.user?.username ?? '' }}</span>
        </button>
        <Transition name="pop">
          <div v-if="menuOpen" class="menu card" @click.stop>
            <div class="menu-head">
              <div class="menu-user">{{ auth.user?.username }}</div>
              <span class="badge badge-accent">{{ auth.role }}</span>
            </div>
            <hr class="divider" />
            <button class="menu-item" type="button" @click="handleLogout">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                <path d="M16 17l5-5-5-5M21 12H9" />
              </svg>
              Logout
            </button>
          </div>
        </Transition>
        <div v-if="menuOpen" class="menu-backdrop" @click="menuOpen = false" />
      </div>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  height: var(--topbar-h);
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 0 20px;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
}

.page-title {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.02em;
  white-space: nowrap;
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.live {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, var(--up) 35%, transparent);
  background: var(--up-bg);
}

.live .live-label {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: var(--up);
}

.live .live-dot.off {
  background: var(--text-2);
  animation: none;
}

.live-off {
  border-color: var(--border);
  background: transparent;
}

.live-off .live-label {
  color: var(--text-2);
}

.icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-2);
  cursor: pointer;
  transition:
    color var(--dur-fast) ease,
    border-color var(--dur-fast) ease,
    background-color var(--dur-fast) ease;
}

.icon-btn:hover {
  color: var(--accent);
  border-color: var(--border);
  background: var(--hover-layer);
}

.icon-btn svg {
  width: 16px;
  height: 16px;
}

.user-menu {
  position: relative;
}

.user-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px 4px 4px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: transparent;
  color: var(--text);
  cursor: pointer;
  transition:
    border-color var(--dur-fast) ease,
    background-color var(--dur-fast) ease;
}

.user-btn:hover {
  border-color: var(--border);
  background: var(--hover-layer);
}

.avatar {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: var(--accent-dim);
  color: var(--accent);
  font-size: 12px;
  font-weight: 700;
}

.username {
  font-size: 13px;
  font-weight: 500;
}

.menu {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  min-width: 180px;
  z-index: 60;
  box-shadow: var(--shadow);
}

.menu-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
}

.menu-user {
  font-size: 13px;
  font-weight: 600;
}

.menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 9px 12px;
  border: 0;
  background: transparent;
  color: var(--text-2);
  font-size: 13px;
  cursor: pointer;
  transition: color var(--dur-fast) ease, background-color var(--dur-fast) ease;
}

.menu-item:hover {
  color: var(--down);
  background: var(--down-bg);
}

.menu-item svg {
  width: 15px;
  height: 15px;
}

.menu-backdrop {
  position: fixed;
  inset: 0;
  z-index: 50;
}
</style>
