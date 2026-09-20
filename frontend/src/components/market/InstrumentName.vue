<script setup lang="ts">
import { computed } from 'vue'

import { useInstrumentsStore } from '@/stores/instruments'

const props = withDefaults(
  defineProps<{
    instrumentId: string
    /** Show only the Chinese product name (no symbol). */
    nameOnly?: boolean
  }>(),
  { nameOnly: false },
)

const store = useInstrumentsStore()

const instrument = computed(() => store.instrumentById.get(props.instrumentId))
const product = computed(() =>
  instrument.value?.product_code
    ? store.productByCode.get(instrument.value.product_code)
    : undefined,
)

const displayName = computed(
  () => product.value?.name ?? instrument.value?.product_code ?? '',
)
</script>

<template>
  <span class="inst-name">
    <span v-if="!nameOnly" class="inst-id mono">{{ instrumentId }}</span>
    <span v-if="displayName && !nameOnly" class="sep">·</span>
    <span v-if="displayName" class="inst-product">{{ displayName }}</span>
  </span>
</template>

<style scoped>
.inst-name {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  white-space: nowrap;
}

.inst-id {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.sep {
  color: var(--text-2);
}

.inst-product {
  font-size: 12.5px;
  color: var(--text-2);
}
</style>
