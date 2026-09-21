<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import PageContainer from '@/components/layout/PageContainer.vue'
import MetricCard from '@/components/common/MetricCard.vue'
import TickChart from '@/components/market/TickChart.vue'
import MicroEvents from '@/components/market/MicroEvents.vue'
import { useSystemStore } from '@/stores/system'
import { useInstrumentsStore } from '@/stores/instruments'
import { wsMarket } from '@/ws/client'
import { percent } from '@/labels'
const system = useSystemStore()
const instruments = useInstrumentsStore()
const selected = ref('')
const options = computed(() => [...new Set([...Object.keys(system.gateway?.instruments ?? {}), ...Object.keys(system.status?.realtime ?? {}), ...instruments.instruments.map(i => i.instrument_id)])])
watch(options, ids => { if (!selected.value && ids.length) selected.value = ids[0] }, { immediate: true })
watch(selected, (next, prev) => { if (prev) wsMarket.unsubscribe(prev); if (next) wsMarket.subscribe(next) }, { immediate: true })
const metrics = computed(() => system.status?.realtime[selected.value])
const tickSize = computed(() => instruments.instrumentById.get(selected.value)?.tick_size ?? null)
const decimals = computed(() => {
  const tick = tickSize.value
  if (tick && tick > 0) {
    const s = tick.toString()
    return s.includes('.') ? s.split('.')[1].length : 0
  }
  return 1
})
onMounted(() => { void instruments.init(); void system.refreshStatus(true) })
onUnmounted(() => { if (selected.value) wsMarket.unsubscribe(selected.value) })
</script>
<template>
  <PageContainer title="Tick 跳变分析" subtitle="观察价格方波与阶梯跳变，区分买卖价反弹和真实报价移动">
    <template #actions><select v-model="selected" class="select" aria-label="分析合约"><option value="" disabled>请选择合约</option><option v-for="id in options" :key="id" :value="id">{{ id }}</option></select></template>
    <p class="scope">实时统计以 API 本次运行为范围；比例的分子与分母均限定为最近 200 次转换中，最新价变化恰好为 1 tick 的子集。历史全天分析请使用“数据与报告”。</p>
    <div class="metrics"><MetricCard label="反弹占比" :value="percent(metrics?.bounce_ratio)" /><MetricCard label="真实移动占比" :value="percent(metrics?.genuine_move_ratio)" /><MetricCard label="窗口内 1-tick 变化" :value="metrics?.one_tick_last_changes ?? '—'" /><MetricCard label="快照 / 分钟" :value="metrics?.message_rate_per_min ?? '—'" /></div>
    <div class="card"><div class="card-title">价格与买卖报价 · {{ selected || '待选择' }}</div><TickChart :instrument-id="selected" :decimals="decimals" :tick-size="tickSize" /></div>
    <div class="card"><div class="card-title">实时事件分类 · 最近 60 条</div><MicroEvents :instrument-id="selected" /></div>
  </PageContainer>
</template>
<style scoped>
.metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }
.scope { color: var(--text-2); font-size: 13px; line-height: 1.7; }
@media(max-width: 1000px) { .metrics { grid-template-columns: repeat(2, 1fr); } }
</style>
