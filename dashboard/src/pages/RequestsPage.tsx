import { useEffect, useState, useCallback, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type { TraceSummary } from '../api/types'
import FilterBar from '../components/FilterBar'
import Pagination from '../components/Pagination'

/* ------------------------------------------------------------------ */
/* helpers                                                             */
/* ------------------------------------------------------------------ */

function fmtTime(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleTimeString('zh-CN', { hour12: false })
}

function fmtDur(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`
  return `${ms.toFixed(0)}ms`
}

function truncate(s: string, max: number): string {
  if (s.length <= max) return s
  return s.slice(0, max) + '…'
}

/* ------------------------------------------------------------------ */
/* RequestCard                                                         */
/* ------------------------------------------------------------------ */

function RequestCard({ item }: { item: TraceSummary }) {
  const nav = useNavigate()

  const handleClick = () => {
    nav(`/requests/${item.id}`)
  }

  const isError = item.status_code >= 500
  const hasDec = item.decision_snippet != null
  const hasVis = item.vision_snippets != null && item.vision_snippets.length > 0

  /* variant detection */
  const isTextOnly = !item.has_images
  const isDecisionEmpty = item.has_images && hasDec && (item.decision_snippet?.hashes?.length ?? 0) === 0
  // const isFullPipeline = item.has_images && hasDec && !isDecisionEmpty

  return (
    <div
      className={`req-card${isError ? ' error' : ''}`}
      onClick={handleClick}
    >
      {/* header */}
      <div className="req-card-header">
        <span className="mono" style={{ fontWeight: 600 }}>#{item.id}</span>
        <span className="mono" style={{ color: 'var(--color-text-muted)' }}>{fmtTime(item.timestamp)}</span>
        <span className={`tag ${item.source_format === 'anthropic' ? 'tag-accent' : 'tag-info'}`}>
          {item.method} {item.path}
        </span>
        <span className={`tag ${item.source_format === 'anthropic' ? 'tag-accent' : 'tag-info'}`} style={{ opacity: 0.8 }}>
          {item.source_format}
        </span>
        <span style={{ color: 'var(--color-text-muted)' }}>&rarr;</span>
        <span>{item.target_model}</span>
        {item.image_count > 0 ? (
          <span style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary)' }}>{item.image_count}张图</span>
        ) : (
          <span className="tag-text">纯文本</span>
        )}
        <span className={`tag ${item.status_code < 300 ? 'tag-success' : 'tag-danger'}`}>{item.status_code}</span>
        <span className="mono flex-spacer" style={{ color: 'var(--color-text-secondary)' }}>{fmtDur(item.total_duration_ms)}</span>
      </div>

      {/* body */}
      <div className="req-card-body">
        {/* user input — always shown */}
        {item.user_input && (
          <div className="io-row">
            <span className="io-label input">📝 输入</span>
            <span className="io-content">{truncate(item.user_input, 200)}</span>
          </div>
        )}

        {/* decision — shown when has decision snippet */}
        {hasDec && item.decision_snippet && (
          <div className="io-row">
            <span className="io-label decision">🧠 决策</span>
            <div className="io-content">
              <span className={`tag ${item.decision_snippet.mode ? 'tag-accent' : 'tag-default'}`}>
                {item.decision_snippet.mode || 'unknown'}
              </span>
              {item.decision_snippet.focus && (
                <span className="io-content-secondary" style={{ marginLeft: 6 }}>
                  focus: "{truncate(item.decision_snippet.focus, 80)}"
                </span>
              )}
              {isDecisionEmpty ? (
                <div className="io-content-secondary" style={{ marginTop: 2 }}>
                  hashes: [] — 不需要重识别
                </div>
              ) : item.decision_snippet.hashes && item.decision_snippet.hashes.length > 0 && (
                <div className="io-hash">{item.decision_snippet.hashes.join(' · ')}</div>
              )}
              {item.decision_snippet.reasoning && (
                <div className="io-content-secondary" style={{ marginTop: 2 }}>
                  reasoning: {truncate(item.decision_snippet.reasoning, 100)}
                </div>
              )}
            </div>
          </div>
        )}

        {/* vision descriptions — shown when has vision snippets */}
        {hasVis && item.vision_snippets && (
          <div className="io-row">
            <span className="io-label vision">👁 视觉</span>
            <div className="io-content">
              {item.vision_snippets.map((v) => (
                <div className="vision-item" key={v.hash}>
                  <span className="vision-hash-label">{truncate(v.hash, 20)}</span>
                  <span className="io-content-secondary">&rarr; "{truncate(v.description, 150)}"</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* target response — shown when has preview */}
        {!isError && item.target_response_preview && (
          <div className="io-row">
            <span className="io-label target">🎯 回复</span>
            <div className="io-content-secondary">
              {truncate(item.target_response_preview, 300)}
            </div>
          </div>
        )}

        {/* error case: forwarded but failed */}
        {isError && (
          <div className="io-row">
            <span className="io-label" style={{ color: 'var(--color-danger)' }}>🎯 转发</span>
            <div style={{ color: 'var(--color-danger)', fontSize: '0.75rem' }}>
              ⚠ 目标模型 {item.target_model} 返回 {item.status_code} (超时 {fmtDur(item.total_duration_ms)})
            </div>
          </div>
        )}
      </div>

      {/* footer */}
      <div className="req-card-footer">
        {isTextOnly && !isError && (
          <span className="link-muted" onClick={(e) => e.stopPropagation()}>
            纯文本直通（跳过 Decision + Vision）
          </span>
        )}
        {!isTextOnly && (
          <>
            {isError && (
              <span className="link-accent" onClick={(e) => { e.stopPropagation(); /* replay will be wired in C03 */ }}>
                ⟳ 重放
              </span>
            )}
            <span className="link-accent">查看完整链路 &rarr;</span>
          </>
        )}
        {isTextOnly && isError && (
          <span className="link-accent">查看完整链路 &rarr;</span>
        )}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* RequestsPage                                                        */
/* ------------------------------------------------------------------ */

const PAGE_SIZE = 20
const POLL_INTERVAL = 5000

export default function RequestsPage() {
  const [searchParams, setSearchParams] = useSearchParams()

  // Read initial state from URL params
  const page = Number(searchParams.get('page')) || 1
  const statusFilter = searchParams.get('status') || ''
  const pathFilter = searchParams.get('path') || ''
  const typeFilter = searchParams.get('type') || ''
  const searchQuery = searchParams.get('q') || ''

  const [items, setItems] = useState<TraceSummary[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [toast, setToast] = useState<string | null>(null)
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const [searchInput, setSearchInput] = useState(searchQuery)
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Track filter values for polling (to avoid re-building fetch on every poll)
  const filtersRef = useRef({ status: statusFilter, path: pathFilter, type: typeFilter, q: searchQuery })
  filtersRef.current = { status: statusFilter, path: pathFilter, type: typeFilter, q: searchQuery }

  const showToast = useCallback((msg: string) => {
    setToast(msg)
    if (toastTimer.current) clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToast(null), 3000)
  }, [])

  // Update a single URL param, reset page to 1
  const setParam = useCallback((key: string, value: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      if (value) next.set(key, value)
      else next.delete(key)
      if (key !== 'page') next.delete('page')
      return next
    }, { replace: true })
  }, [setSearchParams])

  const fetchRequests = useCallback(async (p: number, filters: {
    status: string; path: string; type: string; q: string;
  }) => {
    setLoading(true)
    try {
      const params: Record<string, string | number | boolean | undefined> = {
        page: p,
        size: PAGE_SIZE,
      }
      if (filters.status) params.status = filters.status
      if (filters.path) params.path = filters.path
      if (filters.type === 'has_images') params.has_images = true
      else if (filters.type === 'text_only') params.has_images = false
      if (filters.q) params.q = filters.q

      const res = await api.getRequests(params)
      setItems(res.items)
      setTotal(res.total)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '获取请求列表失败'
      showToast(msg)
    } finally {
      setLoading(false)
    }
  }, [showToast])

  // Fetch on mount + filter/page change
  useEffect(() => {
    fetchRequests(page, { status: statusFilter, path: pathFilter, type: typeFilter, q: searchQuery })
  }, [page, statusFilter, pathFilter, typeFilter, searchQuery, fetchRequests])

  // Auto-poll: refresh current page every POLL_INTERVAL ms (silent, no loading spinner)
  useEffect(() => {
    const timer = setInterval(async () => {
      const f = filtersRef.current
      try {
        const params: Record<string, string | number | boolean | undefined> = {
          page,
          size: PAGE_SIZE,
        }
        if (f.status) params.status = f.status
        if (f.path) params.path = f.path
        if (f.type === 'has_images') params.has_images = true
        else if (f.type === 'text_only') params.has_images = false
        if (f.q) params.q = f.q

        const res = await api.getRequests(params)
        setItems(res.items)
        setTotal(res.total)
      } catch {
        // Silent fail on poll errors
      }
    }, POLL_INTERVAL)
    return () => clearInterval(timer)
  }, [page])

  // Sync searchInput with URL q param (when it changes externally, e.g. browser back)
  useEffect(() => {
    setSearchInput(searchQuery)
  }, [searchQuery])

  // Debounced search
  const handleSearchChange = (value: string) => {
    setSearchInput(value)
    if (searchTimer.current) clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => {
      setParam('q', value)
    }, 300)
  }

  const handleFilterChange = (key: string, value: string) => {
    setParam(key, value)
  }

  const handlePageChange = (p: number) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.set('page', String(p))
      return next
    }, { replace: true })
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div>
      <FilterBar>
        <select
          value={statusFilter}
          onChange={(e) => handleFilterChange('status', e.target.value)}
        >
          <option value="">全部状态</option>
          <option value="200">200</option>
          <option value="500">5xx</option>
        </select>
        <select
          value={pathFilter}
          onChange={(e) => handleFilterChange('path', e.target.value)}
        >
          <option value="">全部路径</option>
          <option value="/v1/messages">/v1/messages</option>
          <option value="/v1/chat/completions">/v1/chat/completions</option>
        </select>
        <select
          value={typeFilter}
          onChange={(e) => handleFilterChange('type', e.target.value)}
        >
          <option value="">全部类型</option>
          <option value="has_images">含图片</option>
          <option value="text_only">纯文本</option>
        </select>
        <input
          type="text"
          placeholder="搜索输入/输出/描述关键词..."
          value={searchInput}
          onChange={(e) => handleSearchChange(e.target.value)}
          style={{ flex: 1, maxWidth: 260 }}
        />
      </FilterBar>

      {loading && items.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '48px 0', color: 'var(--color-text-muted)' }}>
          加载中…
        </div>
      ) : items.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '48px 0', color: 'var(--color-text-muted)' }}>
          暂无请求记录
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {items.map((item) => (
            <RequestCard key={item.id} item={item} />
          ))}
        </div>
      )}

      <Pagination
        page={page}
        totalPages={totalPages}
        total={total}
        onPageChange={handlePageChange}
      />

      {/* toast */}
      {toast && (
        <div style={{
          position: 'fixed',
          bottom: 24,
          right: 24,
          background: 'var(--color-danger)',
          color: '#fff',
          padding: '10px 20px',
          borderRadius: 'var(--radius-button)',
          fontSize: '0.8125rem',
          fontWeight: 500,
          zIndex: 1000,
          boxShadow: 'var(--shadow-dropdown)',
        }}>
          {toast}
        </div>
      )}
    </div>
  )
}
