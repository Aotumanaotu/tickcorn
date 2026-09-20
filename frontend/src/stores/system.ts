import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { systemApi } from '@/api/client'
import type { SystemStatus } from '@/api/types'
import { wsSystem } from '@/ws/client'

const POLL_INTERVAL_MS = 10_000

/**
 * System health store: REST snapshot + wsSystem push refresh,
 * with a 10s poll as safety net.
 */
export const useSystemStore = defineStore('system', () => {
  const status = ref<SystemStatus | null>(null)
  const wsState = ref<string>('idle')
  const lastUpdated = ref<Date | null>(null)
  const loadError = ref<string>('')

  let pollTimer: ReturnType<typeof setInterval> | null = null
  let detach: (() => void)[] = []
  let lastFetchAt = 0

  const gateway = computed(() => status.value?.gateway ?? null)
  const gatewayState = computed(() => gateway.value?.state ?? 'unknown')
  const gatewayConnected = computed(
    () => status.value?.gateway_connected ?? false,
  )
  const simulate = computed(() => Boolean(gateway.value?.simulate))
  const dbOk = computed(() => status.value?.db.ok ?? false)
  const version = computed(() => status.value?.version ?? '')

  /** LIVE = gateway process reachable and logged in / streaming. */
  const isLive = computed(() => {
    const state = gatewayState.value
    return (
      gatewayConnected.value &&
      (state === 'running' || state === 'connected' || state === 'streaming')
    )
  })

  let fetching: Promise<void> | null = null
  async function refreshStatus(force = false): Promise<void> {
    if (fetching && !force) return fetching
    const now = Date.now()
    if (!force && now - lastFetchAt < 2_000) return
    lastFetchAt = now
    fetching = (async () => {
      try {
        status.value = await systemApi.status()
        lastUpdated.value = new Date()
        loadError.value = ''
      } catch (err) {
        loadError.value = err instanceof Error ? err.message : String(err)
      } finally {
        fetching = null
      }
    })()
    return fetching
  }

  async function connectGateway(
    payload: Parameters<typeof systemApi.gatewayConnect>[0],
  ): Promise<void> {
    await systemApi.gatewayConnect(payload)
    await refreshStatus(true)
  }

  async function disconnectGateway(): Promise<void> {
    await systemApi.gatewayDisconnect()
    await refreshStatus(true)
  }

  function start(): void {
    stop()
    wsSystem.connect()
    detach.push(
      wsSystem.onState((s) => {
        wsState.value = s
      }),
    )
    // Push-driven refresh: any system broadcast re-syncs the snapshot.
    detach.push(
      wsSystem.on(() => {
        void refreshStatus(true)
      }),
    )
    void refreshStatus(true)
    pollTimer = setInterval(() => void refreshStatus(), POLL_INTERVAL_MS)
  }

  function stop(): void {
    if (pollTimer) clearInterval(pollTimer)
    pollTimer = null
    for (const off of detach) off()
    detach = []
    wsSystem.close()
  }

  return {
    status,
    wsState,
    lastUpdated,
    loadError,
    gateway,
    gatewayState,
    gatewayConnected,
    simulate,
    dbOk,
    version,
    isLive,
    refreshStatus,
    connectGateway,
    disconnectGateway,
    start,
    stop,
  }
})
