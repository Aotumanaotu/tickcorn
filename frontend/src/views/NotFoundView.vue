<script setup lang="ts">
import PageContainer from '@/components/layout/PageContainer.vue'
</script>

<template>
  <PageContainer title="页面不存在">
    <div class="nf card">
      <div class="code mono">404</div>
      <h2 class="title">页面不存在</h2>
      <p class="hint">找不到你访问的页面，请检查地址或返回总览。</p>
      <router-link class="btn btn-primary" :to="{ name: 'overview' }">
        返回总览
      </router-link>
    </div>
  </PageContainer>
</template>

<style scoped>
.nf {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 64px 24px;
}

.code {
  font-size: 56px;
  font-weight: 700;
  color: var(--text-2);
  opacity: 0.4;
}

.title {
  font-size: 18px;
  font-weight: 700;
}

.hint {
  font-size: 13px;
  color: var(--text-2);
  margin-bottom: 12px;
}
</style>
