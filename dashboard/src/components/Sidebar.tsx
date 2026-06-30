import { NavLink } from 'react-router-dom'
import ThemeToggle from './ThemeToggle'

interface SidebarProps {
  currentPath: string
}

export default function Sidebar({ currentPath }: SidebarProps) {
  const links = [
    { to: '/dashboard', label: '仪表盘', icon: DashboardIcon },
    { to: '/requests', label: '请求列表', icon: ListIcon },
    { to: '/decisions', label: '决策审计', icon: ClockIcon },
    { to: '/cache', label: '图片缓存', icon: DatabaseIcon },
  ]

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">LensGate</div>
      <nav className="sidebar-nav">
        {links.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            end={link.to === '/'}
            className={({ isActive }) =>
              `sidebar-link${isActive || (link.to !== '/' && currentPath.startsWith(link.to)) ? ' active' : ''}`
            }
          >
            <svg className="sidebar-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
              {link.icon}
            </svg>
            {link.label}
          </NavLink>
        ))}
      </nav>
      <div className="sidebar-footer">
        <ThemeToggle />
      </div>
    </aside>
  )
}

/* SVG icon paths */
const DashboardIcon = (
  <>
    <rect x="1" y="1" width="6" height="6" rx="1" />
    <rect x="9" y="1" width="6" height="6" rx="1" />
    <rect x="1" y="9" width="6" height="6" rx="1" />
    <rect x="9" y="9" width="6" height="6" rx="1" />
  </>
)

const ListIcon = (
  <>
    <path d="M2 3h12M2 8h12M2 13h12" />
  </>
)

const ClockIcon = (
  <>
    <circle cx="8" cy="8" r="6" />
    <path d="M8 5v3l2 2" />
  </>
)

const DatabaseIcon = (
  <>
    <rect x="2" y="3" width="12" height="10" rx="1.5" />
    <path d="M6 8h4" />
  </>
)
