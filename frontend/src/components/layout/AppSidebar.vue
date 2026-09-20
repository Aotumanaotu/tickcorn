<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import { FEATURES } from '@/router'

interface NavItem {
  label: string
  to: string
  icon: string
  feature?: keyof typeof FEATURES
}

const route = useRoute()

const navItems: NavItem[] = [
  { label: '总览', to: '/overview', icon: 'grid' },
  { label: '行情市场', to: '/markets', icon: 'list' },
  { label: '合约工作台', to: '/workspace/c2611', icon: 'chart' },
  { label: '微观结构', to: '/microstructure', icon: 'layers' },
  { label: '数据与报告', to: '/research', icon: 'flask' },
  { label: '策略', to: '/strategies', icon: 'target', feature: 'strategies' },
  { label: '交易', to: '/trading', icon: 'dollar', feature: 'trading' },
  { label: '模型', to: '/models', icon: 'cpu', feature: 'models' },
  { label: '飞书推送', to: '/monitor', icon: 'bell' },
  { label: '采集与设置', to: '/system', icon: 'settings' },
]

const visibleItems = computed(() =>
  navItems.filter((item) => !item.feature || FEATURES[item.feature]),
)

function isActive(to: string): boolean {
  if (to.startsWith('/workspace')) return route.path.startsWith('/workspace')
  return route.path.startsWith(to)
}
</script>

<template>
  <aside class="sidebar">
    <div class="brand">
      <svg class="brand-mark" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M3 17l4-8 4 5 3-9 4 7" />
        <path d="M2 21h20" stroke-opacity=".45" />
      </svg>
      <div class="brand-text">
        <span class="brand-name">MICROTERM</span>
        <span class="brand-sub">买卖价反弹研究</span>
      </div>
    </div>

    <nav class="nav">
      <router-link
        v-for="item in visibleItems"
        :key="item.to"
        :to="item.to"
        class="nav-item"
        :class="{ active: isActive(item.to) }"
      >
        <span class="nav-indicator" aria-hidden="true" />
        <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <template v-if="item.icon === 'grid'">
            <rect x="3" y="3" width="7" height="7" rx="1" />
            <rect x="14" y="3" width="7" height="7" rx="1" />
            <rect x="3" y="14" width="7" height="7" rx="1" />
            <rect x="14" y="14" width="7" height="7" rx="1" />
          </template>
          <template v-else-if="item.icon === 'list'">
            <path d="M8 6h13M8 12h13M8 18h13" />
            <path d="M3 6h.01M3 12h.01M3 18h.01" />
          </template>
          <template v-else-if="item.icon === 'chart'">
            <path d="M3 3v18h18" />
            <path d="M7 15l4-6 3 3 5-8" />
          </template>
          <template v-else-if="item.icon === 'layers'">
            <path d="M12 2l9 5-9 5-9-5 9-5z" />
            <path d="M3 12l9 5 9-5" stroke-opacity=".6" />
            <path d="M3 17l9 5 9-5" stroke-opacity=".35" />
          </template>
          <template v-else-if="item.icon === 'flask'">
            <path d="M9 3h6" />
            <path d="M10 3v6l-6 9a2 2 0 0 0 1.7 3h12.6a2 2 0 0 0 1.7-3l-6-9V3" />
            <path d="M7.5 15h9" stroke-opacity=".6" />
          </template>
          <template v-else-if="item.icon === 'target'">
            <circle cx="12" cy="12" r="9" />
            <circle cx="12" cy="12" r="5" />
            <circle cx="12" cy="12" r="1" />
          </template>
          <template v-else-if="item.icon === 'dollar'">
            <path d="M12 2v20" />
            <path d="M16.5 6.5c-1-1.2-2.8-1.8-4.5-1.5-2.2.4-3.6 2-3.4 3.8.2 1.7 1.7 2.5 4.4 3.2 2.4.6 4 1.5 4.2 3.5.2 2-1.5 3.7-3.9 4-2 .3-4.1-.4-5.3-1.7" />
          </template>
          <template v-else-if="item.icon === 'cpu'">
            <rect x="5" y="5" width="14" height="14" rx="2" />
            <rect x="10" y="10" width="4" height="4" />
            <path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3" />
          </template>
          <template v-else>
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1.1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3h.1a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5h.1a1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9v.1a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
          </template>
        </svg>
        <span class="nav-label">{{ item.label }}</span>
      </router-link>
    </nav>

    <div class="sidebar-footer">
      <span class="version">v0.3.0</span>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: var(--sidebar-w);
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: var(--panel);
  border-right: 1px solid var(--border);
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border);
}

.brand-mark {
  width: 26px;
  height: 26px;
  color: var(--accent);
  flex-shrink: 0;
}

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.25;
}

.brand-name {
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.12em;
  color: var(--text);
}

.brand-sub {
  font-size: 10px;
  color: var(--text-2);
  letter-spacing: 0.04em;
}

.nav {
  flex: 1;
  padding: 10px 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  overflow-y: auto;
}

.nav-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  font-weight: 500;
  color: var(--text-2);
  transition:
    color var(--dur-fast) ease,
    background-color var(--dur-fast) ease;
}

.nav-item:hover {
  color: var(--text);
  background: var(--hover-layer);
}

.nav-item.active {
  color: var(--accent);
  background: var(--accent-dim);
}

.nav-indicator {
  position: absolute;
  left: -8px;
  top: 20%;
  height: 60%;
  width: 3px;
  border-radius: 2px;
  background: transparent;
  transition: background-color var(--dur-fast) ease;
}

.nav-item.active .nav-indicator {
  background: var(--accent);
}

.nav-icon {
  width: 17px;
  height: 17px;
  flex-shrink: 0;
}

.sidebar-footer {
  padding: 12px 16px;
  border-top: 1px solid var(--border);
}

.version {
  font-size: 11px;
  color: var(--text-2);
  font-variant-numeric: tabular-nums;
}
</style>
