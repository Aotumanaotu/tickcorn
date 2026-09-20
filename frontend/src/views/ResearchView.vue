<script setup lang="ts">
import EmptyState from '@/components/common/EmptyState.vue'
import PageContainer from '@/components/layout/PageContainer.vue'
</script>

<template>
  <PageContainer
    title="Research"
    subtitle="Sessions, replays and generated research reports"
  >
    <div class="layout">
      <div class="card">
        <div class="card-title"><span>Recent Sessions</span></div>
        <EmptyState
          icon="clock"
          title="Available in F4"
          hint="Recorded microstructure sessions with replay controls and summary statistics."
        />
      </div>
      <div class="card">
        <div class="card-title"><span>Reports</span></div>
        <EmptyState
          icon="flask"
          title="Available in F4"
          hint="Bounce-ratio reports, comparative studies and exportable notebooks."
        />
      </div>
      <div class="card">
        <div class="card-title"><span>Replay Lab</span></div>
        <EmptyState
          title="Available in F4"
          hint="Deterministic event replay for hypothesis testing."
        />
      </div>
    </div>
  </PageContainer>
</template>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  min-height: 320px;
}

@media (max-width: 1000px) {
  .layout {
    grid-template-columns: 1fr;
  }
}
</style>
