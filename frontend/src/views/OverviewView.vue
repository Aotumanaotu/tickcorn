<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'

import EmptyState from '@/components/common/EmptyState.vue'
import MetricCard from '@/components/common/MetricCard.vue'
import PageContainer from '@/components/layout/PageContainer.vue'
import InstrumentName from '@/components/market/InstrumentName.vue'
import PriceText from '@/components/market/PriceText.vue'
import { useInstrumentsStore } from '@/stores/instruments'
import { useRealtimeStore } from '@/stores/realtime'
import { useSystemStore } from '@/stores/system'

const router = useRouter()
const system = useSystemStore()
const instruments = useInstrumentsStore()
const realtime = useRealtimeStore()

const REFRESH_MS = 3_000
let timer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  void system.refreshStatus(true)
  void instruments.loadWatchlists()
  timer = setInterval(() => void system.refreshStatus(true), REFRESH_MS)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

// --- status cards -----------------------------------------------------------

const gatewayState = computed(() => system.gatewayState)
const gatewayStateDot = computed<'ok' | 'warn' | 'error' | 'idle'>(() => {
  if (!system.gatewayConnected) return system.gatewayState === 'offline' ? 'idle' : 'warn'
  return system.isLive ? 'ok' : 'warn'
})

const gatewaySub = computed(() => {
  const count = system.gateway?.instruments?.length ?? 0
  const events = system.gateway?.events_published
  const parts: string[] = []
  if (count) parts.push(`${count} subscribed`)
  if (events !== undefined) parts.push(`${events.toLocaleString()} events`)
  return parts.join(' · ') || 'No subscriptions'
})

const ingestSub = computed(() => {
  const events = system.status?.ingest.events_received
  if (events === undefined) return '—'
  return `${events.toLocaleString()} events received`
})

const lastEventAgo = computed(() => {
  const iso = system.status?.ingest.last_event_at
  if (!iso) return 'no events yet'
  const ms = Date.now() - new Date(iso).getTime()
  if (Number.isNaN(ms) || ms < 0) return 'just now'
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  return `${Math.floor(m / 60)}h ago`
})

const wsDot = computed<'ok' | 'warn' | 'idle'>(() =>
  realtime.marketState === 'open' ? 'ok' : realtime.marketState === 'idle' ? 'idle' : 'warn',
)

const wsSub = computed(
  () =>
    `market: ${realtime.marketState} · analysis: ${realtime.analysisState} · ${realtime.quotes.size} quotes`,
)

// --- watchlist -----------------------------------------------------------------

const watchItems = computed(() =>
  (instruments.primaryWatchlist?.items ?? [])
    .slice()
    .sort((a, b) => a.sort_order - b.sort_order),
)

function openInstrument(id: string): void {
  router.push({ name: 'workspace', params: { instrumentId: id } })
}
</script>

<template>
  <PageContainer
    title="Overview"
    :subtitle="`System health, watchlist and research activity · backend v${system.version || '—'}`"
  >
    <div class="metrics">
      <MetricCard
        label="Gateway"
        :value="gatewayState"
        :sub="gatewaySub"
        :state="gatewayStateDot"
      />
      <MetricCard
        label="Database"
        :value="system.dbOk ? 'OK' : 'Error'"
        :state="system.dbOk ? 'ok' : 'error'"
        sub="PostgreSQL · ticks & events"
      />
      <MetricCard
        label="WebSocket"
        :value="realtime.marketState === 'open' ? 'Connected' : 'Disconnected'"
        :sub="wsSub"
        :state="wsDot"
      />
      <MetricCard
        label="Ingest"
        :value="(system.status?.ingest.events_received ?? 0).toLocaleString()"
        :sub="`${ingestSub} · ${lastEventAgo}`"
        :state="system.status?.ingest.last_event_at ? 'ok' : 'idle'"
      />
    </div>

    <div class="grid">
      <div class="card">
        <div class="card-title">
          <span>Watchlist</span>
          <router-link class="manage" :to="{ name: 'markets' }">Manage →</router-link>
        </div>
        <div v-if="watchItems.length" class="watch-list">
          <button
            v-for="item in watchItems"
            :key="item.instrument_id"
            class="watch-row"
            type="button"
            @click="openInstrument(item.instrument_id)"
          >
            <InstrumentName :instrument-id="item.instrument_id" />
            <PriceText
              :value="realtime.quoteFor(item.instrument_id)?.data.last ?? null"
              tone="auto"
              :previous="
                realtime.quoteFor(item.instrument_id)?.data.pre_close ?? null
              "
              size="sm"
            />
          </button>
        </div>
        <EmptyState
          v-else
          title="No instruments watched yet"
          hint="Browse the markets catalog and add contracts to your watchlist."
        >
          <router-link class="btn btn-primary" :to="{ name: 'markets' }">
            Browse Markets
          </router-link>
        </EmptyState>
      </div>

      <div class="grid-col">
        <div class="card">
          <div class="card-title"><span>Recent Sessions</span></div>
          <EmptyState
            icon="clock"
            title="Available in F4"
            hint="Recorded microstructure sessions will be listed here."
          />
        </div>
        <div class="card">
          <div class="card-title"><span>Latest Reports</span></div>
          <EmptyState
            icon="flask"
            title="Available in F4"
            hint="Session reports and bounce-ratio analyses will appear here."
          />
        </div>
      </div>
    </div>
  </PageContainer>
</template>

<style scoped>
.metrics {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}

.grid {
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 16px;
}

.grid-col {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.manage {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: none;
}

.watch-list {
  display: flex;
  flex-direction: column;
  max-height: 320px;
  overflow-y: auto;
}

.watch-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 16px;
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  transition: background-color var(--dur-fast) ease;
  text-align: left;
}

.watch-list .watch-row:last-child {
  border-bottom: 0;
}

.watch-row:hover {
  background: var(--hover-layer);
}

@media (max-width: 1100px) {
  .metrics {
    grid-template-columns: repeat(2, 1fr);
  }

  .grid {
    grid-template-columns: 1fr;
  }
}
</style>
