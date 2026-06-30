import { Outlet, useLocation } from 'react-router-dom'
import Sidebar from './Sidebar'
import TopBar from './TopBar'
import { useEffect } from 'react'
import { useTheme } from '../hooks/useTheme'

export default function Layout() {
  const location = useLocation()

  /* ensure theme is applied on initial load */
  useTheme()

  useEffect(() => {
    window.scrollTo(0, 0)
  }, [location.pathname])

  const pageTitle = getPageTitle(location.pathname)

  return (
    <div className="app-layout">
      <Sidebar currentPath={location.pathname} />
      <div className="main-content">
        <TopBar title={pageTitle} />
        <div className="page-body">
          <Outlet />
        </div>
      </div>
    </div>
  )
}

function getPageTitle(pathname: string): string {
  if (pathname.startsWith('/dashboard')) return '仪表盘'
  if (pathname.startsWith('/requests')) return '请求列表'
  if (pathname.startsWith('/decisions')) return '决策审计'
  if (pathname.startsWith('/cache')) return '图片缓存'
  return 'LensGate'
}
