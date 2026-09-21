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
  instruments?: Record<string, string>
  batch_id?: string | null
  events_published?: number
  simulate?: boolean
  [key: string]: unknown
}

export interface IngestStatus {
  events_received: number
  last_event_at: number | null
}

export interface SystemStatus {
  gateway: GatewayStatus
  gateway_connected: boolean
  ingest: IngestStatus
  db: { ok: boolean }
  realtime: Record<string, RealtimeMetrics>
  version: string
}

export interface GatewayConnectPayload {
  source_kind: string
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

// ---------------------------------------------------------------------------
// Historical market data (GET /market/ticks, /market/events)
// ---------------------------------------------------------------------------

export interface TickRow {
  id: number
  instrument_id: string
  ts: string | null
  exchange_ts_ns: number | null
  local_ts_ns: number | null
  seq: number | null
  last: number | null
  bid1: number | null
  ask1: number | null
  bid_volume1: number | null
  ask_volume1: number | null
  volume: number | null
  spread: number | null
  spread_ticks: number | null
  mid: number | null
  obi1: number | null
  source: string | null
  data_mode: string | null
  [key: string]: unknown
}

export interface MicroEventRow {
  id: number
  instrument_id: string | null
  ts: string | null
  exchange_ts_ns: number | null
  local_ts_ns: number | null
  seq: number | null
  label: string | null
  family: string | null
  state5: string | null
  direction: number | null
  reason: string | null
  direction_hint: number | null
  db_ticks: number | null
  da_ticks: number | null
  dl_ticks: number | null
  dm_ticks: number | null
  spread_prev_ticks: number | null
  spread_cur_ticks: number | null
  source: string | null
  data_mode: string | null
}

export interface HistoryQuery {
  from_ns?: string
  to_ns?: string
  label?: string
  limit?: number
  offset?: number
}

/** /ws/analysis envelope payload: rolling metrics + regime. */
export interface AnalysisData {
  metrics: RealtimeMetrics
  regime: string
  label: string
}

export interface RealtimeMetrics {
  bounce_ratio: number | null
  genuine_move_ratio: number | null
  one_tick_last_changes: number
  message_rate_per_min: number
  mean_spread_ticks: number | null
  msg_count: number
  last_event_ns: number | null
}
export interface ArchivePartition { instrument: string; day: string; staging: boolean }
export interface ReportJob {
  id: string; instrument: string; day: string; source: string
  status: string; created_at: string; snapshots?: number; error?: string
}
export interface MonitorSettings {
  enabled: boolean; feishu_app_id: string; feishu_app_secret: string
  feishu_receive_id: string; feishu_receive_id_type: string
  report_times: string[]; alert_only: boolean; title: string; tz: string
  has_secret?: boolean; test_request?: number
}
export interface MonitorWorker {
  heartbeat_at?: number; last_sent_at?: number; last_result?: string; test_request?: number
}
