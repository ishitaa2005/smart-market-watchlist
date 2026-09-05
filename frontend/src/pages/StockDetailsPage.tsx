import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ArrowDownRight, ArrowLeft, ArrowUpRight, BarChart3, Clock3, Gauge, Minus, RefreshCw, ShieldCheck } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { ApiError, getStockDetails } from '../services/api'

function formatPrice(value: number | null) {
  if (value === null) return 'Unavailable'
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 }).format(value)
}

function formatVolume(value: number | null) {
  if (value === null) return 'Unavailable'
  return new Intl.NumberFormat('en-IN', { notation: 'compact', maximumFractionDigits: 1 }).format(value)
}

function attentionLevel(score: number | null) {
  if (score === null) return 'Not available'
  if (score >= 60) return 'High attention'
  if (score >= 40) return 'Medium attention'
  return 'Low attention'
}

function directionMeta(direction: string | null) {
  if (direction === 'UP') return { label: 'Upward', className: 'is-up', icon: ArrowUpRight }
  if (direction === 'DOWN') return { label: 'Downward', className: 'is-down', icon: ArrowDownRight }
  return { label: 'Neutral', className: 'is-neutral', icon: Minus }
}

function RangeMeter({ low, high, current }: { low: number | null; high: number | null; current: number | null }) {
  const valid = low !== null && high !== null && current !== null && high > low
  const position = valid ? Math.min(100, Math.max(0, ((current - low) / (high - low)) * 100)) : 0

  return <div className="range-meter">
    <div className="range-track" aria-label={valid ? `Current price is ${Math.round(position)} percent through its 52-week range` : '52-week range unavailable'}>
      {valid && <span className="range-position" style={{ left: `${position}%` }} />}
    </div>
    <div className="range-labels"><span><small>52W low</small><strong>{formatPrice(low)}</strong></span><span><small>52W high</small><strong>{formatPrice(high)}</strong></span></div>
  </div>
}

export default function StockDetailsPage() {
  const { symbol = '' } = useParams()
  const normalizedSymbol = symbol.trim().toUpperCase()
  const detailsQuery = useQuery({
    queryKey: ['stock', normalizedSymbol],
    queryFn: () => getStockDetails(normalizedSymbol),
    enabled: Boolean(normalizedSymbol),
    retry: false,
  })

  if (detailsQuery.isPending) return <main className="workspace details-page"><div className="details-skeleton" aria-label="Loading stock details" /></main>

  if (detailsQuery.isError) {
    const notFound = detailsQuery.error instanceof ApiError && detailsQuery.error.status === 404
    return <main className="workspace details-page"><div className="details-state"><AlertTriangle size={28} aria-hidden="true" /><h1>{notFound ? 'Stock not found' : 'Unable to load stock details'}</h1><p>{detailsQuery.error instanceof ApiError ? detailsQuery.error.message : 'Try again in a moment.'}</p><div><Link to="/watchlist"><ArrowLeft size={16} aria-hidden="true" />Back to watchlist</Link>{!notFound && <button type="button" onClick={() => detailsQuery.refetch()}><RefreshCw size={15} aria-hidden="true" />Try again</button>}</div></div></main>
  }

  const stock = detailsQuery.data
  const direction = directionMeta(stock.direction)
  const DirectionIcon = direction.icon
  const stale = stock.data_status === 'stale' || stock.data_status === 'unavailable'

  return <main className="workspace details-page">
    <Link className="back-link" to="/watchlist"><ArrowLeft size={16} aria-hidden="true" />Back to watchlist</Link>

    {stale && <div className="stale-banner" role="alert"><AlertTriangle size={17} aria-hidden="true" /><span><strong>Market data may be stale.</strong> Use this information with care until a fresh update arrives.</span></div>}

    <section className="stock-hero" aria-labelledby="stock-title">
      <div className="stock-title-group"><span className="detail-avatar">{stock.symbol.slice(0, 2)}</span><div><p className="eyebrow">NSE · Equity</p><h1 id="stock-title">{stock.name ?? stock.symbol}</h1><span>{stock.symbol}</span></div></div>
      <div className="hero-price"><small>Latest known price</small><strong>{formatPrice(stock.last_price)}</strong><span className={`direction ${direction.className}`}><DirectionIcon size={15} aria-hidden="true" />{direction.label}</span></div>
    </section>

    <section className="details-grid">
      <article className="attention-summary">
        <div className="detail-card-heading"><span><Gauge size={19} aria-hidden="true" /></span><div><p className="eyebrow">Attention intelligence</p><h2>{attentionLevel(stock.attention_score)}</h2></div><strong className="attention-score">{stock.attention_score === null ? '—' : Math.round(stock.attention_score)}</strong></div>
        <div className="score-track" aria-label={`Attention score ${stock.attention_score ?? 'unavailable'} out of 100`}><span style={{ width: `${stock.attention_score ?? 0}%` }} /></div>
        <p className="explanation">{stock.explanation ?? 'There is not enough market history to explain this stock yet.'}</p>
        <div className="analysis-meta"><span><ShieldCheck size={15} aria-hidden="true" />{stock.confidence ? `${stock.confidence} confidence` : 'Confidence unavailable'}</span><span><Clock3 size={15} aria-hidden="true" />{stock.data_status} data</span></div>
      </article>

      <article className="market-card">
        <div className="detail-card-heading"><span><BarChart3 size={19} aria-hidden="true" /></span><div><p className="eyebrow">Market position</p><h2>52-week range</h2></div></div>
        <RangeMeter low={stock.week52_low} high={stock.week52_high} current={stock.last_price} />
        <div className="market-stats"><span><small>Latest volume</small><strong>{formatVolume(stock.last_volume)}</strong></span><span><small>Data status</small><strong className={`status-text is-${stock.data_status}`}>{stock.data_status}</strong></span></div>
      </article>
    </section>

    <section className="reasons-panel" aria-labelledby="reasons-title">
      <div><p className="eyebrow">Why the system cares</p><h2 id="reasons-title">Signal breakdown</h2></div>
      <div className="reason-list">{stock.reasons?.length ? stock.reasons.map((reason) => <article key={`${reason.code}-${reason.message}`}><span>{reason.code.replaceAll('_', ' ')}</span><p>{reason.message}</p>{reason.value !== null && <strong>{reason.value.toFixed(2)}</strong>}</article>) : <p className="no-reasons">No meaningful signal reasons are available.</p>}</div>
    </section>
  </main>
}