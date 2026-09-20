<script setup lang="ts">
import { computed } from 'vue'
import { eventLabel } from '@/labels'
import { useRealtimeStore } from '@/stores/realtime'
import EmptyState from '@/components/common/EmptyState.vue'
const props = defineProps<{ instrumentId: string }>()
const realtime = useRealtimeStore()
const events = computed(() => realtime.microRing.filter(e => e.instrument_id === props.instrumentId).slice(-60).reverse())
function time(ns: number | null) { return ns ? new Date(ns / 1e6).toLocaleTimeString('zh-CN', { hour12: false }) : '—' }
</script>
<template>
  <div v-if="events.length" class="table-wrap"><table class="table"><thead><tr><th>时间</th><th>事件分类</th><th>价格变化（tick）</th><th>序号</th></tr></thead>
    <tbody><tr v-for="(e, i) in events" :key="`${e.seq}-${i}`"><td>{{ time(e.exchange_ts_ns || e.local_ts_ns) }}</td><td>{{ eventLabel(e.data.label) }}</td><td>{{ e.data.dl_ticks ?? '—' }}</td><td>{{ e.seq }}</td></tr></tbody>
  </table></div>
  <EmptyState v-else title="暂无实时分类事件" hint="收到连续有效报价后，会显示买卖价反弹、真实报价移动与不确定变化。" />
</template>
<style scoped>.table-wrap { overflow: auto; max-height: 350px; }</style>
