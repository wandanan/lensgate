import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { TraceRecord, StageSnapshot, ReplayResponse } from '../api/types'

/* ═══════════════════════════════════════════════════════════════
   Constants
   ═══════════════════════════════════════════════════════════════ */

const STAGE_NAMES: Record<string, string> = {
  format_detect: 'Format Detector',
  image_check: 'Image Check',
  decision: 'Decision Engine',
  vision: 'Vision Client',
  rewrite: 'Request Rewriter',
  target: 'Target Client',
  response: 'Response Handler',
}

const STAGE_SUBTITLES: Record<string, string> = {
  format_detect: '检测请求格式 (Anthropic / OpenAI)',
  image_check: '检测消息中的图片块',
  decision: 'DeepSeek Chat · tool-calling · route_decision',
  vision: '视觉识别 · 多模态模型 · 图片→文本描述',
  rewrite: '图片块 → 视觉识别文本块',
  target: '发送改写后请求到目标模型',
  response: 'SSE 流解析 → 客户端响应',
}

const STAGE_ORDER = [
  'format_detect',
  'image_check',
  'decision',
  'vision',
  'rewrite',
  'target',
  'response',
]

type StageStatus = 'ok' | 'error' | 'skipped'

/* ═══════════════════════════════════════════════════════════════
   Helpers
   ═══════════════════════════════════════════════════════════════ */

function fmtDuration(ms: number): string {
  if (ms >= 1000) {
    const s = ms / 1000
    return s >= 10 ? `${Math.round(s)}s` : `${s.toFixed(1)}s`
  }
  if (ms < 1) return `${(ms * 1000).toFixed(0)}μs`
  return `${Math.round(ms)}ms`
}

function fmtBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)}MB`
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${bytes}B`
}

function fmtNum(n: number): string {
  return n.toLocaleString()
}

function findStage(
  stages: StageSnapshot[],
  name: string,
): StageSnapshot | undefined {
  return stages.find((s) => s.stage === name)
}

/* ═══════════════════════════════════════════════════════════════
   PipelineTimeline — internal component
   ═══════════════════════════════════════════════════════════════ */

const TIMELINE_COLORS: Record<string, string> = {
  format: '#a1a1aa',
  image_check: '#a1a1aa',
  decision: '#d97706',
  vision: '#2563eb',
  rewrite: '#8b5cf6',
  target: '#16a34a',
  response: '#a1a1aa',
}

interface PipelineTimelineProps {
  stages: StageSnapshot[]
  totalMs: number
  excludedStages: Set<number>
}

function PipelineTimeline({ stages, totalMs, excludedStages }: PipelineTimelineProps) {
  if (totalMs <= 0) return null

  return (
    <div className="pipeline-timeline">
      {STAGE_ORDER.map((name, i) => {
        const stageNum = i + 1
        const isExcluded = excludedStages.has(stageNum)
        const stage = findStage(stages, name)
        const dur = stage ? stage.duration_ms : 0
        const flex = totalMs > 0 ? (dur / totalMs) * 100 : 1
        const minFlex = flex < 0.5 ? 0.5 : flex

        return (
          <div
            key={name}
            className={`timeline-stage${isExcluded ? ' excluded' : ''}`}
            style={{
              flex: minFlex,
              minWidth: dur > 0 ? 60 : 30,
              backgroundColor: isExcluded ? undefined : (TIMELINE_COLORS[name] ?? '#a1a1aa'),
            }}
            title={isExcluded
              ? `${STAGE_NAMES[name]}: 已排除`
              : `${STAGE_NAMES[name]}: ${fmtDuration(dur)}`}
          >
            <span className="ts-dur">{isExcluded ? '--' : fmtDuration(dur)}</span>
            <span className="ts-name">
              {isExcluded ? '已排除' : name === 'image_check' ? 'ImgChk' : name.charAt(0).toUpperCase() + name.slice(1)}
            </span>
          </div>
        )
      })}
    </div>
  )
}

/* ═══════════════════════════════════════════════════════════════
   VisionImageCard
   ═══════════════════════════════════════════════════════════════ */

interface VisionImageCardProps {
  image: Record<string, unknown>
  index: number
  traceId: string
}

function VisionImageCard({ image, index, traceId }: VisionImageCardProps) {
  const hash = (image.hash as string) ?? 'unknown'
  const cacheHit = image.cache_hit === true
  const durationMs = (image.duration_ms as number) ?? 0
  const fileName = (image.file_name as string) ?? `image_${index + 1}`
  const description = (image.description as string) ?? ''
  const format = (image.format as string) ?? 'image/png'
  const size = (image.size_bytes as number) ?? 0
  const position = (image.position as number) ?? index + 1

  const imgSrc = hash && hash !== 'unknown' ? `/api/dashboard/requests/${traceId}/images/${hash}` : ''

  return (
    <div className="vision-card">
      <div className="vision-card-header">
        <div className="vision-img-preview">
          {imgSrc ? (
            <img
              src={imgSrc}
              alt={`Image ${index + 1}`}
              style={{ width: '100%', height: '100%', objectFit: 'cover' }}
              loading="lazy"
            />
          ) : (
            <div style={{ textAlign: 'center', lineHeight: 1.4 }}>
              <div style={{ fontSize: '1.5rem', marginBottom: 2 }}>&#x1F5BC;</div>
              <div style={{ fontSize: '0.6875rem' }}>{format}</div>
            </div>
          )}
        </div>
        <div className="vision-card-meta">
          <div className="vision-card-hash">{hash}</div>
          <div className="vision-card-tags">
            <span className={`tag ${cacheHit ? 'tag-success' : 'tag-warning'}`}>
              {cacheHit ? 'cache hit' : 'cache miss'}
            </span>
            <span className="mono" style={{ fontSize: '0.75rem' }}>
              {fmtDuration(durationMs)}
            </span>
            <span>第 {position} 张</span>
            <span style={{ color: 'var(--color-text-muted)' }}>
              {fileName}
            </span>
            {size > 0 && (
              <span style={{
                fontSize: '0.6875rem',
                color: 'var(--color-text-muted)',
              }}>
                {fmtBytes(size)} · {format}
              </span>
            )}
          </div>
        </div>
      </div>
      <div className="vision-card-body">
        <div className="vc-label">识图结果</div>
        <div style={{ fontStyle: description ? 'normal' : 'italic', color: description ? undefined : 'var(--color-text-muted)' }}>
          {description || '(无描述)'}
        </div>
      </div>
    </div>
  )
}

/* ═══════════════════════════════════════════════════════════════
   ReplayCompare
   ═══════════════════════════════════════════════════════════════ */

interface ReplayRow {
  stage: string
  original: string
  replay: string
  diff: string
  isDiff: boolean
}

interface ReplayCompareProps {
  originalTrace: TraceRecord
  replayTrace: TraceRecord
}

function ReplayCompare({ originalTrace, replayTrace }: ReplayCompareProps) {
  const compareStages = ['decision', 'vision', 'target']
  const rows: ReplayRow[] = compareStages.map((name) => {
    const orig = findStage(originalTrace.stages, name)
    const repl = findStage(replayTrace.stages, name)

    const origDur = orig ? orig.duration_ms : 0
    const replDur = repl ? repl.duration_ms : 0
    const diffMs = replDur - origDur
    const diffSign = diffMs > 0 ? '+' : ''
    const diffStr = diffMs === 0
      ? '0'
      : `${diffSign}${fmtDuration(Math.abs(diffMs))}`
    const isDiff = Math.abs(diffMs) > 50

    return {
      stage: STAGE_NAMES[name],
      original: orig ? fmtDuration(origDur) : '--',
      replay: repl ? fmtDuration(replDur) : '--',
      diff: diffStr,
      isDiff,
    }
  })

  // Total row
  const totalDiff = replayTrace.total_duration_ms - originalTrace.total_duration_ms
  const totalDiffSign = totalDiff > 0 ? '+' : ''
  const totalDiffStr = totalDiff === 0
    ? '0'
    : `${totalDiffSign}${fmtDuration(Math.abs(totalDiff))}`

  rows.push({
    stage: 'Total',
    original: fmtDuration(originalTrace.total_duration_ms),
    replay: fmtDuration(replayTrace.total_duration_ms),
    diff: totalDiffStr,
    isDiff: Math.abs(totalDiff) > 100,
  })

  return (
    <div className="replay-section">
      <div className="detail-header">
        <div style={{
          fontSize: '1rem',
          fontWeight: 600,
          marginBottom: 12,
        }}>
          重放对比 — 原请求 #{originalTrace.id} vs 重放 #{replayTrace.id}
        </div>
        <table className="replay-diff-table">
          <thead>
            <tr>
              <th style={{ width: 120 }}>Stage</th>
              <th>原请求 #{originalTrace.id}</th>
              <th>重放 #{replayTrace.id}</th>
              <th>差异</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.stage} className={row.isDiff ? 'diff-row' : ''}>
                <td className="stage-col">{row.stage}</td>
                <td>{row.original}</td>
                <td>{row.replay}</td>
                <td style={{
                  color: row.diff.startsWith('-')
                    ? 'var(--color-success)'
                    : row.diff === '0'
                      ? undefined
                      : 'var(--color-danger)',
                }}>
                  {row.diff}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/* ═══════════════════════════════════════════════════════════════
   RequestDetailPage — main component
   ═══════════════════════════════════════════════════════════════ */

export default function RequestDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const [trace, setTrace] = useState<TraceRecord | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [replayLoading, setReplayLoading] = useState(false)
  const [replayTrace, setReplayTrace] = useState<TraceRecord | null>(null)

  // Track which stages are open; keys 3,4 start open
  const [openStages, setOpenStages] = useState<Set<number>>(
    () => new Set([3, 4]),
  )

  // Stage exclusion & raw data & copy toast
  const [excludedStages, setExcludedStages] = useState<Set<number>>(() => new Set())
  const [showRawData, setShowRawData] = useState<Set<number>>(() => new Set())
  const [copied, setCopied] = useState(false)
  const [copiedStage, setCopiedStage] = useState<number | null>(null)
  const [includeRewriter, setIncludeRewriter] = useState(false)
  const copyTimerRef = useRef<ReturnType<typeof setTimeout>>(0)
  const stageCopyTimerRef = useRef<ReturnType<typeof setTimeout>>(0)

  /* ---- data fetch ---- */

  useEffect(() => {
    if (!id) return
    let cancelled = false

    async function load() {
      setLoading(true)
      setError(null)
      try {
        const data = await api.getRequestDetail(id!)
        if (!cancelled) setTrace(data)
      } catch (err: unknown) {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : 'Unknown error'
          setError(msg)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    return () => { cancelled = true }
  }, [id])

  /* ---- helpers ---- */

  const getStage = useCallback(
    (name: string) => trace ? findStage(trace.stages, name) : undefined,
    [trace],
  )

  const stageStatus = useCallback((name: string): StageStatus => {
    const s = getStage(name)
    if (!s) return 'skipped'
    return (s.status as StageStatus) === 'error' ? 'error' : 'ok'
  }, [getStage])

  /* ---- toggle ---- */

  const toggleStage = useCallback((n: number) => {
    setOpenStages((prev) => {
      const next = new Set(prev)
      if (next.has(n)) next.delete(n)
      else next.add(n)
      return next
    })
  }, [])

  const toggleAllStages = useCallback(() => {
    setOpenStages((prev) => {
      if (prev.size >= 7) return new Set()
      return new Set([1, 2, 3, 4, 5, 6, 7])
    })
  }, [])

  const handleCopyStage = useCallback((stageNum: number, stageName: string, stage: StageSnapshot | undefined) => {
    const displayName = STAGE_NAMES[stageName] ?? stageName
    const data = stage
      ? { stage: displayName, status: stage.status, duration_ms: stage.duration_ms, input: stage.input, output: stage.output }
      : { stage: displayName, status: 'skipped' }
    navigator.clipboard.writeText(JSON.stringify(data, null, 2)).then(() => {
      setCopiedStage(stageNum)
      clearTimeout(stageCopyTimerRef.current)
      stageCopyTimerRef.current = setTimeout(() => setCopiedStage(null), 2000)
    }).catch(() => {})
  }, [])

  const toggleExcludeStage = useCallback((n: number) => {
    setExcludedStages((prev) => {
      const next = new Set(prev)
      if (next.has(n)) next.delete(n)
      else next.add(n)
      return next
    })
  }, [])

  const toggleRawData = useCallback((n: number) => {
    setShowRawData((prev) => {
      const next = new Set(prev)
      if (next.has(n)) next.delete(n)
      else next.add(n)
      return next
    })
  }, [])

  const handleCopyTrace = useCallback(() => {
    if (!trace) return

    // Filter out excluded stages
    const excludedNames = new Set(
      Array.from(excludedStages).map((n) => STAGE_ORDER[n - 1]).filter(Boolean)
    )
    const visibleStages = trace.stages.filter((s) => !excludedNames.has(s.stage))

    // Strip Rewriter content: keep key structure, replace all leaf string values
    function stripContent(v: unknown): unknown {
      if (typeof v === 'string') return '"[…]"'
      if (typeof v === 'number' || typeof v === 'boolean' || v === null) return v
      if (Array.isArray(v)) {
        if (v.length === 0) return []
        return [stripContent(v[0]), `…[${v.length - 1} more items]`]
      }
      if (v && typeof v === 'object') {
        const out: Record<string, unknown> = {}
        for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
          out[k] = stripContent(val)
        }
        return out
      }
      return v
    }

    // Strip Rewriter input/output when not opted in
    const cleanStages = visibleStages.map((s) => {
      if (s.stage !== 'rewrite' || includeRewriter) return s
      return { ...s, input: stripContent(s.input), output: stripContent(s.output) }
    })

    // Build stage map keyed by name, ordered by STAGE_ORDER
    const stageByName: Record<string, unknown> = {}
    for (const name of STAGE_ORDER) {
      const s = cleanStages.find((cs) => cs.stage === name)
      if (!s) continue
      stageByName[name] = { duration_ms: s.duration_ms, status: s.status, input: s.input, output: s.output }
    }

    const source = {
      id: trace.id,
      timestamp: trace.timestamp,
      method: trace.method,
      path: trace.path,
      source_format: trace.source_format,
      target_model: trace.target_model,
      stream: trace.stream,
      status_code: trace.status_code,
      total_duration_ms: trace.total_duration_ms,
      stages: stageByName,
      replay_of: trace.replay_of,
      replays: trace.replays,
    }
    const data = JSON.stringify(source, null, 2)

    navigator.clipboard.writeText(data).then(() => {
      setCopied(true)
      clearTimeout(copyTimerRef.current)
      copyTimerRef.current = setTimeout(() => setCopied(false), 2000)
    }).catch(() => {
      // Fallback for older browsers
      const ta = document.createElement('textarea')
      ta.value = data
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
      setCopied(true)
      clearTimeout(copyTimerRef.current)
      copyTimerRef.current = setTimeout(() => setCopied(false), 2000)
    })
  }, [trace, excludedStages, includeRewriter])

  /* ---- replay ---- */

  const handleReplay = useCallback(async () => {
    if (!id || replayLoading) return
    setReplayLoading(true)
    try {
      const result: ReplayResponse = await api.replayRequest(id)
      // Fetch the new replay trace
      const newTrace = await api.getRequestDetail(result.replay_id)
      setReplayTrace(newTrace)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Unknown error'
      setError(msg)
    } finally {
      setReplayLoading(false)
    }
  }, [id, replayLoading])

  /* ---- derived data ---- */

  const totalMs = useMemo(
    () => trace?.total_duration_ms ?? 0,
    [trace],
  )

  const pipelineFormula = useMemo(() => {
    if (!trace) return ''
    const parts = STAGE_ORDER.map((name, i) => {
      const label = name === 'image_check' ? 'ImageCheck' : name.charAt(0).toUpperCase() + name.slice(1)
      if (excludedStages.has(i + 1)) return `${label}(已排除)`
      const s = findStage(trace.stages, name)
      return `${label}(${s ? fmtDuration(s.duration_ms) : '--'})`
    })
    const activeTotal = STAGE_ORDER.reduce((sum, name, i) => {
      if (excludedStages.has(i + 1)) return sum
      const s = findStage(trace.stages, name)
      return sum + (s ? s.duration_ms : 0)
    }, 0)
    return parts.join(' + ') + ` = ${fmtDuration(activeTotal)}`
  }, [trace, totalMs, excludedStages])

  /* ---- render ---- */

  // Loading skeleton
  if (loading) {
    return (
      <div>
        <div className="breadcrumb">
          <Link to="/requests">&larr; 返回列表</Link>
          {' / '}
          <span className="current">...</span>
        </div>
        <div style={{
          background: 'var(--color-bg-alt)',
          borderRadius: 'var(--radius-card)',
          height: 120,
          marginBottom: 20,
        }} />
        {[1, 2, 3, 4, 5, 6, 7].map((n) => (
          <div
            key={n}
            style={{
              height: 52,
              background: 'var(--color-bg-alt)',
              borderRadius: 'var(--radius-card)',
              marginBottom: 10,
              opacity: 1 - n * 0.06,
            }}
          />
        ))}
      </div>
    )
  }

  // Error state
  if (error && !trace) {
    const is404 = error.includes('404')
    return (
      <div>
        <div className="breadcrumb">
          <Link to="/requests">&larr; 返回列表</Link>
          {' / '}
          <span className="current">错误</span>
        </div>
        <div style={{
          padding: '48px 0',
          textAlign: 'center',
          color: 'var(--color-text-muted)',
        }}>
          <div style={{ fontSize: '1.25rem', marginBottom: 8, color: 'var(--color-text)' }}>
            {is404 ? '请求不存在' : '加载失败'}
          </div>
          <div style={{ fontSize: '0.8125rem', marginBottom: 16 }}>
            {is404 ? `请求 #${id} 未找到，可能已被删除或 ID 不正确。` : error}
          </div>
          <button className="btn btn-secondary" onClick={() => navigate('/requests')}>
            返回列表
          </button>
        </div>
      </div>
    )
  }

  if (!trace) return null

  // Decision data
  const decisionStage = getStage('decision')
  const decisionInput = (decisionStage?.input ?? {}) as Record<string, unknown>
  const decisionOutput = (decisionStage?.output ?? {}) as Record<string, unknown>
  const systemPrompt = (decisionInput.system_prompt as string) ?? ''
  const constructedPrompt = (decisionInput.constructed_prompt as string) ?? ''
  const rawToolCallJson = (decisionOutput.raw_json ?? decisionOutput.tool_call_json) as string
  const decisionMode = (decisionOutput.mode as string) ?? ''
  const decisionHashes = (decisionOutput.hashes ?? decisionOutput.image_hashes) as string[] ?? []
  const decisionFocus = (decisionOutput.focus_prompt as string) ?? ''
  const decisionReasoning = (decisionOutput.reasoning as string) ?? ''
  const decisionAttempt = (decisionOutput.attempt as number) ?? 0
  const decisionMaxAttempts = (decisionInput.max_attempts as number) ?? 2
  const decisionModel = (decisionInput.model as string) ?? 'deepseek-chat'
  const decisionEndpoint = (decisionInput.endpoint as string) ?? ''
  const decisionMaxTokens = (decisionInput.max_tokens as number) ?? 400
  const decisionTemperature = (decisionInput.temperature as number) ?? 0.1

  // Vision data
  const visionStage = getStage('vision')
  const visionInput = (visionStage?.input ?? {}) as Record<string, unknown>
  const visionOutput = (visionStage?.output ?? {}) as Record<string, unknown>
  const raw = visionOutput.descriptions ?? visionOutput.images ?? visionOutput.results ?? []
  const visionImages = Array.isArray(raw) ? raw as Record<string, unknown>[] : []
  const visionMode = (visionInput.mode ?? visionOutput.mode) as string ?? ''
  const visionFocus = (visionInput.focus as string) ?? ''
  const visionPrompt = (visionInput.prompt as string) ?? (visionImages.length > 0 ? (visionImages[0].prompt as string) : '') ?? ''
  const visionModel = (visionInput.model ?? visionOutput.model) as string ?? ''
  const visionEndpoint = (visionInput.endpoint ?? visionOutput.endpoint) as string ?? ''
  const visionMaxTokens = (visionInput.max_tokens ?? visionOutput.max_tokens) as number ?? 2000
  const visionReason = (visionInput.reason as string) ?? ''
  const visionParallel = visionImages.length > 1

  // Rewrite data
  const rewriteStage = getStage('rewrite')
  const rewriteInput = (rewriteStage?.input ?? {}) as Record<string, unknown>
  const rewriteOutput = (rewriteStage?.output ?? {}) as Record<string, unknown>
  const originalBody = (rewriteInput.original_body as Record<string, unknown>) ?? trace.original_body
  const rewrittenBody = (rewriteOutput.rewritten_body as Record<string, unknown>) ?? {}
  const imageBlockCount = (rewriteInput.image_block_count as number) ?? 0

  // Target data
  const targetStage = getStage('target')
  const targetInput = (targetStage?.input ?? {}) as Record<string, unknown>
  const targetOutput = (targetStage?.output ?? {}) as Record<string, unknown>
  const targetEndpoint = (targetInput.endpoint as string) ?? ''
  const targetModel = (targetInput.model as string) ?? trace.target_model
  const targetStream = (targetInput.stream as boolean) ?? trace.stream
  const targetTimeout = (targetInput.timeout_s as number) ?? 120
  const targetHeaders = (targetInput.headers as Record<string, string>) ?? {}
  const targetRequestBody = (targetInput.body as Record<string, unknown>) ?? rewrittenBody
  const targetConnectionMs = (targetOutput.connection_ms as number) ?? 0
  const targetTtfbMs = (targetOutput.ttfb_ms as number) ?? 0
  const targetStreamingMs = (targetOutput.streaming_ms as number) ?? 0
  const targetResponsePreview = (targetOutput.response_preview as string) ?? ''
  const targetStreamLines = (targetOutput.stream_lines as number) ?? 0

  // Response data
  const responseStage = getStage('response')
  const responseOutput = (responseStage?.output ?? {}) as Record<string, unknown>
  const responseStatus = (responseOutput.status_code as number) ?? trace.status_code
  const responseBytes = (responseOutput.response_bytes as number) ?? 0
  const responseStreamLines = (responseOutput.stream_lines as number) ?? 0
  const responseModel = (responseOutput.model as string) ?? trace.target_model
  const responseStopReason = (responseOutput.stop_reason as string) ?? ''
  const responseOutputTokens = (responseOutput.output_tokens as number) ?? 0

  return (
    <div>
      {/* Breadcrumb */}
      <div className="breadcrumb">
        <Link to="/requests">&larr; 返回列表</Link>
        {' / '}
        请求 <span className="current">#{trace.id}</span>
        {' '}
        <span className="detail-time">{trace.timestamp}</span>
      </div>

      {/* Detail Header Card */}
      <div className="detail-header">
        <div className="detail-header-top">
          <div>
            <div className="detail-id">#{trace.id}</div>
            <div className="detail-meta" style={{ marginTop: 6 }}>
              <span className="tag tag-accent">{trace.method}</span>
              <span>{trace.path}</span>
              <span className="tag tag-accent">{trace.source_format}</span>
              <span style={{ color: 'var(--color-text-muted)' }}>&rarr;</span>
              <span>{trace.target_model}</span>
              <span className={`tag ${trace.status_code < 400 ? 'tag-success' : 'tag-danger'}`}>
                {trace.status_code}
              </span>
              <span className="mono" style={{
                fontSize: '0.8125rem',
                color: 'var(--color-text-secondary)',
              }}>
                {fmtDuration(totalMs)}
              </span>
              <span style={{ fontSize: '0.75rem', color: 'var(--color-text-muted)' }}>
                {trace.stream ? 'stream' : 'non-stream'}
                {visionImages.length > 0 && ` · ${visionImages.length}张图`}
                {responseBytes > 0 && ` · ${fmtBytes(responseBytes)}`}
              </span>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
            <button
              className="btn btn-ghost btn-sm"
              onClick={handleCopyTrace}
              title="复制链路 JSON 数据到剪贴板"
            >
              &#x1F4CB; 复制{excludedStages.size > 0 ? '链路' : '全链路'}
            </button>
            <label
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                fontSize: '0.6875rem',
                color: 'var(--color-text-muted)',
                cursor: 'pointer',
                userSelect: 'none',
              }}
              title="默认复制时 Rewriter 的 input/output 仅保留字段结构。勾选后保留内容（仍会截断 base64）"
            >
              <input
                type="checkbox"
                checked={includeRewriter}
                onChange={(e) => setIncludeRewriter(e.target.checked)}
                style={{ cursor: 'pointer', accentColor: 'var(--color-accent)' }}
              />
              含 Rewriter
            </label>
            <button
              className="btn btn-primary"
              onClick={handleReplay}
              disabled={replayLoading}
            >
              {replayLoading ? '重放中...' : '⟳ 重放此请求'}
            </button>
            <button
              className="btn btn-secondary btn-sm"
              onClick={toggleAllStages}
            >
              全部展开/折叠
            </button>
            <button className="btn btn-danger btn-sm">删除</button>
          </div>
        </div>

        {/* Pipeline Timeline Bar */}
        <PipelineTimeline stages={trace.stages} totalMs={totalMs} excludedStages={excludedStages} />
      </div>

      {/* Stage Filter Bar */}
      <div className="stage-filter-bar">
        <span className="filter-label">显示环节</span>
        {STAGE_ORDER.map((name, i) => {
          const stageNum = i + 1
          const isExcluded = excludedStages.has(stageNum)
          const color = TIMELINE_COLORS[name] ?? '#a1a1aa'
          return (
            <button
              key={name}
              className={`stage-filter-chip${isExcluded ? ' excluded' : ' active'}`}
              onClick={() => toggleExcludeStage(stageNum)}
              title={isExcluded ? `点击恢复 ${STAGE_NAMES[name]}` : `点击排除 ${STAGE_NAMES[name]}`}
            >
              <span className="chip-num" style={{ background: isExcluded ? 'var(--color-text-muted)' : color }}>
                {stageNum}
              </span>
              {STAGE_NAMES[name] ?? name}
            </button>
          )
        })}
        {excludedStages.size > 0 && (
          <button
            className="stage-filter-chip active"
            style={{ marginLeft: 8 }}
            onClick={() => setExcludedStages(new Set())}
          >
            全部恢复
          </button>
        )}
      </div>

      {/* Stage Panels */}
      {STAGE_ORDER.map((stageName, i) => {
        const stageNum = i + 1
        const s = getStage(stageName)
        const isExcluded = excludedStages.has(stageNum)
        const isOpen = openStages.has(stageNum)
        const showRaw = showRawData.has(stageNum)

        // Excluded stage placeholder
        if (isExcluded) {
          return (
            <div key={stageName}>
              {i > 0 && <div className="data-flow-connector excluded" />}
              <div className="excluded-stage">
                <div className="excluded-stage-inner">
                  <div className="excluded-stage-label">
                    <div className={`ds-stage-num s${stageNum}`} style={{ opacity: 0.4 }}>
                      {stageNum}
                    </div>
                    <span>{STAGE_NAMES[stageName] ?? stageName}</span>
                    <span style={{ fontSize: '0.75rem' }}>已排除</span>
                  </div>
                  <button
                    className="excluded-stage-btn"
                    onClick={() => toggleExcludeStage(stageNum)}
                  >
                    ↶ 恢复
                  </button>
                </div>
              </div>
            </div>
          )
        }

        return (
          <div key={stageName}>
            {/* Data flow connector between stages */}
            {i > 0 && (
              <div className="data-flow-connector">
                <div className="flow-line" />
                <div className="flow-arrow" />
                <span className="flow-label">
                  {STAGE_ORDER[i - 1] === 'image_check' ? 'ImgChk' : STAGE_ORDER[i - 1].charAt(0).toUpperCase() + STAGE_ORDER[i - 1].slice(1)}
                  {' → '}
                  {stageName === 'image_check' ? 'ImgChk' : stageName.charAt(0).toUpperCase() + stageName.slice(1)}
                </span>
              </div>
            )}
            <div
              className={`detail-stage${isOpen ? ' open' : ''}`}
            >
              <div
                className="ds-header"
                onClick={() => toggleStage(stageNum)}
              >
                <div className="ds-header-left">
                  <div className={`ds-stage-num s${stageNum}`}>
                    {stageNum}
                  </div>
                  <div>
                    <div className="ds-title">
                      {STAGE_NAMES[stageName] ?? stageName}
                    </div>
                    <div className="ds-subtitle">
                      {STAGE_SUBTITLES[stageName] ?? ''}
                    </div>
                  </div>
                </div>
                <div className="ds-status">
                  <span className={`tag ${
                    stageStatus(stageName) === 'ok' ? 'tag-success'
                      : stageStatus(stageName) === 'error' ? 'tag-danger'
                      : 'tag-default'
                  }`}>
                    {stageStatus(stageName) === 'ok' ? '✓ OK'
                      : stageStatus(stageName) === 'error' ? '✗ Error'
                      : '→ Skipped'}
                  </span>
                  <span className="ds-subtitle">
                    {s ? fmtDuration(s.duration_ms) : '--'}
                    {stageName === 'decision' && decisionAttempt > 0
                      ? ` · attempt ${decisionAttempt}/${decisionMaxAttempts}`
                      : ''}
                    {stageName === 'target' && targetStreamLines > 0
                      ? ` · ${targetStreamLines} lines`
                      : ''}
                  </span>
                  <button
                    className="stage-copy-btn"
                    onClick={(e) => {
                      e.stopPropagation()
                      handleCopyStage(stageNum, stageName, s)
                    }}
                    title="复制此 Stage 数据"
                  >
                    {copiedStage === stageNum ? (
                      <svg width="12" height="12" viewBox="0 0 14 14" fill="none" stroke="var(--color-success)" strokeWidth="1.5">
                        <path d="M3 7l3 3 5-6" />
                      </svg>
                    ) : (
                      <svg width="12" height="12" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                        <rect x="4" y="4" width="8" height="8" rx="1" />
                        <path d="M2 10V2h8" />
                      </svg>
                    )}
                  </button>
                  <svg
                    className="ds-chevron"
                    viewBox="0 0 14 14"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.5"
                  >
                    <path d="M5 2l5 5-5 5" />
                  </svg>
                </div>
              </div>

              <div className="ds-body">
                {/* ---- Stage 1: Format Detector ---- */}
                {stageName === 'format_detect' && (
                  <Stage1Body trace={trace} s={s} />
                )}

                {/* ---- Stage 2: Image Check ---- */}
                {stageName === 'image_check' && (
                  <Stage2Body s={s} />
                )}

                {/* ---- Stage 3: Decision Engine ---- */}
                {stageName === 'decision' && (
                  <Stage3Body
                    systemPrompt={systemPrompt}
                    constructedPrompt={constructedPrompt}
                    rawToolCallJson={rawToolCallJson}
                    mode={decisionMode}
                    hashes={decisionHashes}
                    focus={decisionFocus}
                    reasoning={decisionReasoning}
                    attempt={decisionAttempt}
                    maxAttempts={decisionMaxAttempts}
                    model={decisionModel}
                    endpoint={decisionEndpoint}
                    maxTokens={decisionMaxTokens}
                    temperature={decisionTemperature}
                    hasImages={visionImages.length > 0}
                  />
                )}

                {/* ---- Stage 4: Vision Client ---- */}
                {stageName === 'vision' && (
                  <Stage4Body
                    images={visionImages}
                    mode={visionMode}
                    focus={visionFocus}
                    prompt={visionPrompt}
                    model={visionModel}
                    endpoint={visionEndpoint}
                    maxTokens={visionMaxTokens}
                    parallel={visionParallel}
                    traceId={trace.id}
                    reason={visionReason}
                  />
                )}

                {/* ---- Stage 5: Request Rewriter ---- */}
                {stageName === 'rewrite' && (
                  <Stage5Body
                    originalBody={originalBody}
                    rewrittenBody={rewrittenBody}
                    imageBlockCount={imageBlockCount}
                  />
                )}

                {/* ---- Stage 6: Target Client ---- */}
                {stageName === 'target' && (
                  <Stage6Body
                    endpoint={targetEndpoint}
                    model={targetModel}
                    stream={targetStream}
                    timeout={targetTimeout}
                    headers={targetHeaders}
                    requestBody={targetRequestBody}
                    connectionMs={targetConnectionMs}
                    ttfbMs={targetTtfbMs}
                    streamingMs={targetStreamingMs}
                    totalMs={s ? s.duration_ms : 0}
                    responsePreview={targetResponsePreview}
                    streamLines={targetStreamLines}
                  />
                )}

                {/* ---- Stage 7: Response Handler ---- */}
                {stageName === 'response' && (
                  <Stage7Body
                    statusCode={responseStatus}
                    responseBytes={responseBytes}
                    streamLines={responseStreamLines}
                    model={responseModel}
                    stopReason={responseStopReason}
                    outputTokens={responseOutputTokens}
                    formula={pipelineFormula}
                  />
                )}

                {/* ---- Raw Data Toggle (all stages) ---- */}
                {s && (
                  <>
                    <button
                      className={`raw-data-toggle${showRaw ? ' active' : ''}`}
                      onClick={(e) => { e.stopPropagation(); toggleRawData(stageNum) }}
                    >
                      {showRaw ? '▾ 隐藏原始数据' : '▸ 查看原始数据'}
                    </button>
                    {showRaw && (
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 10 }}>
                        <div className="raw-data-block">
                          <div className="raw-data-block-header">Input (原始输入)</div>
                          <div className="raw-data-block-body">{safeJsonString(s.input)}</div>
                        </div>
                        <div className="raw-data-block">
                          <div className="raw-data-block-header">Output (原始输出)</div>
                          <div className="raw-data-block-body">{safeJsonString(s.output)}</div>
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          </div>
        )
      })}

      {/* Replay Compare */}
      {replayTrace && trace && (
        <ReplayCompare originalTrace={trace} replayTrace={replayTrace} />
      )}

      {/* Copy Toast */}
      {copied && (
        <div className="copy-toast">已复制全链路数据到剪贴板</div>
      )}

      {/* Replay History */}
      {trace.replays && trace.replays.length > 0 && (
        <div style={{
          marginTop: 20,
          padding: '12px 16px',
          background: 'var(--color-bg-alt)',
          border: '1px solid var(--color-border)',
          borderRadius: 'var(--radius-card)',
          fontSize: '0.8125rem',
          color: 'var(--color-text-secondary)',
        }}>
          <strong>重放历史:</strong>
          {(trace.replays ?? []).map((replayId: string) => (
            <Link
              key={replayId}
              to={`/requests/${replayId}`}
              style={{
                color: 'var(--color-accent)',
                textDecoration: 'none',
                margin: '0 6px',
              }}
            >
              #{replayId}
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}

/* ═══════════════════════════════════════════════════════════════
   Stage Body Components
   ═══════════════════════════════════════════════════════════════ */

/* ---- Stage 1: Format Detector ---- */

interface Stage1BodyProps {
  trace: TraceRecord
  s: StageSnapshot | undefined
}

function Stage1Body({ trace, s }: Stage1BodyProps) {
  const input = (s?.input ?? {}) as Record<string, unknown>
  const output = (s?.output ?? {}) as Record<string, unknown>
  const path = (input.path as string) ?? trace.path
  const detected = (output.detected_format as string) ?? trace.source_format
  const model = (input.model as string) ?? ''

  return (
    <div style={{
      display: 'flex',
      gap: 12,
      alignItems: 'center',
      fontSize: '0.8125rem',
    }}>
      <span style={{ color: 'var(--color-text-secondary)' }}>path:</span>
      <span className="mono">{path}</span>
      <span style={{ color: 'var(--color-text-muted)' }}>&rarr;</span>
      <span style={{ color: 'var(--color-text-secondary)' }}>detected:</span>
      <span className="tag tag-accent">{detected}</span>
      {model && (
        <span className="mono" style={{
          fontSize: '0.75rem',
          color: 'var(--color-text-muted)',
          marginLeft: 8,
        }}>
          model: {model}
        </span>
      )}
    </div>
  )
}

/* ---- Stage 2: Image Check ---- */

interface Stage2BodyProps {
  s: StageSnapshot | undefined
}

function Stage2Body({ s }: Stage2BodyProps) {
  const output = (s?.output ?? {}) as Record<string, unknown>
  const contentBlocks = (output.content_blocks as number) ?? 0
  const textBlocks = (output.text_blocks as number) ?? 0
  const imageBlocks = (output.image_blocks as number) ?? 0
  const hasImages = (output.has_images as boolean) ?? false

  return (
    <div style={{ display: 'flex', gap: 24, fontSize: '0.8125rem' }}>
      <div>
        <span style={{ color: 'var(--color-text-secondary)' }}>
          Content Blocks:
        </span>{' '}
        <strong>{contentBlocks}</strong>
      </div>
      <div>
        <span style={{ color: 'var(--color-text-secondary)' }}>
          Text Blocks:
        </span>{' '}
        <strong>{textBlocks}</strong>
      </div>
      <div>
        <span style={{ color: 'var(--color-text-secondary)' }}>
          Image Blocks:
        </span>{' '}
        <strong style={{ color: 'var(--color-info)' }}>
          {imageBlocks}
        </strong>
      </div>
      <div>
        <span style={{ color: 'var(--color-text-secondary)' }}>
          Has Images:
        </span>{' '}
        <span className={`tag ${hasImages ? 'tag-info' : 'tag-default'}`}>
          {String(hasImages)}
        </span>
      </div>
    </div>
  )
}

/* ---- Stage 3: Decision Engine ---- */

interface Stage3BodyProps {
  systemPrompt: string
  constructedPrompt: string
  rawToolCallJson: string | undefined
  mode: string
  hashes: string[]
  focus: string
  reasoning: string
  attempt: number
  maxAttempts: number
  model: string
  endpoint: string
  maxTokens: number
  temperature: number
  hasImages: boolean
}

function Stage3Body(props: Stage3BodyProps) {
  const {
    systemPrompt,
    constructedPrompt,
    rawToolCallJson,
    mode,
    hashes,
    focus,
    reasoning,
    attempt,
    maxAttempts,
    model,
    endpoint,
    maxTokens,
    temperature,
    hasImages,
  } = props

  return (
    <>
      {/* Prompt sent + Constructed prompt */}
      <div className="decision-grid">
        <div className="decision-block">
          <div className="decision-block-title">
            &#x2699; System Prompt (发送给 DeepSeek)
          </div>
          <div className="decision-block-body">
            {systemPrompt || '(无 system prompt)'}
          </div>
        </div>
        <div className="decision-block">
          <div className="decision-block-title">
            &#x1F4CB; Constructed Prompt (构造后发送)
          </div>
          <div className="decision-block-body">
            {constructedPrompt || '(无 constructed prompt)'}
          </div>
        </div>
      </div>

      {/* Raw JSON + Parsed Result */}
      <div className="decision-grid" style={{ marginTop: 12 }}>
        <div className="decision-block">
          <div className="decision-block-title">
            &#x1F4E4; Raw Tool-Call JSON (DeepSeek 返回)
          </div>
          <div className="decision-block-body">
            {rawToolCallJson
              ? tryFormatJson(rawToolCallJson)
              : '(无 tool-call 输出)'}
          </div>
        </div>
        <div className="decision-block">
          <div className="decision-block-title">
            &#x2705; Parsed Decision Result
          </div>
          <div style={{
            padding: 14,
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
          }}>
            <div className="decision-output-card">
              {mode && (
                <div className="do-item">
                  <span className="do-label">mode</span>{' '}
                  <span className="tag tag-accent">{mode}</span>
                </div>
              )}
              {hashes.length > 0 && (
                <div className="do-item">
                  <span className="do-label">images</span>{' '}
                  <strong>{hashes.length}</strong> selected
                </div>
              )}
              <div className="do-item">
                <span className="do-label">status</span>{' '}
                <span className={`tag ${mode ? 'tag-success' : 'tag-warning'}`}>
                  {mode ? 'ok' : 'no match'}
                </span>
              </div>
            </div>
            {focus && (
              <div className="do-item" style={{ alignSelf: 'flex-start' }}>
                <span className="do-label">focus</span>
                <span style={{
                  fontSize: '0.8125rem',
                  color: 'var(--color-text)',
                  marginLeft: 6,
                }}>
                  "{focus}"
                </span>
              </div>
            )}
            {reasoning && (
              <div className="do-item" style={{ alignSelf: 'flex-start' }}>
                <span className="do-label">reasoning</span>
                <span style={{
                  fontSize: '0.75rem',
                  color: 'var(--color-text-secondary)',
                  marginLeft: 6,
                }}>
                  "{reasoning}"
                </span>
              </div>
            )}
            {hashes.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {hashes.map((h: string) => (
                  <span
                    key={h}
                    className="mono"
                    style={{
                      fontSize: '0.6875rem',
                      padding: '3px 8px',
                      background: 'var(--color-accent-soft)',
                      borderRadius: 3,
                      color: 'var(--color-accent)',
                    }}
                  >
                    {truncateHash(h)}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* API Info footer */}
      <div style={{
        marginTop: 10,
        fontSize: '0.75rem',
        color: 'var(--color-text-muted)',
        display: 'flex',
        gap: 16,
        flexWrap: 'wrap',
      }}>
        <span>Model: <span className="mono">{model}</span></span>
        {endpoint && (
          <span>API: <span className="mono">{endpoint}</span></span>
        )}
        <span>max_tokens: {maxTokens} · temperature: {temperature}</span>
        {attempt > 0 && (
          <span>
            attempt: {attempt}/{maxAttempts}
            {hasImages ? ' · thinking: enabled' : ' · thinking: disabled'}
          </span>
        )}
      </div>
    </>
  )
}

/* ---- Stage 4: Vision Client ---- */

interface Stage4BodyProps {
  images: Record<string, unknown>[]
  mode: string
  focus: string
  prompt: string
  model: string
  endpoint: string
  maxTokens: number
  parallel: boolean
  traceId: string
  reason: string
}

function Stage4Body({ images, mode, focus, prompt, model, endpoint, maxTokens, parallel, traceId, reason }: Stage4BodyProps) {
  if (!Array.isArray(images) || images.length === 0) {
    return (
      <div style={{ fontSize: '0.8125rem', color: 'var(--color-text-muted)' }}>
        (无图片需要识别)
      </div>
    )
  }

  return (
    <>
      {/* Vision input context */}
      <div style={{
        marginBottom: 14,
        padding: '12px 16px',
        background: 'var(--color-bg)',
        border: '1px solid var(--color-border)',
        borderRadius: 'var(--radius-card)',
        fontSize: '0.8125rem',
      }}>
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: prompt ? 10 : 0 }}>
          <div>
            <span style={{ color: 'var(--color-text-muted)' }}>模式: </span>
            <span className={`tag ${mode === 'skip' ? 'tag-default' : mode === 'replicate' ? 'tag-warning' : 'tag-accent'}`}>{mode}</span>
          </div>
          <div>
            <span style={{ color: 'var(--color-text-muted)' }}>图片数: </span>
            <strong>{images.length}</strong>
          </div>
          {focus && (
            <div>
              <span style={{ color: 'var(--color-text-muted)' }}>决策焦点: </span>
              <span style={{ fontStyle: 'italic' }}>"{focus}"</span>
            </div>
          )}
          {reason && (
            <div>
              <span style={{ color: 'var(--color-text-muted)' }}>原因: </span>
              <span className="tag tag-warning">{reason}</span>
            </div>
          )}
        </div>
        {prompt && (
          <div style={{
            marginTop: 10,
            padding: '10px 14px',
            background: 'var(--color-bg-alt)',
            border: '1px solid var(--color-border)',
            borderRadius: 'var(--radius-button)',
            fontSize: '0.75rem',
            fontFamily: 'var(--font-mono)',
            lineHeight: 1.6,
            color: 'var(--color-text-secondary)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            maxHeight: 200,
            overflowY: 'auto',
          }}>
            <div style={{ fontSize: '0.625rem', fontWeight: 600, textTransform: 'uppercase', color: 'var(--color-text-muted)', marginBottom: 6 }}>
              发送给视觉模型的完整提示词
            </div>
            {prompt}
          </div>
        )}
      </div>

      {images.map((img, idx) => (
        <VisionImageCard key={idx} image={img} index={idx} traceId={traceId} />
      ))}
      <div style={{
        marginTop: 10,
        fontSize: '0.75rem',
        color: 'var(--color-text-muted)',
        display: 'flex',
        gap: 16,
        flexWrap: 'wrap',
      }}>
        <span>Model: <span className="mono">{model}</span></span>
        {endpoint && (
          <span>Endpoint: <span className="mono">{endpoint}</span></span>
        )}
        <span>max_tokens: {maxTokens}</span>
        {parallel && (
          <span>并行: asyncio.gather x {images.length}</span>
        )}
      </div>
    </>
  )
}

/* ---- Stage 5: Request Rewriter ---- */

interface Stage5BodyProps {
  originalBody: Record<string, unknown>
  rewrittenBody: Record<string, unknown>
  imageBlockCount: number
}

function Stage5Body({ originalBody, rewrittenBody, imageBlockCount }: Stage5BodyProps) {
  return (
    <>
      <div style={{
        fontSize: '0.8125rem',
        color: 'var(--color-text-secondary)',
        marginBottom: 12,
      }}>
        将 <strong>{imageBlockCount} 个 image content blocks</strong> 替换为对应
        视觉识别描述文本 &rarr; 生成 rewritten_body 发送给目标模型
      </div>
      <div className="diff-panel">
        <div className="diff-block">
          <div className="diff-block-title">
            Original Body (with image blocks)
          </div>
          <div className="diff-block-body">
            {safeJsonString(originalBody)}
          </div>
        </div>
        <div className="diff-block">
          <div className="diff-block-title">
            Rewritten Body (image &rarr; text blocks)
          </div>
          <div className="diff-block-body">
            {safeJsonString(rewrittenBody)}
          </div>
        </div>
      </div>
    </>
  )
}

/* ---- Stage 6: Target Client ---- */

interface Stage6BodyProps {
  endpoint: string
  model: string
  stream: boolean
  timeout: number
  headers: Record<string, string>
  requestBody: Record<string, unknown>
  connectionMs: number
  ttfbMs: number
  streamingMs: number
  totalMs: number
  responsePreview: string
  streamLines: number
}

function Stage6Body(props: Stage6BodyProps) {
  const {
    endpoint,
    model,
    stream,
    timeout,
    headers,
    requestBody,
    connectionMs,
    ttfbMs,
    streamingMs,
    totalMs,
    responsePreview,
    streamLines,
  } = props

  const headerStr = Object.entries(headers)
    .map(([k, v]) => `${k}: ${maskSecret(k, v)}`)
    .join(' · ')

  return (
    <>
      {/* Info row */}
      <div style={{
        display: 'flex',
        gap: 16,
        flexWrap: 'wrap',
        marginBottom: 14,
        fontSize: '0.75rem',
        color: 'var(--color-text-muted)',
      }}>
        <span>Endpoint: <span className="mono">{endpoint}</span></span>
        <span>Model: <span className="mono">{model}</span></span>
        <span>
          Stream: <span className={`tag ${stream ? 'tag-info' : 'tag-default'}`}>
            {String(stream)}
          </span>
        </span>
        <span>Timeout: {timeout}s</span>
        {headerStr && (
          <span>Headers: <span className="mono">{headerStr}</span></span>
        )}
      </div>

      {/* Request + Timing */}
      <div className="diff-panel" style={{ marginBottom: 14 }}>
        <div className="diff-block">
          <div className="diff-block-title">
            Request Body (发送给 Target)
          </div>
          <div className="diff-block-body">
            {safeJsonString(requestBody)}
          </div>
        </div>
        <div className="diff-block">
          <div className="diff-block-title">Timing</div>
          <div style={{ padding: 14, fontSize: '0.8125rem' }}>
            <TimingRow label="Connection" value={connectionMs} />
            <TimingRow label="TTFB" value={ttfbMs} />
            <TimingRow label="Streaming" value={streamingMs} />
            <TimingRow label="Total" value={totalMs} last />
          </div>
        </div>
      </div>

      {/* Response Preview */}
      {responsePreview && (
        <div className="response-viewer">
          <div className="response-viewer-header">
            <span style={{ fontSize: '0.8125rem', fontWeight: 600 }}>
              Response Stream ({streamLines} lines)
            </span>
            <span style={{ fontSize: '0.75rem', color: 'var(--color-text-muted)' }}>
              SSE · anthropic format
            </span>
          </div>
          <div className="response-viewer-body">
            {truncatePreview(responsePreview)}
          </div>
        </div>
      )}
    </>
  )
}

function TimingRow({
  label,
  value,
  last = false,
}: {
  label: string
  value: number
  last?: boolean
}) {
  return (
    <div style={{
      display: 'flex',
      justifyContent: 'space-between',
      padding: '6px 0',
      borderBottom: last ? 'none' : '1px solid var(--color-border)',
    }}>
      <span>{label}</span>
      <span className="mono" style={last ? { fontWeight: 600 } : undefined}>
        {value > 0 ? fmtDuration(value) : '--'}
      </span>
    </div>
  )
}

/* ---- Stage 7: Response Handler ---- */

interface Stage7BodyProps {
  statusCode: number
  responseBytes: number
  streamLines: number
  model: string
  stopReason: string
  outputTokens: number
  formula: string
}

function Stage7Body(props: Stage7BodyProps) {
  const {
    statusCode,
    responseBytes,
    streamLines,
    model,
    stopReason,
    outputTokens,
    formula,
  } = props

  return (
    <>
      <div style={{ display: 'flex', gap: 24, fontSize: '0.8125rem', flexWrap: 'wrap' }}>
        <div>
          <span style={{ color: 'var(--color-text-secondary)' }}>Status:</span>{' '}
          <span className={`tag ${statusCode < 400 ? 'tag-success' : 'tag-danger'}`}>
            {statusCode}
          </span>
        </div>
        <div>
          <span style={{ color: 'var(--color-text-secondary)' }}>
            Response bytes:
          </span>{' '}
          <span className="mono">{fmtNum(responseBytes)}</span>
        </div>
        <div>
          <span style={{ color: 'var(--color-text-secondary)' }}>
            Stream lines:
          </span>{' '}
          <span className="mono">{fmtNum(streamLines)}</span>
        </div>
        <div>
          <span style={{ color: 'var(--color-text-secondary)' }}>
            Target model:
          </span>{' '}
          <span>{model}</span>
        </div>
        {stopReason && (
          <div>
            <span style={{ color: 'var(--color-text-secondary)' }}>
              Stop reason:
            </span>{' '}
            <span>{stopReason}</span>
          </div>
        )}
        <div>
          <span style={{ color: 'var(--color-text-secondary)' }}>
            Output tokens:
          </span>{' '}
          <span className="mono">{fmtNum(outputTokens)}</span>
        </div>
      </div>
      <div style={{
        marginTop: 10,
        fontSize: '0.75rem',
        color: 'var(--color-text-muted)',
      }}>
        Total pipeline: {formula}
      </div>
    </>
  )
}

/* ═══════════════════════════════════════════════════════════════
   Utility functions
   ═══════════════════════════════════════════════════════════════ */

function safeJsonString(obj: Record<string, unknown>): string {
  try {
    return JSON.stringify(obj, null, 2)
  } catch {
    return String(obj)
  }
}

function tryFormatJson(raw: string): string {
  try {
    const parsed = JSON.parse(raw)
    return JSON.stringify(parsed, null, 2)
  } catch {
    return raw
  }
}

function truncateHash(hash: string, len = 10): string {
  if (hash.length <= len + 4) return hash
  return hash.slice(0, len) + '...'
}

function truncatePreview(text: string, maxLines = 20): string {
  const lines = text.split('\n')
  if (lines.length <= maxLines) return text
  return lines.slice(0, maxLines).join('\n') + '\n...'
}

function maskSecret(key: string, value: string): string {
  const lower = key.toLowerCase()
  if (
    lower.includes('key') ||
    lower.includes('secret') ||
    lower.includes('token') ||
    lower.includes('auth')
  ) {
    if (value.length <= 4) return '****'
    return value.slice(0, 3) + '-****'
  }
  return value
}
