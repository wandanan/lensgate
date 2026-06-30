import { useState } from 'react'
import type { ReactNode } from 'react'

interface StagePanelProps {
  stage: string
  stageNum: number
  status: 'ok' | 'error' | 'skipped'
  durationMs: number
  defaultOpen?: boolean
  children: ReactNode
  subtitle?: string
}

const stageNumClass: Record<number, string> = {
  1: 's1',
  2: 's2',
  3: 's3',
  4: 's4',
  5: 's5',
  6: 's6',
  7: 's7',
}

const statusTagClass: Record<string, string> = {
  ok: 'tag-success',
  error: 'tag-danger',
  skipped: 'tag-default',
}

const statusLabel: Record<string, string> = {
  ok: '✓ OK',
  error: '✗ Error',
  skipped: '→ Skipped',
}

export default function StagePanel({
  stage,
  stageNum,
  status,
  durationMs,
  defaultOpen = false,
  children,
  subtitle,
}: StagePanelProps) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <div className={`detail-stage${open ? ' open' : ''}`}>
      <div className="ds-header" onClick={() => setOpen(!open)}>
        <div className="ds-header-left">
          <div className={`ds-stage-num ${stageNumClass[stageNum] ?? 's1'}`}>
            {stageNum}
          </div>
          <div>
            <div className="ds-title">{stage}</div>
            {subtitle && <div className="ds-subtitle">{subtitle}</div>}
          </div>
        </div>
        <div className="ds-status">
          <span className={`tag ${statusTagClass[status] ?? 'tag-default'}`}>
            {statusLabel[status] ?? status}
          </span>
          <span className="ds-subtitle">{durationMs}ms</span>
          <svg className="ds-chevron" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M5 2l5 5-5 5" />
          </svg>
        </div>
      </div>
      <div className="ds-body">{children}</div>
    </div>
  )
}
