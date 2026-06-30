import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import ErrorBoundary from './components/ErrorBoundary'
import CoverPage from './pages/CoverPage'
import DashboardHome from './pages/DashboardHome'
import RequestsPage from './pages/RequestsPage'
import RequestDetailPage from './pages/RequestDetailPage'
import DecisionsPage from './pages/DecisionsPage'
import CachePage from './pages/CachePage'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<CoverPage />} />
      <Route element={<Layout />}>
        <Route path="dashboard" element={<ErrorBoundary><DashboardHome /></ErrorBoundary>} />
        <Route path="requests" element={<ErrorBoundary><RequestsPage /></ErrorBoundary>} />
        <Route path="requests/:id" element={<ErrorBoundary><RequestDetailPage /></ErrorBoundary>} />
        <Route path="decisions" element={<ErrorBoundary><DecisionsPage /></ErrorBoundary>} />
        <Route path="cache" element={<ErrorBoundary><CachePage /></ErrorBoundary>} />
      </Route>
    </Routes>
  )
}
