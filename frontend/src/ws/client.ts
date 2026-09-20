/**
 * MarketSocket — one authenticated WebSocket per channel.
 *
 * Connect flow: POST /api/v1/system/ws/ticket -> open /ws/{channel}?ticket=…
 * Features: exponential backoff reconnect (0.5s → 10s, resubscribes after
 * reconnect), 30s silent-peer watchdog forcing a reconnect, periodic client
 * pings, JSON envelope fan-out to listeners.
 */

import { systemApi } from '@/api/client'
import type { Envelope } from '@/api/types'

export type WsChannel = 'market' | 'analysis' | 'system'
export type WsState = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed'

type MessageListener = (env: Envelope) => void
type StateListener = (state: WsState) => void

const MIN_BACKOFF_MS = 500
const MAX_BACKOFF_MS = 10_000
const SILENCE_TIMEOUT_MS = 30_000
const PING_INTERVAL_MS = 15_000

export class MarketSocket {
  readonly channel: WsChannel

  private ws: WebSocket | null = null
  private state: WsState = 'idle'
  private desired = false
  private attempts = 0
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private watchdogTimer: ReturnType<typeof setInterval> | null = null
  private pingTimer: ReturnType<typeof setInterval> | null = null
  private lastMessageAt = 0
  private readonly subscriptions = new Set<string>()
  private readonly listeners = new Set<MessageListener>()
  private readonly stateListeners = new Set<StateListener>()

  constructor(channel: WsChannel) {
    this.channel = channel
  }

  // -- lifecycle -------------------------------------------------------------

  connect(): void {
    if (this.desired) return
    this.desired = true
    this.attempts = 0
    void this.open('connecting')
  }

  close(): void {
    this.desired = false
    this.clearTimers()
    if (this.ws) {
      this.ws.onclose = null
      this.ws.onerror = null
      this.ws.onmessage = null
      this.ws.onopen = null
      try {
        this.ws.close(1000)
      } catch {
        /* already closing */
      }
      this.ws = null
    }
    this.setState('closed')
  }

  // -- subscriptions ----------------------------------------------------------

  subscribe(instrument: string): void {
    const key = instrument.toUpperCase()
    this.subscriptions.add(key)
    this.send({ action: 'subscribe', instrument: key })
  }

  unsubscribe(instrument: string): void {
    const key = instrument.toUpperCase()
    this.subscriptions.delete(key)
    this.send({ action: 'unsubscribe', instrument: key })
  }

  // -- listeners ---------------------------------------------------------------

  on(listener: MessageListener): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  onState(listener: StateListener): () => void {
    this.stateListeners.add(listener)
    listener(this.state)
    return () => this.stateListeners.delete(listener)
  }

  getState(): WsState {
    return this.state
  }

  // -- internals ----------------------------------------------------------------

  private async open(initial: WsState): Promise<void> {
    if (!this.desired) return
    this.setState(initial)
    let ticket: string
    try {
      const res = await systemApi.wsTicket()
      ticket = res.ticket
    } catch {
      this.scheduleReconnect()
      return
    }
    if (!this.desired) return

    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    let ws: WebSocket
    try {
      ws = new WebSocket(
        `${proto}://${location.host}/ws/${this.channel}?ticket=${encodeURIComponent(ticket)}`,
      )
    } catch {
      this.scheduleReconnect()
      return
    }
    this.ws = ws

    ws.onopen = () => {
      this.attempts = 0
      this.lastMessageAt = Date.now()
      this.setState('open')
      this.resubscribeAll()
      this.startKeepalive()
    }
    ws.onmessage = (ev: MessageEvent) => this.handleMessage(ev)
    ws.onerror = () => {
      /* onclose follows; reconnect handled there */
    }
    ws.onclose = () => {
      this.stopKeepalive()
      this.ws = null
      if (this.desired) this.scheduleReconnect()
    }
  }

  private handleMessage(ev: MessageEvent): void {
    this.lastMessageAt = Date.now()
    let env: Envelope
    try {
      env = JSON.parse(ev.data as string) as Envelope
    } catch {
      return
    }
    if (
      env.type === 'hello' ||
      env.type === 'heartbeat' ||
      env.type === 'pong' ||
      env.type === 'error'
    ) {
      return
    }
    for (const listener of this.listeners) listener(env)
  }

  private send(msg: Record<string, unknown>): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg))
    }
  }

  private resubscribeAll(): void {
    for (const instrument of this.subscriptions) {
      this.send({ action: 'subscribe', instrument })
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer || !this.desired) return
    const delay = Math.min(
      MIN_BACKOFF_MS * 2 ** this.attempts,
      MAX_BACKOFF_MS,
    )
    this.attempts += 1
    this.setState('reconnecting')
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      void this.open('reconnecting')
    }, delay)
  }

  private startKeepalive(): void {
    this.stopKeepalive()
    this.pingTimer = setInterval(() => {
      this.send({ action: 'ping' })
    }, PING_INTERVAL_MS)
    this.watchdogTimer = setInterval(() => {
      if (Date.now() - this.lastMessageAt > SILENCE_TIMEOUT_MS && this.ws) {
        // Silent peer: force-close, onclose will reconnect.
        try {
          this.ws.close(4000)
        } catch {
          /* ignore */
        }
      }
    }, 5_000)
  }

  private stopKeepalive(): void {
    if (this.pingTimer) clearInterval(this.pingTimer)
    if (this.watchdogTimer) clearInterval(this.watchdogTimer)
    this.pingTimer = null
    this.watchdogTimer = null
  }

  private clearTimers(): void {
    this.stopKeepalive()
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.reconnectTimer = null
  }

  private setState(state: WsState): void {
    if (this.state === state) return
    this.state = state
    for (const listener of this.stateListeners) listener(state)
  }
}

// --- channel singletons --------------------------------------------------------

export const wsMarket = new MarketSocket('market')
export const wsAnalysis = new MarketSocket('analysis')
export const wsSystem = new MarketSocket('system')
