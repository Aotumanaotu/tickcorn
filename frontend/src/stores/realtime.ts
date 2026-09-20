import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import type { Envelope, QuoteData } from '@/api/types'
import { wsAnalysis, wsMarket } from '@/ws/client'

const MICRO_RING_MAX = 200

export interface RealtimeQuote {
  instrumentId: string
  data: QuoteData
  receivedAt: number
  exchangeTsNs: number | null
  dataMode: string
}

/**
 * Realtime fan-out store driven by the market + analysis sockets:
 * latest quote per instrument, a bounded microstructure event ring,
 * and latest analysis payload per instrument.
 */
export const useRealtimeStore = defineStore('realtime', () => {
  const quotes = ref<Map<string, RealtimeQuote>>(new Map())
  const microRing = ref<Envelope[]>([])
  const analysis = ref<Map<string, Envelope>>(new Map())
  const marketState = ref<string>('idle')
  const analysisState = ref<string>('idle')

  const microCount = computed(() => microRing.value.length)

  function handleMarket(env: Envelope): void {
    if (!env.instrument_id) return
    if (env.type === 'quote') {
      quotes.value.set(env.instrument_id, {
        instrumentId: env.instrument_id,
        data: env.data as QuoteData,
        receivedAt: Date.now(),
        exchangeTsNs: env.exchange_ts_ns,
        dataMode: env.data_mode,
      })
    } else if (env.type === 'micro') {
      microRing.value.push(env)
      if (microRing.value.length > MICRO_RING_MAX) {
        microRing.value.splice(0, microRing.value.length - MICRO_RING_MAX)
      }
    }
  }

  function handleAnalysis(env: Envelope): void {
    if (!env.instrument_id) return
    analysis.value.set(env.instrument_id, env)
  }

  function quoteFor(instrumentId: string): RealtimeQuote | null {
    return quotes.value.get(instrumentId.toUpperCase()) ?? null
  }

  function start(): void {
    stop()
    wsMarket.connect()
    wsAnalysis.connect()
    detach.push(
      wsMarket.on(handleMarket),
      wsMarket.onState((s) => {
        marketState.value = s
      }),
      wsAnalysis.on(handleAnalysis),
      wsAnalysis.onState((s) => {
        analysisState.value = s
      }),
    )
  }

  function stop(): void {
    for (const off of detach) off()
    detach = []
    wsMarket.close()
    wsAnalysis.close()
  }

  let detach: (() => void)[] = []

  return {
    quotes,
    microRing,
    analysis,
    marketState,
    analysisState,
    microCount,
    quoteFor,
    start,
    stop,
  }
})
