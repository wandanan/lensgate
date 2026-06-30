import { useEffect, useState, useCallback, useRef } from 'react'
import { api } from '../api/client'
import type { Stats } from '../api/types'
import StatCard from '../components/StatCard'
import MiniChart from '../components/MiniChart'

/* placeholder data when stats has no hourly breakdown */
const PLACEHOLDER_VOLUME = [10, 15, 12, 20, 28, 35, 30, 22, 18, 24, 32, 40, 38, 34, 26, 20, 16, 22, 30, 28, 24, 18, 14, 10]
const PLACEHOLDER_LATENCY = [20, 24, 28, 22, 26, 30, 35, 32, 28, 24, 20, 18, 22, 26, 30, 28, 24, 20, 18, 22, 26, 24, 20, 18]

function fmtNum(n: number): string {
  return n.toLocaleString('en-US')
}

function fmtMs(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}s`
  return `${n.toFixed(0)}ms`
}

function fmtPct(n: number): string {
  return `${(n * 100).toFixed(1)}%`
}

export default function DashboardHome() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [loading, setLoading] = useState(true)
  const [toast, setToast] = useState<string | null>(null)
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const showToast = useCallback((msg: string) => {
    setToast(msg)
    if (toastTimer.current) clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToast(null), 3000)
  }, [])

  const fetchStats = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.getStats()
      setStats(data)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '获取统计数据失败'
      showToast(msg)
    } finally {
      setLoading(false)
    }
  }, [showToast])

  useEffect(() => {
    fetchStats()
  }, [fetchStats])

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <h2 className="section-title" style={{ margin: 0 }}>Overview</h2>
        <button
          className="btn btn-secondary btn-sm"
          onClick={fetchStats}
          disabled={loading}
        >
          刷新
        </button>
      </div>

      <div className="stat-cards">
        <StatCard
          value={stats ? fmtNum(stats.total) : '--'}
          label="总请求数"
        />
        <StatCard
          value={stats ? fmtPct(stats.success_rate) : '--'}
          label="成功率"
        />
        <StatCard
          value={stats ? fmtMs(stats.avg_duration_ms) : '--'}
          label="平均耗时"
        />
        <StatCard
          value={stats ? fmtMs(stats.p99_duration_ms) : '--'}
          label="P99 耗时"
        />
        <StatCard
          value={stats ? fmtPct(stats.cache_hit_rate) : '--'}
          label="缓存命中率"
        />
        <StatCard
          value={stats ? fmtNum(stats.total_images) : '--'}
          label="今日图片"
        />
      </div>

      <div className="chart-card">
        <div className="chart-title">请求量趋势 (24h)</div>
        <MiniChart
          data={PLACEHOLDER_VOLUME}
          color="var(--color-accent-soft)"
        />
      </div>

      <div className="chart-card">
        <div className="chart-title">平均耗时趋势 (24h)</div>
        <MiniChart
          data={PLACEHOLDER_LATENCY}
          color="var(--color-info-soft)"
        />
      </div>

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
