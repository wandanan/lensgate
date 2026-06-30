import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { DecisionRecord } from '../api/types'
import FilterBar from '../components/FilterBar'
import DataTable from '../components/DataTable'
import type { Column } from '../components/DataTable'
import Pagination from '../components/Pagination'

const PAGE_SIZE = 20

export default function DecisionsPage() {
  const navigate = useNavigate()
  const [items, setItems] = useState<DecisionRecord[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(true)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const params: Record<string, string> = { page: String(page), size: String(PAGE_SIZE) }
      if (status) params.status = status
      const res = await api.getDecisions(params)
      setItems(res.items)
      setTotal(res.total)
    } catch (err) {
      console.error('Failed to fetch decisions:', err)
      setItems([])
      setTotal(0)
    } finally {
      setLoading(false)
    }
  }, [page, status])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const columns: Column[] = [
    {
      key: 'timestamp',
      label: '时间',
      mono: true,
      render: (val) => {
        const s = String(val ?? '')
        const timePart = s.includes('T') ? s.split('T')[1] : s.includes(' ') ? s.split(' ')[1] : s
        return timePart ? timePart.split('.')[0] : s
      },
    },
    {
      key: 'trace_id',
      label: '请求',
      mono: true,
      render: (val) => {
        const id = String(val ?? '')
        return id.length > 8 ? `#${id.slice(0, 8)}` : `#${id}`
      },
    },
    {
      key: 'user_messages',
      label: '用户消息',
      render: (val) => {
        const arr = val as string[] | undefined
        if (!arr || arr.length === 0) return '—'
        const text = arr.join('; ')
        return text.length > 50 ? `${text.slice(0, 50)}…` : text
      },
    },
    {
      key: 'mode',
      label: 'mode',
      render: (val) => {
        const m = String(val ?? '')
        if (!m) return '—'
        let cls = 'tag-info'
        if (m === 'compare') cls = 'tag-accent'
        else if (m === 'replicate') cls = 'tag-accent'
        return <span className={`tag ${cls}`}>{m}</span>
      },
    },
    {
      key: 'hashes',
      label: 'hashes',
      render: (val) => String((Array.isArray(val) ? val.length : 0)),
    },
    { key: 'attempt', label: 'attempt' },
    {
      key: 'status',
      label: '状态',
      render: (val) => {
        const s = String(val ?? '')
        if (!s) return '—'
        const cls = s === 'ok' ? 'tag-success' : 'tag-danger'
        return <span className={`tag ${cls}`}>{s}</span>
      },
    },
  ]

  return (
    <div>
      <p style={{ fontSize: '0.75rem', color: 'var(--color-text-muted)', marginBottom: '16px' }}>
        valuation.jsonl · 共 {total} 条
      </p>
      <FilterBar>
        <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1) }}>
          <option value="">全部状态</option>
          <option value="ok">ok</option>
          <option value="failed">failed</option>
        </select>
      </FilterBar>
      <DataTable
        columns={columns}
        data={items as unknown as Record<string, unknown>[]}
        loading={loading}
        onRowClick={(row) => navigate(`/requests/${row.trace_id}`)}
      />
      <Pagination
        page={page}
        totalPages={totalPages}
        total={total}
        onPageChange={setPage}
      />
    </div>
  )
}
