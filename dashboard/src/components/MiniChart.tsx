interface MiniChartProps {
  data: number[]
  color?: string
}

export default function MiniChart({ data, color = 'var(--color-accent)' }: MiniChartProps) {
  const max = Math.max(...data, 1)

  return (
    <div className="mini-chart">
      {data.map((val, i) => (
        <div
          key={i}
          style={{
            flex: 1,
            height: `${Math.max((val / max) * 100, 2)}%`,
            background: color,
            borderRadius: '2px 2px 0 0',
            minWidth: '4px',
            transition: 'height var(--transition-fast)',
          }}
        />
      ))}
    </div>
  )
}
