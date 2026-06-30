import { useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTheme } from '../hooks/useTheme'

interface Particle {
  x: number
  y: number
  r: number
  vx: number
  vy: number
  alpha: number
}

/* ─── SVG icons ─── */
const TraceIcon = (
  <>
    <path d="M3 3v18" />
    <circle cx="3" cy="7" r="2" />
    <circle cx="3" cy="15" r="2" />
    <path d="M7 7h14M7 15h14" />
  </>
)

const ReplayIcon = (
  <>
    <path d="M1 4v6h6" />
    <path d="M3.5 16A9 9 0 1 0 2 12" />
    <path d="M9 12h6M12 9l3 3-3 3" />
  </>
)

const MonitorIcon = (
  <>
    <rect x="2" y="3" width="20" height="14" rx="2" />
    <path d="M8 21h8M12 17v4" />
    <path d="M6 10l4 4 3-6 4 5" />
  </>
)

const features = [
  {
    icon: TraceIcon,
    label: '全链路追踪',
    sub: '7 阶段管道透明可观测',
  },
  {
    icon: ReplayIcon,
    label: '请求重放',
    sub: '一键重现、对比差异',
  },
  {
    icon: MonitorIcon,
    label: '实时洞察',
    sub: '成功率、耗时、趋势一览',
  },
]

export default function CoverPage() {
  const navigate = useNavigate()
  const { theme, toggleTheme } = useTheme()
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const particlesRef = useRef<Particle[]>([])
  const rafRef = useRef(0)

  const initParticles = useCallback(() => {
    const c = canvasRef.current
    if (!c) return
    const W = window.innerWidth
    const H = window.innerHeight
    c.width = W
    c.height = H
    const count = Math.floor((W * H) / 18000)
    const parts: Particle[] = []
    for (let i = 0; i < count; i++) {
      parts.push({
        x: Math.random() * W,
        y: Math.random() * H,
        r: Math.random() * 1.6 + 0.4,
        vx: Math.random() * 0.35 - 0.175,
        vy: Math.random() * 0.35 - 0.175,
        alpha: Math.random() * 0.45 + 0.1,
      })
    }
    particlesRef.current = parts
  }, [])

  useEffect(() => {
    initParticles()
    const handleResize = () => initParticles()
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [initParticles])

  useEffect(() => {
    const c = canvasRef.current
    if (!c) return
    const ctx = c.getContext('2d')
    if (!ctx) return

    const isDark = theme === 'dark'
    const color = isDark ? 'rgba(129,140,248,0.4)' : 'rgba(94,106,210,0.35)'

    function draw() {
      const parts = particlesRef.current
      const W = c!.width
      const H = c!.height
      ctx!.clearRect(0, 0, W, H)
      ctx!.fillStyle = color
      for (let i = 0; i < parts.length; i++) {
        const p = parts[i]
        ctx!.globalAlpha = p.alpha
        ctx!.beginPath()
        ctx!.arc(p.x, p.y, p.r, 0, Math.PI * 2)
        ctx!.fill()
        p.x += p.vx
        p.y += p.vy
        if (p.x < 0) p.x = W
        if (p.x > W) p.x = 0
        if (p.y < 0) p.y = H
        if (p.y > H) p.y = 0
      }
      ctx!.globalAlpha = 1
      rafRef.current = requestAnimationFrame(draw)
    }
    rafRef.current = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(rafRef.current)
  }, [theme])

  return (
    <div className="cover-page">
      {/* Background layers */}
      <div className="cover-bg">
        <div className="cover-grid-lines" />
        <div className="cover-grid-dots" />
        <canvas ref={canvasRef} className="cover-particles" />
      </div>

      {/* Theme toggle */}
      <button
        className="cover-theme-btn"
        onClick={toggleTheme}
        aria-label="切换主题"
      >
        <span>{theme === 'light' ? '☀️' : '🌙'}</span>
        <span>{theme === 'light' ? '亮色' : '暗色'}</span>
      </button>

      <main className="cover-main">
        <div className="cover-logo-type">Multi‑Modal Agent Gateway</div>

        <h1 className="cover-title">
          LensGate
          <span>多模态代理网关 · 监控与运维面板</span>
        </h1>

        <p className="cover-desc">
          让每一次代理请求的完整链路可观测、可回溯、可重放。<br />
          从请求到响应，7 阶段管道透明追踪。
        </p>

        <div className="cover-features">
          {features.map((f) => (
            <div key={f.label} className="cover-feature">
              <div className="cover-feature-icon">
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                >
                  {f.icon}
                </svg>
              </div>
              <div className="cover-feature-label">{f.label}</div>
              <div className="cover-feature-sub">{f.sub}</div>
            </div>
          ))}
        </div>

        <div className="cover-cta-wrap">
          <div className="cover-ring cover-ring-1" />
          <div className="cover-ring cover-ring-2" />
          <button
            className="cover-cta-btn"
            onClick={() => navigate('/dashboard')}
          >
            进入仪表盘 &nbsp;→
          </button>
        </div>
      </main>

      <div className="cover-status">
        <div className="cover-status-dot" />
        LENSGATE SYSTEM V1.0 / OPERATIONAL
      </div>
    </div>
  )
}
