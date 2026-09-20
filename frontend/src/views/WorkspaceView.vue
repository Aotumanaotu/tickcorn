<script setup lang="ts">
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ConnectionBadge from '@/components/common/ConnectionBadge.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import PageContainer from '@/components/layout/PageContainer.vue'
import InstrumentName from '@/components/market/InstrumentName.vue'
import PriceText from '@/components/market/PriceText.vue'
import { useInstrumentsStore } from '@/stores/instruments'
import { useRealtimeStore } from '@/stores/realtime'
import { wsMarket } from '@/ws/client'

const route = useRoute()
const router = useRouter()
const instruments = useInstrumentsStore()
const realtime = useRealtimeStore()

const instrumentId = computed(() =>
  String(route.params.instrumentId ?? '').toUpperCase(),
)

const quote = computed(() => realtime.quoteFor(instrumentId.value))
const quoteData = computed(() => quote.value?.data ?? null)
const decimals = computed(() => {
  const tick = instruments.instrumentById.get(instrumentId.value)?.tick_size
  if (tick && tick > 0) {
    const s = tick.toString()
    if (s.includes('.')) return s.split('.')[1].length
    return 0
  }
  return 1
})

const dataMode = computed(() => quote.value?.dataMode ?? '')
const feedState = computed(() => realtime.marketState)

function subscribe(id: string): void {
  if (id) wsMarket.subscribe(id)
}

function unsubscribe(id: string): void {
  if (id) wsMarket.unsubscribe(id)
}

onMounted(() => {
  void instruments.init()
  subscribe(instrumentId.value)
})

onUnmounted(() => {
  unsubscribe(instrumentId.value)
})

watch(instrumentId, (next, prev) => {
  if (prev) unsubscribe(prev)
  if (next) subscribe(next)
})

// --- watchlist side panel -------------------------------------------------------

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
  <PageContainer>
    <template #actions>
      <ConnectionBadge
        :label="`FEED ${feedState.toUpperCase()}`"
        :tone="feedState === 'open' ? 'accent' : feedState === 'idle' ? 'muted' : 'warn'"
      />
      <ConnectionBadge
        v-if="dataMode"
        :label="dataMode.toUpperCase()"
        :tone="dataMode === 'live' ? 'up' : 'warn'"
      />
    </template>

    <div class="market-header card">
      <div class="ident">
        <h2 class="symbol mono">{{ instrumentId }}</h2>
        <InstrumentName :instrument-id="instrumentId" />
      </div>
      <div class="quote-grid num">
        <div class="q">
          <span class="q-label">Last</span>
          <PriceText
            :value="quoteData?.last ?? null"
            :decimals="decimals"
            tone="auto"
            :previous="quoteData?.pre_close ?? null"
            size="xl"
          />
        </div>
        <div class="q">
          <span class="q-label">Bid 1</span>
          <PriceText :value="quoteData?.bid1 ?? null" :decimals="decimals" size="lg" />
          <span class="q-vol mono">{{ quoteData?.bid_volume1 ?? '—' }}</span>
        </div>
        <div class="q">
          <span class="q-label">Ask 1</span>
          <PriceText :value="quoteData?.ask1 ?? null" :decimals="decimals" size="lg" />
          <span class="q-vol mono">{{ quoteData?.ask_volume1 ?? '—' }}</span>
        </div>
        <div class="q">
          <span class="q-label">Spread</span>
          <PriceText :value="quoteData?.spread ?? null" :decimals="decimals" size="lg" />
        </div>
        <div class="q">
          <span class="q-label">Mid</span>
          <PriceText :value="quoteData?.mid ?? null" :decimals="decimals" size="lg" />
        </div>
      </div>
    </div>

    <div class="trio">
      <div class="card panel">
        <div class="card-title"><span>Watchlist</span></div>
        <div v-if="watchItems.length" class="watch-list">
          <button
            v-for="item in watchItems"
            :key="item.instrument_id"
            class="watch-row"
            :class="{ active: item.instrument_id === instrumentId }"
            type="button"
            @click="openInstrument(item.instrument_id)"
          >
            <span class="mono">{{ item.instrument_id }}</span>
            <PriceText :value="realtime.quoteFor(item.instrument_id)?.data.last ?? null" size="sm" />
          </button>
        </div>
        <EmptyState
          v-else
          title="Empty watchlist"
          hint="Add contracts from the Markets page."
        />
      </div>

      <div class="card panel main-panel">
        <div class="card-title"><span>Chart</span></div>
        <EmptyState
          icon="chart"
          title="Realtime charts arrive in F2"
          hint="Tick-level price & bid/ask charts powered by lightweight-charts will render here."
        />
      </div>

      <div class="card panel">
        <div class="card-title"><span>Intelligence Panel</span></div>
        <EmptyState
          icon="layers"
          title="Intelligence pending"
          hint="Bounce ratio, quote intensity and trade-sign analytics will stream here in F3."
        />
      </div>
    </div>

    <div class="card timeline">
      <div class="card-title"><span>Timeline</span></div>
      <EmptyState
        icon="clock"
        title="Event timeline arrives in F3"
        hint="Classified microstructure events (bid-ask bounce, jumps) will scroll here in realtime."
      />
    </div>
  </PageContainer>
</template>

<style scoped>
.market-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 16px 20px;
  flex-wrap: wrap;
}

.ident {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 160px;
}

.symbol {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 0.03em;
}

.quote-grid {
  display: flex;
  gap: 36px;
  flex-wrap: wrap;
}

.q {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}

.q-label {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text-2);
}

.q-vol {
  font-size: 11px;
  color: var(--text-2);
}

.trio {
  display: grid;
  grid-template-columns: 240px 1fr 300px;
  gap: 16px;
  min-height: 340px;
}

.panel {
  display: flex;
  flex-direction: column;
}

.main-panel {
  flex: 1;
}

.watch-list {
  overflow-y: auto;
  max-height: 320px;
  display: flex;
  flex-direction: column;
}

.watch-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 14px;
  border: 0;
  border-bottom: 1px solid var(--border);
  background: transparent;
  color: var(--text);
  font-size: 12.5px;
  cursor: pointer;
  text-align: left;
  transition: background-color var(--dur-fast) ease;
}

.watch-row:last-child {
  border-bottom: 0;
}

.watch-row:hover {
  background: var(--hover-layer);
}

.watch-row.active {
  background: var(--accent-dim);
  color: var(--accent);
}

@media (max-width: 1100px) {
  .trio {
    grid-template-columns: 1fr;
  }
}
</style>
