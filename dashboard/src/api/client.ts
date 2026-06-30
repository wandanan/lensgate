import type {
  TraceSummary,
  TraceRecord,
  DecisionRecord,
  CacheEntry,
  Stats,
  PaginatedResponse,
  ReplayResponse,
} from './types'

const BASE = '/api/dashboard'

function getApiKey(): string {
  /* from environment variable injected by Vite at build time, or global config */
  if (typeof window !== 'undefined' && (window as unknown as Record<string, unknown>).__LENSGATE_API_KEY__) {
    return (window as unknown as Record<string, string>).__LENSGATE_API_KEY__
  }
  return ''
}

async function get<T>(path: string, params?: Record<string, string | number | boolean | undefined>): Promise<T> {
  const url = new URL(`${BASE}${path}`, window.location.origin)
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') {
        url.searchParams.set(k, String(v))
      }
    })
  }
  const apiKey = getApiKey()
  const headers: Record<string, string> = {}
  if (apiKey) headers['x-api-key'] = apiKey

  const res = await fetch(url.toString(), { headers })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`API ${res.status}: ${text || res.statusText}`)
  }
  return res.json() as Promise<T>
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const url = new URL(`${BASE}${path}`, window.location.origin)
  const apiKey = getApiKey()
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (apiKey) headers['x-api-key'] = apiKey

  const res = await fetch(url.toString(), {
    method: 'POST',
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`API ${res.status}: ${text || res.statusText}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  getRequests: (params?: Record<string, string | number | boolean | undefined>) =>
    get<PaginatedResponse<TraceSummary>>('/requests', params),

  getRequestDetail: (id: string) => get<TraceRecord>(`/requests/${id}`),

  replayRequest: (id: string) => post<ReplayResponse>(`/requests/${id}/replay`),

  getDecisions: (params?: Record<string, string>) =>
    get<PaginatedResponse<DecisionRecord>>('/decisions', params),

  getCache: (params?: Record<string, string>) =>
    get<{ items: CacheEntry[]; total: number }>('/cache', params),

  getStats: () => get<Stats>('/stats'),
}
