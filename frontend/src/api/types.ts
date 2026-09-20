/** Hand-written mirrors of the backend REST / WS DTOs. */

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export interface User {
  id: number
  username: string
  email: string | null
  role: string
  is_active: boolean
  created_at: string | null
  permissions: string[]
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
  user: User
}

export interface UserCreatePayload {
  username: string
  password: string
  role: string
  email?: string
}

// ---------------------------------------------------------------------------
// Reference data
// ---------------------------------------------------------------------------

export interface Product {
  code: string
  name: string
  exchange: string
  category: string
  tick_size: number | null
  sort_order: number
}

export interface Instrument {
  instrument_id: string
  product_code: string | null
  exchange: string
  tick_size: number | null
  is_main: boolean
  first_seen: string | null
  last_seen: string | null
}

export interface InstrumentQuery {
  exchange?: string
  product?: string
  search?: string
  limit?: number
}

export interface WatchlistItem {
  instrument_id: string
  sort_order: number
  added_at: string | null
}

export interface Watchlist {
  id: number
  name: string
  created_at: string | null
  items: WatchlistItem[]
}

// ---------------------------------------------------------------------------
// System
// ---------------------------------------------------------------------------

export interface GatewayStatus {
  state: string
  instruments?: string[]
  batch_id?: string | null
  events_published?: number
  simulate?: boolean
  [key: string]: unknown
}

export interface IngestStatus {
  events_received: number
  last_event_at: string | null
}

export interface SystemStatus {
  gateway: GatewayStatus
  gateway_connected: boolean
  ingest: IngestStatus
  db: { ok: boolean }
  realtime: Record<string, unknown>
  version: string
}

export interface GatewayConnectPayload {
  fronts: string[]
  broker_id: string
  user: string
  password: string
  instruments: string[]
  remember: boolean
}

// ---------------------------------------------------------------------------
// WebSocket envelopes
// ---------------------------------------------------------------------------

export type EnvelopeType =
  | 'quote'
  | 'micro'
  | 'analysis'
  | 'connection'
  | 'system'
  | 'hello'
  | 'heartbeat'
  | 'pong'
  | 'error'

export interface Envelope<D = Record<string, unknown>> {
  v: number
  type: EnvelopeType
  instrument_id: string | null
  seq: number | null
  exchange_ts_ns: number | null
  local_ts_ns: number | null
  source: string
  data_mode: string
  data: D
}

export interface QuoteData {
  trading_day?: string | null
  last: number | null
  bid1: number | null
  ask1: number | null
  bid_volume1?: number | null
  ask_volume1?: number | null
  spread: number | null
  mid: number | null
  obi1?: number | null
  microprice?: number | null
  volume?: number | null
  open?: number | null
  high?: number | null
  low?: number | null
  pre_close?: number | null
  upper_limit?: number | null
  lower_limit?: number | null
  [key: string]: unknown
}

export interface WsTicket {
  ticket: string
  expires_in: number
}
