import type { ReactNode } from 'react'

interface TopBarProps {
  title: string
  children?: ReactNode
}

export default function TopBar({ title, children }: TopBarProps) {
  return (
    <header className="topbar">
      <div className="topbar-left">
        <span className="topbar-title">{title}</span>
      </div>
      {children && <div className="topbar-actions">{children}</div>}
    </header>
  )
}
