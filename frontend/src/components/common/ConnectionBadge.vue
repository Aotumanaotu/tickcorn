<script setup lang="ts">
withDefaults(
  defineProps<{
    label: string
    tone?: 'accent' | 'warn' | 'up' | 'down' | 'muted'
  }>(),
  { tone: 'muted' },
)
</script>

<template>
  <span class="conn-badge" :class="`tone-${tone}`">{{ label }}</span>
</template>

<style scoped>
.conn-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 9px;
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.09em;
  border-radius: 4px;
  border: 1px solid transparent;
  white-space: nowrap;
}

.tone-muted {
  color: var(--text-2);
  border-color: var(--border);
}

.tone-accent {
  color: var(--accent);
  border-color: color-mix(in srgb, var(--accent) 40%, transparent);
  background: var(--accent-dim);
}

.tone-warn {
  color: var(--warn);
  border-color: color-mix(in srgb, var(--warn) 40%, transparent);
  background: var(--warn-bg);
}

.tone-up {
  color: var(--up);
  border-color: color-mix(in srgb, var(--up) 40%, transparent);
  background: var(--up-bg);
}

.tone-down {
  color: var(--down);
  border-color: color-mix(in srgb, var(--down) 40%, transparent);
  background: var(--down-bg);
}
</style>
