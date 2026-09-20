<script setup lang="ts">
import { stateLabel, modeLabel, percent } from '@/labels'
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ConnectionBadge from '@/components/common/ConnectionBadge.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import PageContainer from '@/components/layout/PageContainer.vue'
import InstrumentName from '@/components/market/InstrumentName.vue'
import PriceText from '@/components/market/PriceText.vue'
import { useInstrumentsStore } from '@/stores/instruments'
import { useRealtimeStore } from '@/stores/realtime'
import TickChart from '@/components/market/TickChart.vue'
import MicroEvents from '@/components/market/MicroEvents.vue'
import { useSystemStore } from '@/stores/system'
import { wsMarket } from '@/ws/client'

const route = useRoute()
const router = useRouter()
const instruments = useInstrumentsStore()
const realtime = useRealtimeStore()
const system = useSystemStore()
const metrics = computed(() => system.status?.realtime[instrumentId.value])

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
        :label="`行情连接：${stateLabel(feedState)}`"
        :tone="feedState === 'open' ? 'accent' : feedState === 'idle' ? 'muted' : 'warn'"
      />
      <ConnectionBadge
        v-if="dataMode"
        :label="modeLabel(dataMode)"
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
          <span class="q-label">最新价</span>
          <PriceText
            :value="quoteData?.last ?? null"
            :decimals="decimals"
            tone="auto"
            :previous="quoteData?.pre_close ?? null"
            size="xl"
          />
        </div>
        <div class="q">
          <span class="q-label">买一</span>
          <PriceText :value="quoteData?.bid1 ?? null" :decimals="decimals" size="lg" />
          <span class="q-vol mono">{{ quoteData?.bid_volume1 ?? '—' }}</span>
        </div>
        <div class="q">
          <span class="q-label">卖一</span>
          <PriceText :value="quoteData?.ask1 ?? null" :decimals="decimals" size="lg" />
          <span class="q-vol mono">{{ quoteData?.ask_volume1 ?? '—' }}</span>
        </div>
        <div class="q">
          <span class="q-label">买卖价差</span>
          <PriceText :value="quoteData?.spread ?? null" :decimals="decimals" size="lg" />
        </div>
        <div class="q">
          <span class="q-label">中间价</span>
          <PriceText :value="quoteData?.mid ?? null" :decimals="decimals" size="lg" />
        </div>
      </div>
    </div>

    <div class="trio">
      <div class="card panel">
        <div class="card-title"><span>自选合约</span></div>
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
          title="暂无自选合约"
          hint="前往行情市场添加自选合约。"
        />
      </div>

      <div class="card panel main-panel">
        <div class="card-title"><span>行情图表</span></div>
        <TickChart :instrument-id="instrumentId" />
      </div>

      <div class="card panel">
        <div class="card-title"><span>分析面板</span></div>
        <div class="card-body analysis-stats"><p>反弹占比：{{ percent(metrics?.bounce_ratio) }}</p><p>真实移动占比：{{ percent(metrics?.genuine_move_ratio) }}</p><p>1-tick 变化：{{ metrics?.one_tick_last_changes ?? '—' }}</p><p>快照 / 分钟：{{ metrics?.message_rate_per_min ?? '—' }}</p><p class="dim">最近 200 次转换的 1-tick 子集；完整统计请生成归档报告。</p></div>
      </div>
    </div>

    <div class="card timeline">
      <div class="card-title"><span>事件时间轴</span></div>
      <MicroEvents :instrument-id="instrumentId" />
    </div>
  </PageContainer>
</template>

<style scoped>
.analysis-stats p { margin-bottom: 12px; line-height: 1.6; }
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
