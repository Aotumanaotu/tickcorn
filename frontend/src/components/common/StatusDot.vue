<script setup lang="ts">
type DotState = 'ok' | 'warn' | 'error' | 'idle'

withDefaults(defineProps<{ state?: DotState; label?: string }>(), {
  state: 'idle',
  label: '',
})

const colorClass: Record<DotState, string> = {
  ok: 'dot-ok',
  warn: 'dot-warn',
  error: 'dot-error',
  idle: 'dot-idle',
}
</script>

<template>
  <span class="status-dot" :class="colorClass[state]">
    <span class="dot" aria-hidden="true" />
    <span v-if="label" class="dot-label">{{ label }}</span>
  </span>
</template>

<style scoped>
.status-dot {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.dot-ok {
  color: var(--up);
}

.dot-ok .dot {
  background: var(--up);
}

.dot-warn {
  color: var(--warn);
}

.dot-warn .dot {
  background: var(--warn);
}

.dot-error {
  color: var(--down);
}

.dot-error .dot {
  background: var(--down);
}

.dot-idle {
  color: var(--text-2);
}

.dot-idle .dot {
  background: var(--text-2);
  opacity: 0.6;
}

.dot-label {
  font-size: 12px;
  font-weight: 500;
}
</style>
