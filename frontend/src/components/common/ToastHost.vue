<script setup lang="ts">
import { appToast } from './appToast'
</script>

<template>
  <div class="toast-host" aria-live="polite">
    <TransitionGroup name="toast">
      <div
        v-for="toast in appToast.toasts"
        :key="toast.id"
        class="toast card"
        :class="`kind-${toast.kind}`"
        role="status"
        @click="appToast.dismiss(toast.id)"
      >
        <span class="toast-dot" aria-hidden="true" />
        <span class="toast-msg">{{ toast.message }}</span>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-host {
  position: fixed;
  right: 20px;
  bottom: 20px;
  z-index: 200;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 360px;
}

.toast {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 14px;
  font-size: 13px;
  cursor: pointer;
  box-shadow: var(--shadow);
}

.toast-dot {
  width: 8px;
  height: 8px;
  margin-top: 5px;
  border-radius: 50%;
  flex-shrink: 0;
}

.kind-success .toast-dot {
  background: var(--up);
}

.kind-error .toast-dot {
  background: var(--down);
}

.kind-info .toast-dot {
  background: var(--accent);
}

.toast-msg {
  word-break: break-word;
}
</style>
