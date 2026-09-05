import { useQuery } from '@tanstack/react-query'
import { Activity, ArrowLeft, CircleAlert, RefreshCw } from 'lucide-react'
import { Link, NavLink, Route, Routes } from 'react-router-dom'
import './styles/shell.css'
import ChangesPage from './pages/ChangesPage'
import StockDetailsPage from './pages/StockDetailsPage'
import WatchlistPage from './pages/WatchlistPage'
import { getHealth } from './services/api'

function ConnectionStatus() {
  const healthQuery = useQuery({ queryKey: ['health'], queryFn: getHealth, retry: false })
  const statusLabel = healthQuery.isSuccess ? 'Market service connected' : healthQuery.isError ? 'Market service unavailable' : 'Connecting to market service'

  return (
    <div className={`connection ${healthQuery.isSuccess ? 'is-online' : healthQuery.isError ? 'is-offline' : ''}`} role="status" aria-live="polite" aria-label={statusLabel}>
      <span className="connection-dot" aria-hidden="true" />
      <span>{statusLabel}</span>
      {healthQuery.isError && <button className="icon-button" type="button" onClick={() => healthQuery.refetch()} aria-label="Retry market service connection" title="Retry connection"><RefreshCw size={15} aria-hidden="true" /></button>}
    </div>
  )
}

function NotFoundPage() {
  return <main className="workspace details-page"><section className="details-state"><CircleAlert size={30} aria-hidden="true" /><h1>Page not found</h1><p>The page you requested does not exist.</p><div><Link to="/watchlist"><ArrowLeft size={15} aria-hidden="true" />Back to watchlist</Link></div></section></main>
}

function App() {
  return <div className="app-shell">
    <header className="topbar">
      <NavLink className="brand" to="/" aria-label="Smart Market Watchlist home"><span className="brand-mark"><Activity size={20} aria-hidden="true" /></span><span><strong>Smart Market</strong><small>Watchlist</small></span></NavLink>
      <nav aria-label="Primary navigation"><NavLink to="/" end>Overview</NavLink><NavLink to="/watchlist">Watchlist</NavLink><NavLink to="/changes">What changed</NavLink></nav>
      <ConnectionStatus />
    </header>
    <Routes>
      <Route path="/" element={<WatchlistPage />} />
      <Route path="/watchlist" element={<WatchlistPage />} />
      <Route path="/changes" element={<ChangesPage />} />
      <Route path="/stocks/:symbol" element={<StockDetailsPage />} />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  </div>
}

export default App
