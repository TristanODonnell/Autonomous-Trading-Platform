import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ComposedChart, Area, Line, XAxis, YAxis,
  Tooltip, ResponsiveContainer,
} from 'recharts'
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table'
import MetricCard from '../components/shared/MetricCard'
import StatusBadge from '../components/shared/StatusBadge'
import { cn } from '../lib/utils'
import {
  fetchPortfolioSummary,
  fetchEquityCurve,
  fetchPortfolioHoldings,
  fetchPortfolioAllocation,
  fetchPortfolioRisk,
  fetchPortfolioPerformance,
  type ApiEquityCurvePeriod,
  type ApiPortfolioHolding,
} from '../services/portfolioService'
import { fetchAllStrategies } from '../services/strategiesService'

// ── Helpers ───────────────────────────────────────────────────────────────────

const MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']

function fmtMonth(d: string) {
  return MONTH_NAMES[parseInt(d.split('-')[1]) - 1]
}
function fmtPrice(n: number) {
  return `$${n.toFixed(2)}`
}
function fmtDollar(n: number) {
  return `$${Math.abs(n).toLocaleString()}`
}

function computeMonthTicks(points: { date: string }[]): string[] {
  const seen = new Set<string>()
  const ticks: string[] = []
  for (const p of points) {
    const month = p.date.slice(0, 7)
    if (!seen.has(month)) { seen.add(month); ticks.push(p.date) }
  }
  return ticks
}

// ── Period helpers ────────────────────────────────────────────────────────────

type Period = '1W' | '1M' | '3M' | '1Y'
const PERIODS: Period[] = ['1W', '1M', '3M', '1Y']
const PERIOD_TO_API: Record<Period, ApiEquityCurvePeriod> = {
  '1W': '1w', '1M': '1m', '3M': '3m',
  // TODO: backend has no 1-year period — 'ytd' is the closest available option
  '1Y': 'ytd',
}

// ── Allocation color palette ──────────────────────────────────────────────────

const ALLOC_PALETTE = [
  'var(--accent)',
  'var(--blue)',
  'var(--purple)',
  'var(--yellow)',
  'var(--red)',
  'var(--text3)',
]

// ── Card shell helpers ────────────────────────────────────────────────────────

function Card({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div
      className={cn('rounded-lg p-5', className)}
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      {children}
    </div>
  )
}

function CardTitle({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="font-mono text-[11px] font-medium text-[var(--text2)] uppercase tracking-[0.08em] mb-4 pb-3"
      style={{ borderBottom: '1px solid var(--border)' }}
    >
      {children}
    </div>
  )
}

function Row({
  label,
  value,
  valueColor,
  last = false,
}: {
  label: React.ReactNode
  value: React.ReactNode
  valueColor?: string
  last?: boolean
}) {
  return (
    <div
      className="flex items-center justify-between py-2.5"
      style={{ borderBottom: last ? 'none' : '1px solid var(--border)' }}
    >
      <span className="text-[13px] text-[var(--text)]">{label}</span>
      <span className="font-mono text-[13px] text-right" style={{ color: valueColor ?? 'var(--text)' }}>
        {value}
      </span>
    </div>
  )
}

function CardSkeleton() {
  return (
    <Card>
      <div className="animate-pulse space-y-3">
        <div className="h-3 rounded w-1/3" style={{ background: 'var(--border2)' }} />
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-3 rounded" style={{ background: 'var(--border2)' }} />
        ))}
      </div>
    </Card>
  )
}

function CardError({ message }: { message: string }) {
  return (
    <Card>
      <span className="font-mono text-[11px]" style={{ color: 'var(--red)' }}>
        Failed to load: {message}
      </span>
    </Card>
  )
}

// ── Performance chart ─────────────────────────────────────────────────────────

type ChartView = 'equity' | 'drawdown'

function CustomTooltip({ active, payload }: { active?: boolean; payload?: { dataKey: string; value: number }[] }) {
  if (!active || !payload?.length) return null
  const eq = payload.find(p => p.dataKey === 'equity')
  const dd = payload.find(p => p.dataKey === 'drawdown')
  return (
    <div
      className="font-mono text-[11px] px-2 py-1.5 rounded flex flex-col gap-1"
      style={{ background: 'var(--surface2)', border: '1px solid var(--border)', color: 'var(--text)' }}
    >
      {eq && <span style={{ color: 'var(--accent)' }}>{fmtDollar(eq.value)}</span>}
      {dd && <span style={{ color: 'var(--red)' }}>{dd.value.toFixed(1)}%</span>}
    </div>
  )
}

function PerformanceChart() {
  const [period, setPeriod] = useState<Period>('3M')
  const [visible, setVisible] = useState<Set<ChartView>>(new Set(['equity', 'drawdown']))

  const apiPeriod = PERIOD_TO_API[period]

  const { data, isLoading, error } = useQuery({
    queryKey: ['portfolio', 'equity-curve', apiPeriod],
    queryFn: () => fetchEquityCurve(apiPeriod),
  })

  const chartData = useMemo(() => {
    if (!data) return []
    const ddMap = new Map(
      data.drawdown_points.map(p => [p.timestamp.split('T')[0], Number(p.value)])
    )
    return data.points.map(p => {
      const date = p.timestamp.split('T')[0]
      return { date, equity: Number(p.value), drawdown: ddMap.get(date) ?? 0 }
    })
  }, [data])

  const xTicks = computeMonthTicks(chartData)
  const showEq = visible.has('equity')
  const showDd = visible.has('drawdown')

  function toggleView(v: ChartView) {
    setVisible(prev => {
      const next = new Set(prev)
      next.has(v) ? next.delete(v) : next.add(v)
      return next.size === 0 ? prev : next
    })
  }

  return (
    <Card>
      <div className="flex items-center justify-between mb-4">
        <span className="font-mono text-[11px] font-medium text-[var(--text2)] uppercase tracking-[0.08em]">
          Performance
        </span>
        <div className="flex gap-2">
          {/* Period buttons */}
          {PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={cn(
                'inline-flex items-center px-2 py-0.5 rounded-[3px] font-mono text-[9px] font-medium uppercase tracking-[0.08em] cursor-pointer transition-colors',
                p === period
                  ? 'bg-[var(--accent-dim2)] text-[var(--accent)] border border-[rgba(0,229,160,0.3)]'
                  : 'bg-[rgba(139,148,158,0.1)] text-[var(--text2)] border border-[rgba(139,148,158,0.2)]'
              )}
            >
              {p}
            </button>
          ))}
          {/* Separator */}
          <span className="w-px self-stretch" style={{ background: 'var(--border)' }} />
          {/* Series visibility buttons */}
          {(['equity', 'drawdown'] as ChartView[]).map((v) => {
            const active = visible.has(v)
            return (
              <button
                key={v}
                onClick={() => toggleView(v)}
                className={cn(
                  'inline-flex items-center px-2 py-0.5 rounded-[3px] font-mono text-[9px] font-medium uppercase tracking-[0.08em] cursor-pointer transition-colors',
                  active
                    ? 'bg-[var(--accent-dim2)] text-[var(--accent)] border border-[rgba(0,229,160,0.3)]'
                    : 'bg-[rgba(139,148,158,0.1)] text-[var(--text2)] border border-[rgba(139,148,158,0.2)]'
                )}
              >
                {v}
              </button>
            )
          })}
        </div>
      </div>

      {isLoading ? (
        <div className="h-[140px] rounded-md animate-pulse" style={{ background: 'var(--surface2)' }} />
      ) : error ? (
        <div
          className="h-[140px] rounded-md flex items-center justify-center"
          style={{ background: 'var(--surface2)', border: '1px solid var(--border)' }}
        >
          <span className="font-mono text-[11px]" style={{ color: 'var(--red)' }}>Failed to load chart</span>
        </div>
      ) : chartData.length === 0 ? (
        <div
          className="h-[140px] rounded-md flex items-center justify-center"
          style={{ background: 'var(--surface2)', border: '1px solid var(--border)' }}
        >
          <span className="font-mono text-[11px] text-[var(--text3)]">No data for period</span>
        </div>
      ) : (
        <div
          className="rounded-md overflow-hidden"
          style={{ background: 'var(--surface2)', border: '1px solid var(--border)' }}
        >
          <ResponsiveContainer width="100%" height={120}>
            <ComposedChart data={chartData} margin={{ top: 8, right: 0, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="pfEqGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#00E5A0" stopOpacity={0.15} />
                  <stop offset="100%" stopColor="#00E5A0" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis
                dataKey="date"
                ticks={xTicks}
                tickFormatter={fmtMonth}
                axisLine={false}
                tickLine={false}
                interval={0}
                tick={{ fill: '#4A5568', fontFamily: 'IBM Plex Mono', fontSize: 8 }}
                height={20}
              />
              <YAxis yAxisId="eq" hide domain={['auto', 'auto']} />
              <YAxis yAxisId="dd" hide domain={[-100, 0]} orientation="right" />
              <Tooltip content={<CustomTooltip />} cursor={{ stroke: 'var(--border2)' }} />
              {showEq && (
                <Area
                  yAxisId="eq"
                  type="monotone"
                  dataKey="equity"
                  stroke="#00E5A0"
                  strokeWidth={1.5}
                  fill="url(#pfEqGrad)"
                  dot={false}
                  activeDot={{ r: 3, fill: '#00E5A0', strokeWidth: 0 }}
                />
              )}
              {showDd && (
                <Line
                  yAxisId="dd"
                  type="monotone"
                  dataKey="drawdown"
                  stroke="#FF4D6D"
                  strokeWidth={1}
                  dot={false}
                  opacity={0.8}
                  activeDot={{ r: 2, fill: '#FF4D6D', strokeWidth: 0 }}
                />
              )}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </Card>
  )
}

// ── Holdings table ────────────────────────────────────────────────────────────

interface EnrichedHolding extends ApiPortfolioHolding {
  strategy_name: string
  unrealized_pnl: number
}

const colHelper = createColumnHelper<EnrichedHolding>()

const HOLDING_COLS = [
  colHelper.accessor('symbol', {
    header: 'Symbol',
    cell: ({ getValue }) => <strong className="text-[13px] text-[var(--text)]">{getValue()}</strong>,
  }),
  colHelper.accessor('quantity', {
    header: 'Qty',
    cell: ({ getValue }) => <span className="font-mono text-[12px]">{Number(getValue()).toLocaleString()}</span>,
  }),
  colHelper.accessor('average_entry_price', {
    header: 'Avg Price',
    cell: ({ getValue }) => <span className="font-mono text-[12px]">{fmtPrice(Number(getValue()))}</span>,
  }),
  colHelper.accessor('current_price', {
    header: 'Current',
    cell: ({ getValue }) => <span className="font-mono text-[12px]">{fmtPrice(Number(getValue()))}</span>,
  }),
  colHelper.accessor('market_value', {
    header: 'Value',
    cell: ({ getValue }) => <span className="font-mono text-[12px]">{fmtDollar(Number(getValue()))}</span>,
  }),
  colHelper.accessor('unrealized_pnl', {
    // Computed as market_value - (average_entry_price × quantity). Represents total
    // unrealized gain/loss since entry, not just today's move.
    header: 'PnL',
    cell: ({ getValue }) => {
      const v = getValue()
      return (
        <span className="font-mono text-[12px]" style={{ color: v >= 0 ? 'var(--accent)' : 'var(--red)' }}>
          {v >= 0 ? '+' : '-'}{fmtDollar(v)}
        </span>
      )
    },
  }),
  colHelper.accessor('strategy_name', {
    header: 'Strategy',
    cell: ({ getValue }) => <StatusBadge variant="gray">{getValue()}</StatusBadge>,
  }),
]

function HoldingsTable() {
  const { data: rawHoldings = [], isLoading: holdingsLoading, error: holdingsError } = useQuery({
    queryKey: ['portfolio', 'holdings'],
    queryFn: fetchPortfolioHoldings,
  })

  const { data: strategies = [] } = useQuery({
    queryKey: ['strategies', 'all'],
    queryFn: fetchAllStrategies,
  })

  const nameMap = useMemo(
    () => new Map(strategies.map(s => [s.strategy_id, s.display_name])),
    [strategies]
  )

  const holdings: EnrichedHolding[] = useMemo(
    () => rawHoldings.map(h => ({
      ...h,
      strategy_name: nameMap.get(h.strategy_id) ?? h.strategy_id,
      unrealized_pnl: Number(h.market_value) - Number(h.average_entry_price) * Number(h.quantity),
    })),
    [rawHoldings, nameMap]
  )

  const table = useReactTable({
    data: holdings,
    columns: HOLDING_COLS,
    getCoreRowModel: getCoreRowModel(),
  })

  if (holdingsLoading) return <CardSkeleton />
  if (holdingsError) return <CardError message={(holdingsError as Error).message} />

  return (
    <Card>
      <CardTitle>Holdings</CardTitle>
      {holdings.length === 0 ? (
        <p className="font-mono text-[12px] text-[var(--text3)] text-center py-4">No open positions</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              {table.getHeaderGroups().map(hg => (
                <tr key={hg.id}>
                  {hg.headers.map(h => (
                    <th
                      key={h.id}
                      className="font-mono text-[9px] font-medium text-[var(--text3)] uppercase tracking-[0.1em] text-left pb-2.5 pr-3"
                      style={{ borderBottom: '1px solid var(--border)' }}
                    >
                      {flexRender(h.column.columnDef.header, h.getContext())}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.map((row, i, arr) => (
                <tr key={row.id}>
                  {row.getVisibleCells().map(cell => (
                    <td
                      key={cell.id}
                      className="py-2.5 pr-3 align-middle"
                      style={{ borderBottom: i < arr.length - 1 ? '1px solid var(--border)' : 'none' }}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

// ── Allocation by strategy ────────────────────────────────────────────────────

function AllocationByStrategy() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['portfolio', 'allocation'],
    queryFn: fetchPortfolioAllocation,
  })

  if (isLoading) return <CardSkeleton />
  if (error) return <CardError message={(error as Error).message} />

  const items = data?.by_strategy ?? []

  return (
    <div
      className="rounded-lg p-4"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      <CardTitle>Allocation by Strategy</CardTitle>
      {items.length === 0 ? (
        <p className="font-mono text-[12px] text-[var(--text3)] text-center py-4">No allocations</p>
      ) : (
        items.map(({ name, percent_of_portfolio }, i) => {
          const pct = Number(percent_of_portfolio)
          const color = ALLOC_PALETTE[i % ALLOC_PALETTE.length]
          return (
            <div
              key={name}
              className="flex items-center justify-between py-2.5"
              style={{ borderBottom: i < items.length - 1 ? '1px solid var(--border)' : 'none' }}
            >
              <div className="flex-1 mr-4 min-w-0">
                <div className="text-[13px] text-[var(--text)] mb-1.5">{name}</div>
                <div className="h-[5px] rounded-[3px] overflow-hidden" style={{ background: 'var(--border2)' }}>
                  <div
                    className="h-full rounded-[3px] transition-all duration-300"
                    style={{ width: `${pct}%`, background: color }}
                  />
                </div>
              </div>
              <span className="font-mono text-[13px] text-[var(--text)] shrink-0">
                {pct.toFixed(1)}%
              </span>
            </div>
          )
        })
      )}
    </div>
  )
}

// ── Risk metrics ──────────────────────────────────────────────────────────────

function RiskMetricsCard() {
  const { data: risk, isLoading: riskLoading, error: riskError } = useQuery({
    queryKey: ['portfolio', 'risk'],
    queryFn: fetchPortfolioRisk,
  })
  const { data: perf, isLoading: perfLoading, error: perfError } = useQuery({
    queryKey: ['portfolio', 'performance'],
    queryFn: fetchPortfolioPerformance,
  })

  const isLoading = riskLoading || perfLoading
  const error = riskError ?? perfError

  if (isLoading) return <CardSkeleton />
  if (error) return <CardError message={(error as Error).message} />
  if (!risk || !perf) return null

  const rows: { label: string; value: string; color?: string }[] = [
    { label: 'Sharpe Ratio',      value: Number(perf.sharpe_ratio).toFixed(2),                  color: 'var(--accent)' },
    { label: 'Sortino Ratio',     value: Number(perf.sortino_ratio).toFixed(2),                 color: 'var(--accent)' },
    { label: 'Max Drawdown',      value: `${Number(perf.max_drawdown).toFixed(1)}%`,            color: 'var(--yellow)' },
    { label: 'Volatility (ann.)', value: `${Number(perf.volatility).toFixed(1)}%` },
    { label: 'Beta (vs SPY)',     value: Number(risk.beta).toFixed(2) },
    { label: 'VaR 95% (1d)',      value: `$${Number(risk.value_at_risk_1d_95).toLocaleString()}` },
  ]

  return (
    <div
      className="rounded-lg p-4"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      <CardTitle>Risk Metrics</CardTitle>
      {rows.map(({ label, value, color }, i) => (
        <Row key={label} label={label} value={value} valueColor={color} last={i === rows.length - 1} />
      ))}
    </div>
  )
}

// ── Sector exposure (deferred) ────────────────────────────────────────────────

function SectorExposureCard() {
  // TODO: No backend endpoint for sector exposure.
  // GET /api/v1/portfolio/allocation exposes by_strategy and by_asset (individual symbols),
  // but no sector-level grouping exists.
  // Backend dependency: add GET /api/v1/portfolio/sector-exposure returning
  // [{ sector: string; percent_of_portfolio: number; warning: boolean }]
  // before this card can be wired.
  return (
    <div
      className="rounded-lg p-4"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      <CardTitle>Sector Exposure</CardTitle>
      <div
        className="rounded-md px-4 py-6 text-center"
        style={{ background: 'var(--surface2)', border: '1px solid var(--border)' }}
      >
        <p className="font-mono text-[11px] text-[var(--text3)]">
          Sector exposure data not yet available.
        </p>
        <p className="font-mono text-[10px] text-[var(--text3)] mt-1">
          Awaiting GET /portfolio/sector-exposure endpoint.
        </p>
      </div>
    </div>
  )
}

// ── Portfolio ─────────────────────────────────────────────────────────────────

export default function Portfolio() {
  const { data: summary, isLoading: summaryLoading } = useQuery({
    queryKey: ['portfolio', 'summary'],
    queryFn: fetchPortfolioSummary,
  })

  const { data: holdings = [] } = useQuery({
    queryKey: ['portfolio', 'holdings'],
    queryFn: fetchPortfolioHoldings,
  })

  const totalValue    = Number(summary?.current_portfolio_value ?? 0)
  const cash          = Number(summary?.cash_balance ?? 0)
  const dayPnl        = Number(summary?.todays_pnl_amount ?? 0)
  const invested      = totalValue - cash          // derived: total minus cash
  const openPositions = holdings.length
  const strategyCount = new Set(holdings.map(h => h.strategy_id)).size

  const pctDeployed  = totalValue > 0 ? ((invested / totalValue) * 100).toFixed(1) : '0.0'
  const pctAvailable = totalValue > 0 ? ((cash / totalValue) * 100).toFixed(1) : '0.0'

  return (
    <div className="p-6">
      {/* Top row — 4 metric cards */}
      <div className="grid grid-cols-4 gap-4 mb-4">
        <MetricCard
          label="Total Value"
          value={
            summaryLoading
              ? <span className="text-[36px] text-[var(--text3)]">—</span>
              : <span className="text-[36px]">${totalValue.toLocaleString()}</span>
          }
          change={
            summaryLoading ? '—' : (
              <span style={{ color: dayPnl >= 0 ? 'var(--accent)' : 'var(--red)' }}>
                {dayPnl >= 0 ? '▲' : '▼'} {dayPnl >= 0 ? '+' : '-'}${Math.abs(dayPnl).toLocaleString()} today
              </span>
            )
          }
        />
        <MetricCard
          label="Invested Capital"
          value={
            summaryLoading
              ? <span className="text-[28px] text-[var(--text3)]">—</span>
              : <span className="text-[28px]">${invested.toLocaleString()}</span>
          }
          change={summaryLoading ? '—' : `${pctDeployed}% deployed`}
        />
        <MetricCard
          label="Cash Reserve"
          value={
            summaryLoading
              ? <span className="text-[28px] text-[var(--text3)]">—</span>
              : <span className="text-[28px]">${cash.toLocaleString()}</span>
          }
          change={summaryLoading ? '—' : `${pctAvailable}% available`}
        />
        <MetricCard
          label="Open Positions"
          value={<span className="text-[28px]">{openPositions}</span>}
          change={`across ${strategyCount} ${strategyCount === 1 ? 'strategy' : 'strategies'}`}
        />
      </div>

      {/* Main grid — 2:1 */}
      <div className="grid gap-4" style={{ gridTemplateColumns: '2fr 1fr' }}>
        {/* Left column */}
        <div className="flex flex-col gap-4">
          <PerformanceChart />
          <HoldingsTable />
        </div>

        {/* Right column */}
        <div className="flex flex-col gap-4">
          <AllocationByStrategy />
          <RiskMetricsCard />
          <SectorExposureCard />
        </div>
      </div>
    </div>
  )
}
