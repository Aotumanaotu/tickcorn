<script setup lang="ts">
defineProps<{
  title?: string
  subtitle?: string
}>()
</script>

<template>
  <section class="page">
    <header v-if="title || $slots.actions" class="page-head">
      <div>
        <h2 class="page-title">{{ title }}</h2>
        <p v-if="subtitle" class="page-sub">{{ subtitle }}</p>
      </div>
      <div class="page-actions">
        <slot name="actions" />
      </div>
    </header>
    <slot />
  </section>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 1440px;
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.page-title {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 0.01em;
}

.page-sub {
  margin-top: 2px;
  font-size: 13px;
  color: var(--text-2);
}

.page-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
