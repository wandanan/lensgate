interface PaginationProps {
  page: number
  totalPages: number
  total: number
  onPageChange: (page: number) => void
}

export default function Pagination({ page, totalPages, total, onPageChange }: PaginationProps) {
  if (totalPages <= 1) return null

  const pages: number[] = []
  for (let i = 1; i <= totalPages; i++) {
    pages.push(i)
  }

  return (
    <div className="pagination">
      <span>
        第 {page} / {totalPages} 页，共 {total} 条
      </span>
      <div className="pagination-pages">
        {pages.map((p) => (
          <span
            key={p}
            className={p === page ? 'active' : undefined}
            onClick={() => p !== page && onPageChange(p)}
            style={p !== page ? { cursor: 'pointer' } : undefined}
          >
            {p}
          </span>
        ))}
      </div>
    </div>
  )
}
