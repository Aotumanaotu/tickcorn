<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    value: number | null | undefined
    /** Tick-derived decimals, default 1 (Chinese futures convention). */
    decimals?: number
    /** Direction tint: up / down / flat. */
    tone?: 'up' | 'down' | 'flat' | 'auto'
    size?: 'sm' | 'md' | 'lg' | 'xl'
    /** Previous value to derive direction from when tone = "auto". */
    previous?: number | null
    suffix?: string
  }>(),
  { decimals: 1, tone: 'flat', size: 'md', previous: null, suffix: '' },
)

const text = computed(() => {
  if (props.value === null || props.value === undefined || Number.isNaN(props.value)) {
    return '—'
  }
  return props.value.toFixed(props.decimals)
})

const toneClass = computed(() => {
  if (props.tone !== 'auto') return `tone-${props.tone}`
  if (
    props.value === null ||
    props.value === undefined ||
    props.previous === null ||
    props.previous === undefined
  ) {
    return 'tone-flat'
  }
  if (props.value > props.previous) return 'tone-up'
  if (props.value < props.previous) return 'tone-down'
  return 'tone-flat'
})
</script>

<template>
  <span class="price mono" :class="[`size-${size}`, toneClass]">
    {{ text }}<span v-if="suffix && text !== '—'" class="suffix">{{ suffix }}</span>
  </span>
</template>

<style scoped>
.price {
  display: inline-block;
  font-variant-numeric: tabular-nums;
}

.tone-up {
  color: var(--up);
}

.tone-down {
  color: var(--down);
}

.tone-flat {
  color: var(--text);
}

.size-sm {
  font-size: 12px;
}

.size-md {
  font-size: 14px;
}

.size-lg {
  font-size: 18px;
  font-weight: 600;
}

.size-xl {
  font-size: 26px;
  font-weight: 700;
}

.suffix {
  font-size: 0.6em;
  color: var(--text-2);
  margin-left: 2px;
}
</style>
