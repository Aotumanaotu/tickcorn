<script setup lang="ts">
import { computed } from 'vue'
import type { AnalysisData } from '@/api/types'
import EmptyState from '@/components/common/EmptyState.vue'
import { eventLabel, percent, regimeLabel, regimeTone } from '@/labels'
import { useRealtimeStore } from '@/stores/realtime'
import { useSystemStore } from '@/stores/system'

/**
 * Intelligence panel: live microstructure metrics + market regime from the
 * /ws/analysis push stream, falling back to the polled REST snapshot
 * (system.status.realtime) until the first envelope arrives.
 */

const props = defineProps<{ instrumentId: string }>()

const realtime = useRealtimeStore()
const system = useSystemStore()

const payload = computed<AnalysisData | null>(() => {
  const env = realtime.analysisFor(props.instrumentId)
  return env ? (env.data as unknown as AnalysisData) : null
})

const metrics = computed(
  () => payload.value?.metrics ?? system.status?.realtime[props.instrumentId] ?? null,
)

const regime = computed(() => payload.value?.regime ?? '')
const lastLabel = computed(() => payload.value?.label ?? '')
const isPush = computed(() => payload.value !== null)

const updatedAt = computed(() => {
  const ns = realtime.analysisFor(props.instrumentId)?.local_ts_ns
  return ns ? new Date(ns / 1e6).toLocaleTimeString('zh-CN', { hour12: false }) : ''
})

const bounce = computed(() => metrics.value?.bounce_ratio ?? null)
const genuine = computed(() => metrics.value?.genuine_move_ratio ?? null)

const bar = computed(() => {
  const b = bounce.value ?? 0
  const g = genuine.value ?? 0
  return {
    bounce: `${(b * 100).toFixed(1)}%`,
    genuine: `${(g * 100).toFixed(1)}%`,
    other: `${(Math.max(0, 1 - b - g) * 100).toFixed(1)}%`,
  }
})

const rows = computed(() => [
  { k: '反弹占比（1-tick 子集）', v: percent(bounce.value) },
  { k: '真实移动占比', v: percent(genuine.value) },
  { k: '窗口内 1-tick 变化', v: metrics.value?.one_tick_last_changes ?? '—' },
  {
    k: '快照速率',
    v: metrics.value?.message_rate_per_min != null
      ? `${metrics.value.message_rate_per_min} /分钟`
      : '—',
  },
  {
    k: '平均价差',
    v: metrics.value?.mean_spread_ticks != null
      ? `${metrics.value.mean_spread_ticks.toFixed(2)} tick`
      : '—',
  },
  { k: '累计快照', v: metrics.value?.msg_count ?? '—' },
])
</script>

<template>
  <div class="intel">
    <template v-if="metrics">
      <div class="regime-row">
        <span class="chip" :class="`tone-${regimeTone(regime)}`" data-testid="regime-chip">
          市况判定：{{ regimeLabel(regime) }}
        </span>
        <span class="src">{{ isPush ? 'WS 推送' : 'REST 快照' }}</span>
      </div>

      <div class="ratio">
        <div class="ratio-bar" role="img" aria-label="反弹与真实移动占比">
          <span class="seg bounce" :style="{ width: bar.bounce }"></span>
          <span class="seg genuine" :style="{ width: bar.genuine }"></span>
          <span class="seg other" :style="{ width: bar.other }"></span>
        </div>
        <div class="ratio-legend">
          <span><i class="dot bounce"></i>反弹 {{ percent(bounce) }}</span>
          <span><i class="dot genuine"></i>真实移动 {{ percent(genuine) }}</span>
        </div>
      </div>

      <dl class="rows">
        <template v-for="row in rows" :key="row.k">
          <dt>{{ row.k }}</dt>
          <dd class="num">{{ row.v }}</dd>
        </template>
      </dl>

      <p class="foot">
        最近事件：{{ eventLabel(lastLabel) }}
        <template v-if="updatedAt"> · 更新于 {{ updatedAt }}</template>
      </p>
      <p class="foot dim">
        统计窗口为最近 200 次转换中 |ΔLastPrice| = 1 tick 的子集；完整统计请生成归档报告。
      </p>
    </template>
    <EmptyState
      v-else
      title="等待分析数据"
      hint="收到分类事件后，此处显示反弹/真实移动比率与市况判定。"
      icon="flask"
    />
  </div>
</template>

<style scoped>
.intel {
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow-y: auto;
}

.regime-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.chip {
  font-size: 12px;
  font-weight: 600;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid var(--border);
}

.chip.tone-warn {
  color: var(--warn);
  background: var(--warn-bg);
  border-color: color-mix(in srgb, var(--warn) 40%, transparent);
}

.chip.tone-up {
  color: var(--up);
  background: var(--up-bg);
  border-color: color-mix(in srgb, var(--up) 40%, transparent);
}

.chip.tone-info {
  color: var(--accent);
  background: var(--accent-dim);
  border-color: color-mix(in srgb, var(--accent) 40%, transparent);
}

.chip.tone-muted {
  color: var(--text-2);
  background: var(--hover-layer);
}

.src {
  font-size: 11px;
  color: var(--text-2);
}

.ratio {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.ratio-bar {
  display: flex;
  height: 10px;
  border-radius: 5px;
  overflow: hidden;
  background: var(--hover-layer);
}

.seg.bounce {
  background: var(--warn);
}

.seg.genuine {
  background: var(--up);
}

.seg.other {
  background: transparent;
}

.ratio-legend {
  display: flex;
  gap: 14px;
  font-size: 11.5px;
  color: var(--text-2);
}

.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 5px;
}

.dot.bounce {
  background: var(--warn);
}

.dot.genuine {
  background: var(--up);
}

.rows {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 8px 12px;
  margin: 0;
}

.rows dt {
  font-size: 12px;
  color: var(--text-2);
}

.rows dd {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
}

.foot {
  font-size: 12px;
  line-height: 1.6;
  margin: 0;
}

.foot.dim {
  color: var(--text-2);
}
</style>
