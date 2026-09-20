<script setup lang="ts">
import StatusDot from './StatusDot.vue'

withDefaults(
  defineProps<{
    label: string
    value: string | number
    sub?: string
    state?: 'ok' | 'warn' | 'error' | 'idle'
  }>(),
  { sub: '', state: 'idle' },
)
</script>

<template>
  <div class="card card-hover metric">
    <div class="metric-head">
      <span class="metric-label">{{ label }}</span>
      <StatusDot :state="state" />
    </div>
    <div class="metric-value num">{{ value }}</div>
    <div v-if="sub" class="metric-sub num">{{ sub }}</div>
  </div>
</template>

<style scoped>
.metric {
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.metric-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.metric-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-2);
}

.metric-value {
  font-size: 22px;
  font-weight: 700;
  line-height: 1.2;
}

.metric-sub {
  font-size: 12px;
  color: var(--text-2);
}
</style>
