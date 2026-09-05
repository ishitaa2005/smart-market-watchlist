import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ArrowDownRight, ArrowRight, ArrowUpRight, Beaker, BellRing, Check, Clock3, Play, RefreshCw, Sparkles } from 'lucide-react'
import { Link } from 'react-router-dom'
import { acknowledgeChanges, ApiError, getChanges, runDemoScenario, type DemoScenarioName, type SignificanceEvent } from '../services/api'

const DEMO_SCENARIOS: { value: DemoScenarioName; label: string }[] = [
  { value: 'normal', label: 'Normal movement' },
  { value: 'price_shock', label: 'Price shock' },
  { value: 'volume_anomaly', label: 'Volume anomaly' },
  { value: 'relative_performance', label: 'Relative performance' },
  { value: 'stale_data', label: 'Stale market data' },
]

const DEMO_SYMBOLS = ['TCS', 'INFY', 'RELIANCE', 'HDFCBANK']
const DEMO_MODE_ENABLED = import.meta.env.VITE_DEMO_MODE !== 'false'

function formatPrice(value: number | null) {
  if (value === null) return 'Price unavailable'
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', minimumFractionDigits: 2 }).format(value)
}

function formatTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Time unavailable'

  const elapsedMinutes = Math.max(0, Math.floor((Date.now() - date.getTime()) / 60_000))
  if (elapsedMinutes < 1) return 'Just now'
  if (elapsedMinutes < 60) return `${elapsedMinutes}m ago`
  if (elapsedMinutes < 1_440) return `${Math.floor(elapsedMinutes / 60)}h ago`
  return new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' }).format(date)
}

function attentionLevel(score: number | null) {
  if (score === null) return { label: 'Unrated', className: 'is-low' }
  if (score >= 70) return { label: 'High attention', className: 'is-high' }
  if (score >= 40) return { label: 'Medium attention', className: 'is-medium' }
  return { label: 'Low attention', className: 'is-low' }
}

function directionMeta(direction: SignificanceEvent['direction']) {
  if (direction === 'UP') return { label: 'Positive', happened: 'moved significantly higher', className: 'is-up', Icon: ArrowUpRight }
  if (direction === 'DOWN') return { label: 'Negative', happened: 'moved significantly lower', className: 'is-down', Icon: ArrowDownRight }
  return { label: 'Neutral', happened: 'showed unusual market activity', className: 'is-neutral', Icon: ArrowRight }
}

interface ChangeCardProps {
  event: SignificanceEvent
  symbolEventCount: number
  showAcknowledge: boolean
  isAcknowledging: boolean
  onAcknowledge: (symbol: string) => void
}

function ChangeCard({ event, symbolEventCount, showAcknowledge, isAcknowledging, onAcknowledge }: ChangeCardProps) {
  const attention = attentionLevel(event.score)
  const direction = directionMeta(event.direction)
  const DirectionIcon = direction.Icon

  return <article className={`change-card ${attention.className}`}>
    <div className="change-importance">
      <span>{attention.label}</span>
      <strong>{event.score === null ? '—' : Math.round(event.score)}</strong>
    </div>
    <div className="change-body">
      <div className="change-meta">
        <span className={`change-direction ${direction.className}`}><DirectionIcon size={14} aria-hidden="true" />{direction.label}</span>
        {event.status === 'closed' && <span className="resolved-label"><Check size={13} aria-hidden="true" />Resolved</span>}
        <span><Clock3 size={13} aria-hidden="true" />Since your previous check · {formatTime(event.occurred_at)}</span>
      </div>
      <h2><Link to={`/stocks/${event.symbol}`}>{event.symbol} {direction.happened}.</Link></h2>
      <p>{event.explanation ?? 'The attention engine detected a meaningful change against this stock’s recent baseline.'}</p>
      <div className="change-facts">
        <span><small>Latest event price</small><strong>{formatPrice(event.price)}</strong></span>
        <span><small>Confidence</small><strong>{event.confidence ?? 'Unavailable'}</strong></span>
        <span><small>Data status</small><strong className={`status-text is-${event.data_status ?? 'unavailable'}`}>{event.data_status ?? 'Unavailable'}</strong></span>
      </div>
    </div>
    {showAcknowledge && <button className="ack-button" type="button" disabled={isAcknowledging} onClick={() => onAcknowledge(event.symbol)}>
      <Check size={15} aria-hidden="true" />
      {isAcknowledging ? 'Marking seen…' : `Mark all ${symbolEventCount > 1 ? `${symbolEventCount} ` : ''}${event.symbol} updates seen`}
    </button>}
  </article>
}

export default function ChangesPage() {
  const queryClient = useQueryClient()
  const [demoSymbol, setDemoSymbol] = useState('TCS')
  const [demoScenario, setDemoScenario] = useState<DemoScenarioName>('price_shock')
  const changesQuery = useQuery({ queryKey: ['changes'], queryFn: getChanges, retry: false })
  const ackMutation = useMutation({
    mutationFn: acknowledgeChanges,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['changes'] }),
  })
  const demoMutation = useMutation({
    mutationFn: () => runDemoScenario(demoScenario, demoSymbol),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['changes'] })
      queryClient.invalidateQueries({ queryKey: ['watchlist'] })
      queryClient.invalidateQueries({ queryKey: ['stock', demoSymbol] })
    },
  })
  const changes = changesQuery.data ?? []
  const counts = changes.reduce<Record<string, number>>((result, event) => {
    result[event.symbol] = (result[event.symbol] ?? 0) + 1
    return result
  }, {})
  const firstEventBySymbol = changes.reduce<Record<string, number>>((result, event) => {
    result[event.symbol] ??= event.event_id
    return result
  }, {})

  return <main className="workspace changes-page">
    <section className="page-heading" aria-labelledby="changes-title">
      <div><p className="eyebrow">Since your previous check</p><h1 id="changes-title">What changed?</h1><p className="page-summary">Only meaningful watchlist events appear here, with the reason and importance already worked out.</p></div>
      {!changesQuery.isLoading && !changesQuery.isError && <div className="changes-count"><BellRing size={17} aria-hidden="true" /><strong>{changes.length}</strong><span>unseen {changes.length === 1 ? 'event' : 'events'}</span></div>}
    </section>

    {DEMO_MODE_ENABLED && <section className="demo-controls" aria-labelledby="demo-controls-title">
      <div className="demo-controls-heading"><Beaker size={18} aria-hidden="true" /><div><p className="eyebrow">Hackathon demo</p><h2 id="demo-controls-title">Trigger a deterministic market scenario</h2></div></div>
      <div className="demo-form">
        <label><span>Stock</span><select aria-label="Demo stock" value={demoSymbol} disabled={demoMutation.isPending} onChange={event => setDemoSymbol(event.target.value)}>{DEMO_SYMBOLS.map(symbol => <option key={symbol} value={symbol}>{symbol}</option>)}</select></label>
        <label><span>Scenario</span><select aria-label="Demo scenario" value={demoScenario} disabled={demoMutation.isPending} onChange={event => setDemoScenario(event.target.value as DemoScenarioName)}>{DEMO_SCENARIOS.map(scenario => <option key={scenario.value} value={scenario.value}>{scenario.label}</option>)}</select></label>
        <button type="button" disabled={demoMutation.isPending} onClick={() => demoMutation.mutate()}><Play size={15} aria-hidden="true" />{demoMutation.isPending ? 'Running…' : 'Run scenario'}</button>
      </div>
      {demoMutation.isSuccess && <div className="demo-result" role="status"><strong>{demoMutation.data.symbol}: {demoMutation.data.attention_score === null ? 'Unrated' : `${Math.round(demoMutation.data.attention_score)}/100 attention`}</strong><span>{demoMutation.data.event_created ? 'A meaningful event was created.' : 'No event was created.'} {demoMutation.data.explanation}</span></div>}
      {demoMutation.isError && <div className="demo-result is-error" role="alert">{demoMutation.error instanceof ApiError ? demoMutation.error.message : 'The demo scenario could not run.'}</div>}
    </section>}

    {ackMutation.isError && <div className="changes-notice is-error" role="alert">{ackMutation.error instanceof ApiError ? ackMutation.error.message : 'Unable to mark these updates as seen.'}</div>}

    {changesQuery.isLoading ? <section className="change-feed" aria-label="Loading changes">{[0, 1, 2].map(item => <div className="change-skeleton" key={item} />)}</section>
      : changesQuery.isError ? <section className="changes-state"><RefreshCw size={27} aria-hidden="true" /><h2>Unable to load changes</h2><p>{changesQuery.error instanceof ApiError ? changesQuery.error.message : 'The change feed could not be loaded.'}</p><button type="button" onClick={() => changesQuery.refetch()}><RefreshCw size={15} aria-hidden="true" />Try again</button></section>
        : changes.length === 0 ? <section className="changes-state"><Sparkles size={29} aria-hidden="true" /><h2>You’re all caught up</h2><p>No meaningful watchlist changes have appeared since your previous check.</p><Link to="/watchlist">View watchlist <ArrowRight size={15} aria-hidden="true" /></Link></section>
          : <section className="change-feed" aria-label="Unseen meaningful changes">{changes.map(event => <ChangeCard key={event.event_id} event={event} symbolEventCount={counts[event.symbol]} showAcknowledge={firstEventBySymbol[event.symbol] === event.event_id} isAcknowledging={ackMutation.isPending && ackMutation.variables === event.symbol} onAcknowledge={ackMutation.mutate} />)}</section>}
  </main>
}