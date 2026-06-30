import type { ReactNode } from 'react'

export interface Column {
  key: string
  label: string
  render?: (value: unknown, row: Record<string, unknown>) => ReactNode
  mono?: boolean
}

interface DataTableProps {
  columns: Column[]
  data: Record<string, unknown>[]
  onRowClick?: (row: Record<string, unknown>) => void
  loading?: boolean
}

export default function DataTable({ columns, data, onRowClick, loading }: DataTableProps) {
  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: '32px 0', color: 'var(--color-text-muted)' }}>
        加载中...
      </div>
    )
  }

  if (data.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '32px 0', color: 'var(--color-text-muted)' }}>
        暂无数据
      </div>
    )
  }

  return (
    <table className="data-table">
      <thead>
        <tr>
          {columns.map((col) => (
            <th key={col.key}>{col.label}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {data.map((row, i) => (
          <tr
            key={(row.id ?? row.timestamp ?? i) as string}
            className={onRowClick ? 'clickable' : undefined}
            onClick={() => onRowClick?.(row)}
          >
            {columns.map((col) => {
              const raw = row[col.key]
              const cellContent = col.render ? col.render(raw, row) : String(raw ?? '')
              return (
                <td key={col.key} className={col.mono ? 'mono' : undefined}>
                  {cellContent}
                </td>
              )
            })}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
