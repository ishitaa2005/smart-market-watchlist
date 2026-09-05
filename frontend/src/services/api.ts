const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(/\/$/, '')

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers: { Accept: 'application/json', ...init?.headers } })
  } catch {
    throw new ApiError('Unable to connect to the market service. Try again.', 0)
  }
  if (!response.ok) {
    let message = 'The market service could not complete the request.'
    try {
      const body = (await response.json()) as { detail?: string }
      message = body.detail ?? message
    } catch {
      // The API may return an empty or non-JSON error response.
    }
    throw new ApiError(message, response.status)
  }
  return response.json() as Promise<T>
}

export interface HealthResponse { status: 'ok' }
export function getHealth() { return request<HealthResponse>('/health') }

export interface WatchlistItem {
  symbol: string
  name: string | null
  last_price: string | null
  last_price_at: string | null
  last_volume: string | null
  added_at: string
}

export interface WatchlistMutationResponse {
  symbol: string
  message: string
}

export function getWatchlist() { return request<WatchlistItem[]>('/watchlist') }
export function addToWatchlist(symbol: string) {
  return request<WatchlistMutationResponse>(`/watchlist/${encodeURIComponent(symbol)}`, { method: 'POST' })
}
export function removeFromWatchlist(symbol: string) {
  return request<WatchlistMutationResponse>(`/watchlist/${encodeURIComponent(symbol)}`, { method: 'DELETE' })
}

export interface AttentionReason {
  code: string
  message: string
  value: number | null
}

export interface StockDetails {
  symbol: string
  name: string | null
  last_price: number | null
  last_price_at: string | null
  last_volume: number | null
  week52_high: number | null
  week52_low: number | null
  data_status: 'fresh' | 'delayed' | 'stale' | 'unavailable'
  attention_score: number | null
  direction: 'UP' | 'DOWN' | 'NEUTRAL' | null
  confidence: 'high' | 'medium' | 'low' | null
  explanation: string | null
  reasons: AttentionReason[] | null
}

export function getStockDetails(symbol: string) {
  return request<StockDetails>(`/stocks/${encodeURIComponent(symbol)}`)
}

export interface SignificanceEvent {
  event_id: number
  symbol: string
  score: number | null
  direction: 'UP' | 'DOWN' | 'NEUTRAL' | null
  price: number | null
  occurred_at: string
  reasons: AttentionReason[] | null
  explanation: string | null
  confidence: 'high' | 'medium' | 'low' | null
  data_status: 'fresh' | 'delayed' | 'stale' | 'unavailable' | null
  status: 'active' | 'closed' | null
}

export interface AckResponse {
  symbol: string
  acknowledged: boolean
  last_seen_event_id: number
  message: string
}

export function getChanges() { return request<SignificanceEvent[]>('/watchlist/changes') }
export function acknowledgeChanges(symbol: string) {
  return request<AckResponse>(`/watchlist/${encodeURIComponent(symbol)}/ack`, { method: 'POST' })
}

export type DemoScenarioName = 'normal' | 'price_shock' | 'volume_anomaly' | 'relative_performance' | 'stale_data'

export interface DemoScenarioResponse {
  scenario: DemoScenarioName
  symbol: string
  success: boolean
  attention_score: number | null
  direction: string | null
  explanation: string | null
  data_status: string | null
  event_created: boolean
  error: string | null
}

export function runDemoScenario(scenario: DemoScenarioName, symbol: string) {
  return request<DemoScenarioResponse>(`/demo/scenarios/${scenario}/${encodeURIComponent(symbol)}`, { method: 'POST' })
}