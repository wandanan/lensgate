import type { ReactNode } from 'react'

interface FilterBarProps {
  children: ReactNode
}

export default function FilterBar({ children }: FilterBarProps) {
  return <div className="filter-bar">{children}</div>
}
