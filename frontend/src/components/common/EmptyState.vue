<script setup lang="ts">
withDefaults(
  defineProps<{
    title: string
    hint?: string
    icon?: 'box' | 'chart' | 'layers' | 'flask' | 'lock' | 'clock'
  }>(),
  { hint: '', icon: 'box' },
)
</script>

<template>
  <div class="empty">
    <svg
      class="empty-icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="1.4"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      <template v-if="icon === 'chart'">
        <path d="M3 3v18h18" />
        <path d="M7 15l4-6 3 3 5-8" stroke-dasharray="2 2.5" />
      </template>
      <template v-else-if="icon === 'layers'">
        <path d="M12 2l9 5-9 5-9-5 9-5z" />
        <path d="M3 12l9 5 9-5" stroke-opacity=".6" />
        <path d="M3 17l9 5 9-5" stroke-opacity=".35" />
      </template>
      <template v-else-if="icon === 'flask'">
        <path d="M9 3h6" />
        <path d="M10 3v6l-6 9a2 2 0 0 0 1.7 3h12.6a2 2 0 0 0 1.7-3l-6-9V3" />
        <path d="M7.5 15h9" stroke-opacity=".6" />
      </template>
      <template v-else-if="icon === 'lock'">
        <rect x="4" y="11" width="16" height="10" rx="2" />
        <path d="M8 11V7a4 4 0 0 1 8 0v4" />
      </template>
      <template v-else-if="icon === 'clock'">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 3" />
      </template>
      <template v-else>
        <path d="M21 8l-9-5-9 5v8l9 5 9-5V8z" />
        <path d="M3.3 8.2L12 13l8.7-4.8" stroke-opacity=".6" />
        <path d="M12 13v8" stroke-opacity=".6" />
      </template>
    </svg>
    <h3 class="empty-title">{{ title }}</h3>
    <p v-if="hint" class="empty-hint">{{ hint }}</p>
    <div v-if="$slots.default" class="empty-action">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 40px 24px;
  text-align: center;
}

.empty-icon {
  width: 40px;
  height: 40px;
  color: var(--text-2);
  opacity: 0.55;
}

.empty-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}

.empty-hint {
  font-size: 12.5px;
  color: var(--text-2);
  max-width: 380px;
}

.empty-action {
  margin-top: 8px;
}
</style>
