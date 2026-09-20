import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { marketApi } from '@/api/client'
import type { Instrument, Product, Watchlist } from '@/api/types'

/**
 * Reference data: products, instruments and the primary watchlist.
 */
export const useInstrumentsStore = defineStore('instruments', () => {
  const products = ref<Product[]>([])
  const instruments = ref<Instrument[]>([])
  const watchlists = ref<Watchlist[]>([])
  const loadedProducts = ref(false)
  const loadedInstruments = ref(false)
  const loading = ref(false)

  const productByCode = computed(() => {
    const map = new Map<string, Product>()
    for (const p of products.value) map.set(p.code, p)
    return map
  })

  const instrumentById = computed(() => {
    const map = new Map<string, Instrument>()
    for (const i of instruments.value) map.set(i.instrument_id, i)
    return map
  })

  const exchanges = computed(() => {
    const set = new Set<string>()
    for (const p of products.value) set.add(p.exchange)
    for (const i of instruments.value) set.add(i.exchange)
    return [...set].sort()
  })

  const primaryWatchlist = computed<Watchlist | null>(
    () => watchlists.value[0] ?? null,
  )

  const watchedIds = computed<Set<string>>(() => {
    const set = new Set<string>()
    for (const wl of watchlists.value) {
      for (const item of wl.items) set.add(item.instrument_id)
    }
    return set
  })

  async function loadProducts(force = false): Promise<void> {
    if (loadedProducts.value && !force) return
    const rows = await marketApi.products()
    products.value = rows.sort((a, b) => a.sort_order - b.sort_order)
    loadedProducts.value = true
  }

  async function loadInstruments(force = false): Promise<void> {
    if (loadedInstruments.value && !force) return
    loading.value = true
    try {
      instruments.value = await marketApi.instruments({ limit: 1000 })
      loadedInstruments.value = true
    } finally {
      loading.value = false
    }
  }

  async function loadWatchlists(): Promise<void> {
    const rows = await marketApi.watchlists()
    watchlists.value = rows
  }

  /** Ensure at least one watchlist exists; returns the primary one. */
  async function ensureWatchlist(): Promise<Watchlist> {
    if (!watchlists.value.length) {
      const created = await marketApi.createWatchlist('Default')
      watchlists.value = [created]
    }
    return watchlists.value[0]
  }

  function isWatched(instrumentId: string): boolean {
    return watchedIds.value.has(instrumentId)
  }

  async function toggleWatch(instrumentId: string): Promise<void> {
    const wl = await ensureWatchlist()
    const exists = wl.items.some((i) => i.instrument_id === instrumentId)
    const updated = exists
      ? await marketApi.removeWatchlistItem(wl.id, instrumentId)
      : await marketApi.addWatchlistItem(wl.id, instrumentId)
    watchlists.value = watchlists.value.map((w) => (w.id === updated.id ? updated : w))
  }

  async function init(): Promise<void> {
    await Promise.allSettled([loadProducts(), loadInstruments(), loadWatchlists()])
  }

  return {
    products,
    instruments,
    watchlists,
    loading,
    productByCode,
    instrumentById,
    exchanges,
    primaryWatchlist,
    watchedIds,
    isWatched,
    loadProducts,
    loadInstruments,
    loadWatchlists,
    toggleWatch,
    init,
  }
})
