<script setup lang="ts">
import { computed } from 'vue'
import { useRealtimeStore } from '@/stores/realtime'
import EmptyState from '@/components/common/EmptyState.vue'
const props = defineProps<{ instrumentId: string }>()
const realtime = useRealtimeStore()
const points = computed(() => realtime.quoteHistory.get(props.instrumentId) ?? [])
const series = [{ key: 'last', label: '最新价', color: 'var(--accent)' }, { key: 'bid1', label: '买一', color: 'var(--up)' }, { key: 'ask1', label: '卖一', color: 'var(--down)' }] as const
const range = computed(() => {
  const values = points.value.flatMap(p => [p.data.last, p.data.bid1, p.data.ask1]).filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
  const min = values.length ? Math.min(...values) : 0
  const max = values.length ? Math.max(...values) : 1
  const pad = Math.max((max - min) * 0.15, 0.5)
  return { min: min - pad, max: max + pad }
})
function path(key: 'last' | 'bid1' | 'ask1') {
  let started = false
  return points.value.map((p, i) => {
    const value = p.data[key]
    if (value == null || !Number.isFinite(value)) { started = false; return '' }
    const x = 70 + i / Math.max(points.value.length - 1, 1) * 700
    const y = 230 - (value - range.value.min) / (range.value.max - range.value.min) * 210
    const segment = started ? `H${x}V${y}` : `M${x},${y}`
    started = true
    return segment
  }).join(' ')
}
</script>
<template>
  <div class="tick-chart">
    <div class="legend"><span v-for="s in series" :key="s.key" :style="{ color: s.color }">━ {{ s.label }}</span></div>
    <svg v-if="points.length > 1" viewBox="0 0 800 265" role="img" :aria-label="`${instrumentId} 最新价与买卖报价阶梯图`">
      <g v-for="i in [0, 1, 2, 3, 4]" :key="i"><line x1="70" x2="770" :y1="20 + i * 52.5" :y2="20 + i * 52.5" class="grid-line" /><text x="62" :y="24 + i * 52.5" text-anchor="end">{{ (range.max - i / 4 * (range.max - range.min)).toFixed(2) }}</text></g>
      <path v-for="s in series" :key="s.key" :d="path(s.key)" fill="none" :stroke="s.color" :stroke-width="s.key === 'last' ? 2.5 : 1.3" />
      <text x="70" y="255">较早快照</text><text x="770" y="255" text-anchor="end">最新快照</text>
    </svg>
    <EmptyState v-else title="等待实时 tick" hint="请先连接 CTP 并订阅该合约；有两条以上新快照后显示阶梯图。" icon="chart" />
    <p>横轴为接收顺序，显示当前页面最近 {{ points.length }} 条快照（最多 300 条）。完整记录保存在原始归档中。</p>
  </div>
</template>
<style scoped>
.tick-chart { padding: 16px; }
.legend { display: flex; gap: 20px; font-size: 12px; }
svg { width: 100%; min-height: 180px; }
text { fill: var(--text-2); font-size: 11px; }
.grid-line { stroke: var(--border); stroke-dasharray: 3 4; }
p { color: var(--text-2); font-size: 12px; line-height: 1.6; }
</style>
