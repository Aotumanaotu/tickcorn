<script setup lang="ts">
import { computed } from 'vue'

import ConnectionBadge from '@/components/common/ConnectionBadge.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import PageContainer from '@/components/layout/PageContainer.vue'
import { useRealtimeStore } from '@/stores/realtime'

const realtime = useRealtimeStore()

const recentMicro = computed(() =>
  realtime.microRing.slice(-8).reverse(),
)
</script>

<template>
  <PageContainer
    title="Microstructure"
    subtitle="Bid-ask bounce classification, quote intensity and event streams"
  >
    <template #actions>
      <ConnectionBadge
        :label="`EVENTS ${realtime.microCount}`"
        tone="accent"
      />
    </template>

    <div class="layout">
      <div class="card panel">
        <div class="card-title"><span>Event Monitor</span></div>
        <EmptyState
          icon="layers"
          title="Microstructure analytics arrive in F3"
          hint="Per-instrument bounce ratios, jump classifications and quote/trade intensity will be computed and streamed here."
        />
        <div v-if="recentMicro.length" class="ring">
          <div class="ring-title">Live event ring (latest {{ recentMicro.length }})</div>
          <div v-for="(env, i) in recentMicro" :key="`${env.instrument_id}-${env.seq ?? i}`" class="ring-row mono">
            <span>{{ env.instrument_id }}</span>
            <span class="dim">seq {{ env.seq ?? '—' }}</span>
          </div>
        </div>
      </div>

      <div class="side">
        <div class="card">
          <div class="card-title"><span>Bounce Ratio</span></div>
          <EmptyState
            title="Available in F3"
            hint="Rolling bounce ratio per contract with session comparison."
          />
        </div>
        <div class="card">
          <div class="card-title"><span>Watched Instruments</span></div>
          <EmptyState
            title="Available in F3"
            hint="Cross-sectional ranking of microstructure activity."
          />
        </div>
      </div>
    </div>
  </PageContainer>
</template>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: 1fr 320px;
  gap: 16px;
  min-height: 400px;
}

.panel {
  display: flex;
  flex-direction: column;
}

.side {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.ring {
  border-top: 1px solid var(--border);
  padding: 12px 16px 16px;
}

.ring-title {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-2);
  margin-bottom: 8px;
}

.ring-row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  padding: 4px 0;
  color: var(--text);
}

.dim {
  color: var(--text-2);
}

@media (max-width: 1000px) {
  .layout {
    grid-template-columns: 1fr;
  }
}
</style>
