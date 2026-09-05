import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, Clock3, Plus, RefreshCw, Search, Trash2, TrendingUp } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { addToWatchlist, ApiError, getWatchlist, removeFromWatchlist, type WatchlistItem } from '../services/api'

const SUPPORTED_STOCKS = [
  { symbol: 'TCS', name: 'Tata Consultancy Services' },
  { symbol: 'INFY', name: 'Infosys' },
  { symbol: 'RELIANCE', name: 'Reliance Industries' },
  { symbol: 'HDFCBANK', name: 'HDFC Bank' },
]

function formatPrice(value: string | null) {
  if (value === null) return 'Price unavailable'
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 }).format(Number(value))
}

function formatVolume(value: string | null) {
  if (value === null) return 'Volume unavailable'
  return `${new Intl.NumberFormat('en-IN', { notation: 'compact', maximumFractionDigits: 1 }).format(Number(value))} volume`
}

function getFreshness(timestamp: string | null) {
  if (!timestamp) return { label: 'Unavailable', className: 'is-unavailable' }
  const ageMinutes = Math.max(0, (Date.now() - new Date(timestamp).getTime()) / 60_000)
  if (ageMinutes <= 5) return { label: 'Fresh', className: 'is-fresh' }
  if (ageMinutes <= 30) return { label: 'Delayed', className: 'is-delayed' }
  return { label: 'Stale', className: 'is-stale' }
}

function StockRow({ item, onRemove, removing }: { item: WatchlistItem; onRemove: () => void; removing: boolean }) {
  const freshness = getFreshness(item.last_price_at)

  return <article className="stock-row">
    <Link className="stock-main" to={`/stocks/${item.symbol}`} aria-label={`View ${item.symbol} details`}>
      <span className="stock-avatar">{item.symbol.slice(0, 2)}</span>
      <span className="stock-identity"><strong>{item.symbol}</strong><small>{item.name ?? 'Company name unavailable'}</small></span>
    </Link>
    <div className="stock-market"><strong>{formatPrice(item.last_price)}</strong><small>{formatVolume(item.last_volume)}</small></div>
    <span className={`freshness ${freshness.className}`}><Clock3 size={13} aria-hidden="true" />{freshness.label}</span>
    <div className="stock-actions">
      <Link className="row-link" to={`/stocks/${item.symbol}`} title={`View ${item.symbol} details`} aria-label={`View ${item.symbol} details`}><ArrowRight size={17} aria-hidden="true" /></Link>
      <button className="row-action" type="button" onClick={onRemove} disabled={removing} title={`Remove ${item.symbol}`} aria-label={`Remove ${item.symbol} from watchlist`}><Trash2 size={16} aria-hidden="true" /></button>
    </div>
  </article>
}

export default function WatchlistPage() {
  const queryClient = useQueryClient()
  const [selectedSymbol, setSelectedSymbol] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  const watchlistQuery = useQuery({ queryKey: ['watchlist'], queryFn: getWatchlist })
  const watchedSymbols = new Set(watchlistQuery.data?.map((item) => item.symbol) ?? [])
  const availableStocks = SUPPORTED_STOCKS.filter((stock) => !watchedSymbols.has(stock.symbol))

  const addMutation = useMutation({
    mutationFn: addToWatchlist,
    onSuccess: (response) => {
      setSelectedSymbol('')
      setNotice(response.message)
      queryClient.invalidateQueries({ queryKey: ['watchlist'] })
    },
  })
  const removeMutation = useMutation({
    mutationFn: removeFromWatchlist,
    onSuccess: (response) => {
      setNotice(response.message)
      queryClient.invalidateQueries({ queryKey: ['watchlist'] })
    },
  })
  const mutationError = addMutation.error ?? removeMutation.error

  return <main className="workspace watchlist-page">
    <section className="page-heading" aria-labelledby="watchlist-title">
      <div><p className="eyebrow">Market overview</p><h1 id="watchlist-title">Your watchlist</h1><p className="page-summary">A calm view of the companies you follow. Meaningful changes will rise above everyday market noise.</p></div>
      <div className="watchlist-count"><TrendingUp size={18} aria-hidden="true" /><span><strong>{watchlistQuery.data?.length ?? 0}</strong> stocks monitored</span></div>
    </section>

    <section className="watchlist-panel" aria-labelledby="stocks-heading">
      <div className="watchlist-toolbar">
        <div><p className="eyebrow">Portfolio signals</p><h2 id="stocks-heading">Tracked stocks</h2></div>
        <form className="stock-picker" onSubmit={(event) => { event.preventDefault(); if (selectedSymbol) addMutation.mutate(selectedSymbol) }}>
          <Search size={16} aria-hidden="true" />
          <label className="sr-only" htmlFor="stock-select">Choose a stock</label>
          <select id="stock-select" value={selectedSymbol} onChange={(event) => { setSelectedSymbol(event.target.value); setNotice(null); addMutation.reset() }} disabled={availableStocks.length === 0 || addMutation.isPending}>
            <option value="">{availableStocks.length ? 'Add a stock' : 'All stocks added'}</option>
            {availableStocks.map((stock) => <option key={stock.symbol} value={stock.symbol}>{stock.symbol} · {stock.name}</option>)}
          </select>
          <button type="submit" disabled={!selectedSymbol || addMutation.isPending} aria-label="Add selected stock"><Plus size={17} aria-hidden="true" /><span>Add</span></button>
        </form>
      </div>

      {(notice || mutationError) && <div className={`inline-notice ${mutationError ? 'is-error' : ''}`} role="status">{mutationError instanceof ApiError ? mutationError.message : notice}</div>}

      {watchlistQuery.isPending && <div className="stock-list" aria-label="Loading watchlist">{[1, 2, 3, 4].map((item) => <div className="stock-skeleton" key={item} />)}</div>}
      {watchlistQuery.isError && <div className="watchlist-state"><RefreshCw size={25} aria-hidden="true" /><h3>Unable to load your watchlist</h3><p>{watchlistQuery.error instanceof ApiError ? watchlistQuery.error.message : 'Try again in a moment.'}</p><button type="button" onClick={() => watchlistQuery.refetch()}>Try again</button></div>}
      {watchlistQuery.isSuccess && watchlistQuery.data.length === 0 && <div className="watchlist-state"><Search size={25} aria-hidden="true" /><h3>Your watchlist is empty</h3><p>Choose one of the supported stocks above to start monitoring it.</p></div>}
      {watchlistQuery.isSuccess && watchlistQuery.data.length > 0 && <div className="stock-list">{watchlistQuery.data.map((item) => <StockRow key={item.symbol} item={item} onRemove={() => { setNotice(null); removeMutation.mutate(item.symbol) }} removing={removeMutation.isPending && removeMutation.variables === item.symbol} />)}</div>}
    </section>
  </main>
}