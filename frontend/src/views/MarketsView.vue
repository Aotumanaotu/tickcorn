<script setup lang="ts">
import { categoryLabel, exchangeLabel } from '@/labels'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { appToast } from '@/components/common/appToast'
import EmptyState from '@/components/common/EmptyState.vue'
import PageContainer from '@/components/layout/PageContainer.vue'
import InstrumentName from '@/components/market/InstrumentName.vue'
import { useInstrumentsStore } from '@/stores/instruments'

const router = useRouter()
const store = useInstrumentsStore()

const search = ref('')
const exchangeFilter = ref('')
const productFilter = ref('')

onMounted(() => {
  void store.init()
})

// --- product rail -----------------------------------------------------------

const CATEGORY_ORDER = ['Agriculture', 'Metal', 'Energy', 'Chemical', 'Financial', 'Other']

interface ProductGroup {
  category: string
  products: { code: string; name: string; exchange: string }[]
}

const productGroups = computed<ProductGroup[]>(() => {
  const groups = new Map<string, { code: string; name: string; exchange: string }[]>()
  for (const p of store.products) {
    const list = groups.get(p.category) ?? []
    list.push({ code: p.code, name: p.name, exchange: p.exchange })
    groups.set(p.category, list)
  }
  const order = [...CATEGORY_ORDER.filter((c) => groups.has(c))]
  for (const cat of [...groups.keys()].sort()) {
    if (!order.includes(cat)) order.push(cat)
  }
  return order.map((category) => ({
    category,
    products: groups.get(category) ?? [],
  }))
})

function selectProduct(code: string): void {
  productFilter.value = productFilter.value === code ? '' : code
}

// --- instruments table --------------------------------------------------------

interface Row {
  instrument_id: string
  product_code: string | null
  exchange: string
  tick_size: number | null
  is_main: boolean
  watched: boolean
}

const rows = computed<Row[]>(() => {
  const term = search.value.trim().toLowerCase()
  return store.instruments
    .filter((i) => {
      if (exchangeFilter.value && i.exchange !== exchangeFilter.value) return false
      if (productFilter.value && i.product_code !== productFilter.value) return false
      if (term) {
        const product = i.product_code ? store.productByCode.get(i.product_code) : undefined
        const haystack = `${i.instrument_id} ${i.product_code ?? ''} ${product?.name ?? ''}`.toLowerCase()
        if (!haystack.includes(term)) return false
      }
      return true
    })
    .map((i) => ({
      instrument_id: i.instrument_id,
      product_code: i.product_code,
      exchange: i.exchange,
      tick_size: i.tick_size,
      is_main: i.is_main,
      watched: store.isWatched(i.instrument_id),
    }))
    .sort((a, b) => {
      if (a.is_main !== b.is_main) return a.is_main ? -1 : 1
      return a.instrument_id.localeCompare(b.instrument_id)
    })
})

function exchangeOptions(): string[] {
  return store.exchanges
}

function tickLabel(t: number | null): string {
  if (t === null || t === undefined) return '—'
  return t.toString()
}

function openWorkspace(id: string): void {
  router.push({ name: 'workspace', params: { instrumentId: id } })
}

async function toggleWatch(row: Row, event: Event): Promise<void> {
  event.stopPropagation()
  try {
    await store.toggleWatch(row.instrument_id)
  } catch (err) {
    appToast.error(
      err instanceof Error ? err.message : '更新自选合约失败',
    )
  }
}
</script>

<template>
  <PageContainer
    title="行情市场"
    subtitle="浏览各交易所的品种与合约"
  >
    <div class="layout">
      <aside class="rail card">
        <div class="card-title"><span>品种</span></div>
        <div class="rail-body">
          <button
            class="product-all"
            :class="{ active: !productFilter }"
            type="button"
            @click="productFilter = ''"
          >
            全部品种
          </button>
          <div v-for="group in productGroups" :key="group.category" class="group">
            <div class="group-label">{{ categoryLabel(group.category) }}</div>
            <button
              v-for="p in group.products"
              :key="p.code"
              class="product"
              :class="{ active: productFilter === p.code }"
              type="button"
              :title="`${p.name} · ${exchangeLabel(p.exchange)}`"
              @click="selectProduct(p.code)"
            >
              <span class="product-name">{{ p.name }}</span>
              <span class="product-code mono">{{ p.code }}</span>
            </button>
          </div>
          <EmptyState
            v-if="!productGroups.length"
            title="暂无品种"
            hint="连接服务后将自动加载品种资料。"
          />
        </div>
      </aside>

      <div class="catalog card">
        <div class="toolbar">
          <input
            v-model="search"
            class="input search"
            type="search"
            placeholder="搜索合约或品种…"
          />
          <select v-model="exchangeFilter" class="select exchange">
            <option value="">全部交易所</option>
            <option v-for="ex in exchangeOptions()" :key="ex" :value="ex">
              {{ exchangeLabel(ex) }}
            </option>
          </select>
          <span class="count num">{{ rows.length }} 个合约</span>
        </div>

        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>合约</th>
                <th>名称</th>
                <th>交易所</th>
                <th class="num-col">最小变动价位</th>
                <th class="num-col">主力</th>
                <th class="actions-col"></th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in rows"
                :key="row.instrument_id"
                class="clickable"
                @click="openWorkspace(row.instrument_id)"
              >
                <td><InstrumentName :instrument-id="row.instrument_id" /></td>
                <td class="product-name">
                  {{
                    row.product_code
                      ? (store.productByCode.get(row.product_code)?.name ?? '—')
                      : '—'
                  }}
                </td>
                <td class="mono dim">{{ exchangeLabel(row.exchange) }}</td>
                <td class="num-col mono dim">{{ tickLabel(row.tick_size) }}</td>
                <td class="num-col">
                  <span v-if="row.is_main" class="main-star" title="主力合约">★</span>
                  <span v-else class="dim">—</span>
                </td>
                <td class="actions-col">
                  <button
                    class="btn btn-sm"
                    :class="row.watched ? 'btn-ghost watched' : ''"
                    type="button"
                    @click="toggleWatch(row, $event)"
                  >
                    {{ row.watched ? '★ 已自选' : '☆ 加入自选' }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
          <EmptyState
            v-if="!rows.length && !store.loading"
            title="没有匹配的合约"
            hint="请调整搜索内容、交易所或品种筛选条件。"
          />
        </div>
      </div>
    </div>
  </PageContainer>
</template>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: 220px 1fr;
  gap: 16px;
  min-height: 0;
}

.rail-body {
  padding: 8px;
  max-height: calc(100vh - 220px);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.product-all,
.product {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 7px 10px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-2);
  font-size: 12.5px;
  cursor: pointer;
  text-align: left;
  transition:
    color var(--dur-fast) ease,
    background-color var(--dur-fast) ease;
}

.product-all:hover,
.product:hover {
  color: var(--text);
  background: var(--hover-layer);
}

.product-all.active,
.product.active {
  color: var(--accent);
  background: var(--accent-dim);
}

.group {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.group-label {
  padding: 4px 10px 2px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text-2);
  opacity: 0.7;
}

.product-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.product-code {
  font-size: 11px;
  opacity: 0.7;
  flex-shrink: 0;
}

.catalog {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}

.search {
  max-width: 280px;
}

.exchange {
  width: 160px;
}

.count {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-2);
}

.table-wrap {
  overflow: auto;
  max-height: calc(100vh - 220px);
}

.num-col {
  text-align: right;
}

.actions-col {
  text-align: right;
  width: 110px;
}

.dim {
  color: var(--text-2);
}

.main-star {
  color: var(--warn);
}

.btn.watched {
  color: var(--accent);
}

.product-name-cell {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 900px) {
  .layout {
    grid-template-columns: 1fr;
  }
}
</style>
