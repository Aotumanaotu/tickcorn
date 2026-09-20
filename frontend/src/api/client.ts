/**
 * Thin fetch wrapper for the v1 REST API.
 *
 * - Bearer token lives in memory only (bound from the auth store).
 * - On 401 it attempts one cookie-based refresh + replay, then gives up
 *   and hands control back to the auth store (logout + redirect).
 */

import type {
  Envelope,
  GatewayConnectPayload,
  Instrument,
  InstrumentQuery,
  Product,
  SystemStatus,
  TokenResponse,
  User,
  UserCreatePayload,
  Watchlist,
  WsTicket,
} from './types'

const BASE = '/api/v1'

export class ApiError extends Error {
  status: number
  detail: string

  constructor(status: number, detail: string) {
    super(detail || `HTTP ${status}`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

// --- auth binding (avoids a circular import with the auth store) -----------
let getAccessToken: () => string | null = () => null
let onSessionExpired: () => void = () => {}
let onTokenRefreshed: ((t: TokenResponse) => void) | null = null

export function bindAuth(
  tokenGetter: () => string | null,
  expiredHandler: () => void,
  refreshHandler: ((t: TokenResponse) => void) | null,
): void {
  getAccessToken = tokenGetter
  onSessionExpired = expiredHandler
  onTokenRefreshed = refreshHandler
}

// --- core request ------------------------------------------------------------

interface RequestOptions {
  method?: string
  body?: unknown
  credentials?: RequestCredentials
  auth?: boolean
  signal?: AbortSignal
}

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const res = await doFetch(path, opts)
  if (res.status === 401 && opts.auth !== false) {
    // One refresh + replay attempt.
    try {
      const refreshed = await doFetch('/auth/refresh', {
        method: 'POST',
        credentials: 'include',
        auth: false,
      })
      if (!refreshed.ok) throw new ApiError(refreshed.status, 'refresh failed')
      const token = (await refreshed.json()) as TokenResponse
      onTokenRefreshed?.(token)
    } catch {
      onSessionExpired()
      throw new ApiError(401, 'Session expired')
    }
    const replay = await doFetch(path, opts)
    if (replay.status === 401) {
      onSessionExpired()
      throw new ApiError(401, 'Session expired')
    }
    return unwrap<T>(replay)
  }
  return unwrap<T>(res)
}

async function doFetch(path: string, opts: RequestOptions): Promise<Response> {
  const headers: Record<string, string> = {}
  let body: string | undefined
  if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(opts.body)
  }
  const token = opts.auth === false ? null : getAccessToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  return fetch(`${BASE}${path}`, {
    method: opts.method ?? 'GET',
    headers,
    body,
    credentials: opts.credentials ?? 'same-origin',
    signal: opts.signal,
  })
}

async function unwrap<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = ''
    try {
      const payload = (await res.json()) as { detail?: unknown }
      if (typeof payload.detail === 'string') detail = payload.detail
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail || res.statusText)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

function qs(params: Record<string, string | number | undefined>): string {
  const entries = Object.entries(params).filter(
    ([, v]) => v !== undefined && v !== '',
  )
  if (!entries.length) return ''
  const search = new URLSearchParams()
  for (const [k, v] of entries) search.set(k, String(v))
  return `?${search.toString()}`
}

// --- auth --------------------------------------------------------------------

export const api = {
  login(username: string, password: string): Promise<TokenResponse> {
    return request<TokenResponse>('/auth/login', {
      method: 'POST',
      body: { username, password },
      auth: false,
    })
  },

  refresh(): Promise<TokenResponse> {
    return request<TokenResponse>('/auth/refresh', {
      method: 'POST',
      credentials: 'include',
      auth: false,
    })
  },

  logout(): Promise<void> {
    return request<void>('/auth/logout', {
      method: 'POST',
      credentials: 'include',
    })
  },

  me(): Promise<User> {
    return request<User>('/auth/me')
  },
}

// --- reference data -----------------------------------------------------------

export const marketApi = {
  products(): Promise<Product[]> {
    return request<Product[]>('/products')
  },

  instruments(query: InstrumentQuery = {}): Promise<Instrument[]> {
    return request<Instrument[]>(`/instruments${qs({ ...query })}`)
  },

  watchlists(): Promise<Watchlist[]> {
    return request<Watchlist[]>('/watchlists')
  },

  createWatchlist(name: string): Promise<Watchlist> {
    return request<Watchlist>('/watchlists', {
      method: 'POST',
      body: { name },
    })
  },

  addWatchlistItem(watchlistId: number, instrumentId: string): Promise<Watchlist> {
    return request<Watchlist>(`/watchlists/${watchlistId}/items`, {
      method: 'POST',
      body: { instrument_id: instrumentId },
    })
  },

  removeWatchlistItem(
    watchlistId: number,
    instrumentId: string,
  ): Promise<Watchlist> {
    return request<Watchlist>(
      `/watchlists/${watchlistId}/items/${encodeURIComponent(instrumentId)}`,
      { method: 'DELETE' },
    )
  },
}

// --- system --------------------------------------------------------------------

export const systemApi = {
  status(): Promise<SystemStatus> {
    return request<SystemStatus>('/system/status')
  },

  gatewayConnect(payload: GatewayConnectPayload): Promise<Record<string, unknown>> {
    return request('/system/gateway/connect', { method: 'POST', body: payload })
  },

  gatewayDisconnect(): Promise<Record<string, unknown>> {
    return request('/system/gateway/disconnect', { method: 'POST' })
  },

  gatewaySubscribe(instruments: string[]): Promise<Record<string, unknown>> {
    return request('/system/gateway/subscribe', {
      method: 'POST',
      body: { instruments },
    })
  },

  users(): Promise<User[]> {
    return request<User[]>('/system/users')
  },

  createUser(payload: UserCreatePayload): Promise<User> {
    return request<User>('/system/users', { method: 'POST', body: payload })
  },

  wsTicket(): Promise<WsTicket> {
    return request<WsTicket>('/system/ws/ticket', { method: 'POST' })
  },
}

// Re-export so ws/client.ts can share the envelope type.
export type { Envelope }
