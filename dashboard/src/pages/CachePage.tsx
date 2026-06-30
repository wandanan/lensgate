import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../api/client'
import type { CacheEntry } from '../api/types'
import FilterBar from '../components/FilterBar'

export default function CachePage() {
  const [items, setItems] = useState<CacheEntry[]>([])
  const [total, setTotal] = useState(0)
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const fetchData = useCallback(async (q: string) => {
    setLoading(true)
    try {
      const params: Record<string, string> = {}
      if (q) params.q = q
      const res = await api.getCache(params)
      setItems(res.items)
      setTotal(res.total)
    } catch (err) {
      console.error('Failed to fetch cache:', err)
      setItems([])
      setTotal(0)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData('')
  }, [fetchData])

  const handleSearchChange = (value: string) => {
    setSearch(value)
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      fetchData(value)
    }, 300)
  }

  if (loading && items.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '32px 0', color: 'var(--color-text-muted)' }}>
        加载中...
      </div>
    )
  }

  return (
    <div>
      <p style={{ fontSize: '0.75rem', color: 'var(--color-text-muted)', marginBottom: '16px' }}>
        CacheStore · {total} 条目
      </p>
      <FilterBar>
        <input
          type="text"
          placeholder="搜索 hash 或文件名..."
          style={{ width: 320 }}
          value={search}
          onChange={(e) => handleSearchChange(e.target.value)}
        />
      </FilterBar>
      {items.length === 0 ? (
        <p style={{ color: 'var(--color-text-muted)', padding: '32px 0' }}>No cached items.</p>
      ) : (
        <div className="cache-grid">
          {items.map((item) => (
            <div key={item.hash} className="cache-item">
              <div className="cache-thumb">image</div>
              <div className="cache-meta">
                <div className="hash">{item.hash}</div>
                <div className="pos">{item.position_label} · {item.file_name}</div>
                {item.summary && (
                  <div style={{ color: 'var(--color-text-secondary)', marginTop: 4 }}>
                    通用描述: {item.summary}
                  </div>
                )}
                {Object.entries(item.focus_results ?? {}).map(([focus, result]) => (
                  <div key={focus} style={{ color: 'var(--color-text-muted)', fontSize: '0.6875rem', marginTop: 4 }}>
                    focus &ldquo;{focus}&rdquo; &rarr; {result}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
