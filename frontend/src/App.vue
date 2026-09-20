<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'

import AppSidebar from '@/components/layout/AppSidebar.vue'
import TopBar from '@/components/layout/TopBar.vue'
import ToastHost from '@/components/common/ToastHost.vue'
import { useAuthStore } from '@/stores/auth'
import { useInstrumentsStore } from '@/stores/instruments'
import { useRealtimeStore } from '@/stores/realtime'
import { useSystemStore } from '@/stores/system'
import { useThemeStore } from '@/stores/theme'

const route = useRoute()
const auth = useAuthStore()
const theme = useThemeStore()
const system = useSystemStore()
const realtime = useRealtimeStore()
const instruments = useInstrumentsStore()

theme.init()
void auth.boot()

const isPublic = computed(() => route.meta.public === true)

watch(
  () => auth.isAuthenticated,
  (authenticated) => {
    if (authenticated) {
      system.start()
      realtime.start()
      void instruments.init()
    } else {
      system.stop()
      realtime.stop()
    }
  },
  { immediate: true },
)
</script>

<template>
  <router-view v-if="isPublic" />
  <div v-else class="shell">
    <AppSidebar />
    <div class="shell-main">
      <TopBar />
      <main class="shell-content">
        <router-view v-slot="{ Component }">
          <Transition name="fade" mode="out-in">
            <component :is="Component" :key="route.path" />
          </Transition>
        </router-view>
      </main>
    </div>
  </div>
  <ToastHost />
</template>

<style scoped>
.shell {
  display: flex;
  height: 100%;
  overflow: hidden;
}

.shell-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.shell-content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 20px 24px;
}
</style>
