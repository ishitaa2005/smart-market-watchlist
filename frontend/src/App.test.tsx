import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const STOCK = {
  symbol: 'TCS',
  name: 'Tata Consultancy Services',
  last_price: '3800.00',
  last_price_at: new Date().toISOString(),
  last_volume: '1200000',
  added_at: new Date().toISOString(),
}

const STOCK_DETAILS = {
  symbol: 'TCS',
  name: 'Tata Consultancy Services',
  last_price: 3800,
  last_price_at: new Date().toISOString(),
  last_volume: 1200000,
  week52_high: 4560,
  week52_low: 3040,
  data_status: 'fresh',
  attention_score: 72.5,
  direction: 'UP',
  confidence: 'high',
  explanation: 'TCS moved significantly above its recent baseline.',
  reasons: [{ code: 'PRICE_ANOMALY', message: 'Price movement was 3.2x normal volatility.', value: 3.2 }],
}

const CHANGE = {
  event_id: 21,
  symbol: 'TCS',
  score: 86.4,
  direction: 'UP',
  price: 3925.5,
  occurred_at: new Date().toISOString(),
  reasons: [{ code: 'PRICE_ANOMALY', message: 'Price movement was significantly larger than its recent baseline.', value: 4.1 }],
  explanation: 'Price movement was significantly larger than its recent baseline.',
  confidence: 'high',
  data_status: 'fresh',
  status: 'active',
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function mockApi(initialWatchlist = [STOCK], details: unknown = STOCK_DETAILS, detailsStatus = 200, initialChanges: typeof CHANGE[] = []) {
  let watchlist = initialWatchlist
  let changes = initialChanges
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    if (url.endsWith('/health')) return jsonResponse({ status: 'ok' })
    if (url.endsWith('/stocks/TCS')) return jsonResponse(details, detailsStatus)
    if (url.endsWith('/watchlist/changes')) return jsonResponse(changes)
    if (url.endsWith('/demo/scenarios/price_shock/TCS') && init?.method === 'POST') {
      changes = [CHANGE, ...changes]
      return jsonResponse({
        scenario: 'price_shock', symbol: 'TCS', success: true, attention_score: 86.4,
        direction: 'UP', explanation: CHANGE.explanation, data_status: 'fresh', event_created: true, error: null,
      })
    }
    if (url.endsWith('/watchlist/TCS/ack') && init?.method === 'POST') {
      changes = changes.filter(change => change.symbol !== 'TCS')
      return jsonResponse({ symbol: 'TCS', acknowledged: true, last_seen_event_id: 21, message: "'TCS' marked as seen" })
    }
    if (url.endsWith('/watchlist') && (!init?.method || init.method === 'GET')) return jsonResponse(watchlist)
    if (url.endsWith('/watchlist/TCS') && init?.method === 'POST') {
      watchlist = [STOCK]
      return jsonResponse({ symbol: 'TCS', added: true, message: "'TCS' added to watchlist" }, 201)
    }
    if (url.endsWith('/watchlist/TCS') && init?.method === 'DELETE') {
      watchlist = []
      return jsonResponse({ symbol: 'TCS', removed: true, message: "'TCS' removed from watchlist" })
    }
    return jsonResponse({ detail: 'Not found' }, 404)
  })
}

function renderApp(initialEntry = '/') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('App foundation', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('shows the connected watchlist and market data', async () => {
    mockApi()

    renderApp()

    expect(screen.getByRole('heading', { name: 'Your watchlist' })).toBeInTheDocument()
    expect(await screen.findByText('Market service connected')).toBeInTheDocument()
    expect(await screen.findByText('Tata Consultancy Services')).toBeInTheDocument()
    expect(screen.getByText('₹3,800.00')).toBeInTheDocument()
  })

  it('shows a retry control when the API is unavailable', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Network error'))

    renderApp()

    expect(await screen.findByText('Market service unavailable')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry market service connection' })).toBeInTheDocument()
  })

  it('shows a useful empty state', async () => {
    mockApi([])

    renderApp()

    expect(await screen.findByRole('heading', { name: 'Your watchlist is empty' })).toBeInTheDocument()
  })

  it('removes a stock through the API and refreshes the list', async () => {
    const user = userEvent.setup()
    const fetchMock = mockApi()

    renderApp()
    await user.click(await screen.findByRole('button', { name: 'Remove TCS from watchlist' }))

    expect(await screen.findByText("'TCS' removed from watchlist")).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Your watchlist is empty' })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/watchlist/TCS',
      expect.objectContaining({ method: 'DELETE' }),
    )
  })

  it('adds a selected stock and refreshes the list', async () => {
    const user = userEvent.setup()
    const fetchMock = mockApi([])

    renderApp()
    await user.selectOptions(await screen.findByRole('combobox', { name: 'Choose a stock' }), 'TCS')
    await user.click(screen.getByRole('button', { name: 'Add selected stock' }))

    expect(await screen.findByText("'TCS' added to watchlist")).toBeInTheDocument()
    expect(await screen.findByText('Tata Consultancy Services')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/watchlist/TCS',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('renders backend-provided stock attention details and reasons', async () => {
    mockApi()

    renderApp('/stocks/TCS')

    expect(await screen.findByRole('heading', { name: 'Tata Consultancy Services' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'High attention' })).toBeInTheDocument()
    expect(screen.getByText('TCS moved significantly above its recent baseline.')).toBeInTheDocument()
    expect(screen.getByText('Price movement was 3.2x normal volatility.')).toBeInTheDocument()
    expect(screen.getByText('₹4,560.00')).toBeInTheDocument()
  })

  it('clearly warns when stock data is stale', async () => {
    mockApi([STOCK], { ...STOCK_DETAILS, data_status: 'stale' })

    renderApp('/stocks/TCS')

    expect(await screen.findByRole('alert')).toHaveTextContent('Market data may be stale')
  })

  it('shows a useful state when the stock does not exist', async () => {
    mockApi([STOCK], { detail: "Instrument 'TCS' does not exist" }, 404)

    renderApp('/stocks/TCS')

    expect(await screen.findByRole('heading', { name: 'Stock not found' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to watchlist' })).toHaveAttribute('href', '/watchlist')
  })

  it('explains unseen events with importance, direction, and time context', async () => {
    mockApi([STOCK], STOCK_DETAILS, 200, [CHANGE])

    renderApp('/changes')

    expect(await screen.findByRole('heading', { name: 'TCS moved significantly higher.' })).toBeInTheDocument()
    expect(screen.getByText('High attention')).toBeInTheDocument()
    expect(screen.getByText('Positive')).toBeInTheDocument()
    expect(screen.getByText('Price movement was significantly larger than its recent baseline.')).toBeInTheDocument()
    expect(screen.getByText(/Since your previous check ·/)).toBeInTheDocument()
  })

  it('acknowledges every unseen event for one symbol without hiding another symbol', async () => {
    const user = userEvent.setup()
    const fetchMock = mockApi([STOCK], STOCK_DETAILS, 200, [
      CHANGE,
      { ...CHANGE, event_id: 20, score: 74 },
      { ...CHANGE, event_id: 19, symbol: 'INFY', direction: 'DOWN', score: 55 },
    ])

    renderApp('/changes')
    await user.click(await screen.findByRole('button', { name: 'Mark all 2 TCS updates seen' }))

    expect(await screen.findByRole('heading', { name: 'INFY moved significantly lower.' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'TCS moved significantly higher.' })).not.toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/watchlist/TCS/ack',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('shows an all-caught-up state when there are no unseen events', async () => {
    mockApi()

    renderApp('/changes')

    expect(await screen.findByRole('heading', { name: 'You’re all caught up' })).toBeInTheDocument()
  })

  it('runs a deterministic scenario and refreshes the meaningful change feed', async () => {
    const user = userEvent.setup()
    const fetchMock = mockApi()

    renderApp('/changes')
    await user.click(await screen.findByRole('button', { name: 'Run scenario' }))

    expect(await screen.findByText('TCS: 86/100 attention')).toBeInTheDocument()
    expect(screen.getByText(/A meaningful event was created/)).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'TCS moved significantly higher.' })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/demo/scenarios/price_shock/TCS',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('shows a real not-found page for unknown application routes', () => {
    mockApi()

    renderApp('/not-a-real-page')

    expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to watchlist' })).toHaveAttribute('href', '/watchlist')
  })
})