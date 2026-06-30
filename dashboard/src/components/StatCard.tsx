interface StatCardProps {
  value: string
  label: string
  sub?: string
}

export default function StatCard({ value, label, sub }: StatCardProps) {
  return (
    <div className="stat-card">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}
