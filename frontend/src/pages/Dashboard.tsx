import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { formatDistanceToNow, parseISO } from 'date-fns'
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
} from 'recharts'
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table'
import MetricCard from '../components/shared/MetricCard'
import StatusBadge from '../components/shared/StatusBadge'
import type { BadgeVariant } from '../components/shared/StatusBadge'
import { cn } from '../lib/utils'
import {
  fetchPortfolioSummary,
  fetchEquityCurve,
  fetchPortfolioRisk,
  fetchPortfolioPerformance,
  type ApiEquityCurvePeriod,
  type ApiEquityCurvePoint,
} from '../services/portfolioService'
import { fetchActiveStrategies, type ApiActiveStrategy } from '../services/strategiesService'
import { fetchSystemHealth, type ApiSystemHealth } from '../services/systemService'
import { fetchRecentActivity, type ApiActivityItem } from '../services/activityService'

// ── Local helpers ─────────────────────────────────────────────────────────────

const MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']

function fmtMonth(dateStr: string) {
  return MONTH_NAMES[parseInt(dateStr.split('-')[1]) - 1]
}

function fmtDollar(n: number) {
  return `$${Math.abs(n).toLocaleString()}`
}

function fmtRelTime(iso: string): string {
  try {
    return formatDistanceToNow(parseISO(iso), { addSuffix: true })
  } catch {
    return iso
  }
}

// ── Equity chart period mapping ───────────────────────────────────────────────

type Period = '1W' | '1M' | '3M' | '1Y'
const PERIODS: Period[] = ['1W', '1M', '3M', '1Y']

const PERIOD_TO_API: Record<Period, ApiEquityCurvePeriod> = {
  '1W': '1w',
  '1M': '1m',
  '3M': '3m',
  // TODO: Backend has no 1-year period — 'ytd' (year-to-date) is the closest available option.
  // Add a '1y' literal to PortfolioEquityCurvePeriod schema and service once the backend supports it.
  '1Y': 'ytd',
}

function transformEquityPoints(points: ApiEquityCurvePoint[]): { date: string; value: number }[] {
  return points.map((p) => ({
    date: p.timestamp.split('T')[0],
    value: Number(p.value),
  }))
}

function computeMonthTicks(points: { date: string; value: number }[]): string[] {
  const seen = new Set<string>()
  const ticks: string[] = []
  for (const p of points) {
    const month = p.date.slice(0, 7)
    if (!seen.has(month)) {
      seen.add(month)
      ticks.push(p.date)
    }
  }
  return ticks
}

// ── Badge helpers ─────────────────────────────────────────────────────────────

function activeStatusBadgeVariant(status: 'live' | 'paper' | 'off'): BadgeVariant {
  if (status === 'live') return 'green'
  if (status === 'paper') return 'blue'
  return 'gray'
}

function systemStatusBadgeVariant(status: ApiSystemHealth['status']): BadgeVariant {
  if (status === 'healthy') return 'green'
  if (status === 'degraded') return 'yellow'
  return 'red'
}

// ── Activity severity → dot color ─────────────────────────────────────────────

const SEVERITY_DOT_COLOR: Record<ApiActivityItem['severity'], string> = {
  info:     'var(--text3)',
  warning:  'var(--yellow)',
  error:    'var(--red)',
  critical: 'var(--red)',
}

// ── Shared UI primitives ──────────────────────────────────────────────────────

function ToggleDisplay({ on }: { on: boolean }) {
  return (
    <div
      className="relative w-9 h-5 rounded-[10px] shrink-0 transition-colors duration-200"
      style={{ background: on ? 'var(--accent)' : 'var(--border2)' }}
    >
      <div
        className="absolute top-[3px] w-3.5 h-3.5 rounded-full bg-white transition-all duration-200"
        style={{ left: on ? '19px' : '3px' }}
      />
    </div>
  )
}

function CardSkeleton() {
  return (
    <div
      className="rounded-lg p-5 animate-pulse"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      <div className="h-3 rounded w-1/3 mb-4" style={{ background: 'var(--border2)' }} />
      {[1, 2, 3].map((i) => (
        <div key={i} className="h-3 rounded mb-2" style={{ background: 'var(--border2)' }} />
      ))}
    </div>
  )
}

function CardError({ message }: { message: string }) {
  return (
    <div
      className="rounded-lg p-5"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      <span className="font-mono text-[11px]" style={{ color: 'var(--red)' }}>
        Failed to load: {message}
      </span>
    </div>
  )
}

// ── Equity chart ──────────────────────────────────────────────────────────────

function CustomTooltip({ active, payload }: { active?: boolean; payload?: { value: number }[] }) {
  if (!active || !payload?.length) return null
  return (
    <div
      className="font-mono text-[11px] px-2 py-1 rounded"
      style={{ background: 'var(--surface2)', border: '1px solid var(--border)', color: 'var(--text)' }}
    >
      {fmtDollar(payload[0].value)}
    </div>
  )
}

function EquityChart() {
  const [period, setPeriod] = useState<Period>('3M')
  const apiPeriod = PERIOD_TO_API[period]

  const { data, isLoading, error } = useQuery({
    queryKey: ['portfolio', 'equity-curve', apiPeriod],
    queryFn: () => fetchEquityCurve(apiPeriod),
  })

  const chartData = data ? transformEquityPoints(data.points) : []
  const xTicks = computeMonthTicks(chartData)

  return (
    <div
      className="rounded-lg p-5"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      <div className="flex items-center justify-between mb-4">
        <span className="font-mono text-[11px] font-medium text-[var(--text2)] uppercase tracking-[0.08em]">
          Equity Curve
        </span>
        <div className="flex gap-2">
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
        </div>
      </div>

      {isLoading ? (
        <div
          className="h-[150px] rounded-md animate-pulse"
          style={{ background: 'var(--surface2)' }}
        />
      ) : error ? (
        <div
          className="h-[150px] rounded-md flex items-center justify-center"
          style={{ background: 'var(--surface2)', border: '1px solid var(--border)' }}
        >
          <span className="font-mono text-[11px]" style={{ color: 'var(--red)' }}>
            Failed to load chart
          </span>
        </div>
      ) : chartData.length === 0 ? (
        <div
          className="h-[150px] rounded-md flex items-center justify-center"
          style={{ background: 'var(--surface2)', border: '1px solid var(--border)' }}
        >
          <span className="font-mono text-[11px] text-[var(--text3)]">No data for period</span>
        </div>
      ) : (
        <div
          className="rounded-md overflow-hidden"
          style={{ background: 'var(--surface2)', border: '1px solid var(--border)' }}
        >
          <ResponsiveContainer width="100%" height={130}>
            <AreaChart data={chartData} margin={{ top: 8, right: 0, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#00E5A0" stopOpacity={0.2} />
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
              <YAxis hide domain={['auto', 'auto']} />
              <Tooltip content={<CustomTooltip />} cursor={{ stroke: 'var(--border2)' }} />
              <Area
                type="monotone"
                dataKey="value"
                stroke="#00E5A0"
                strokeWidth={1.5}
                fill="url(#eqGrad)"
                dot={false}
                activeDot={{ r: 3, fill: '#00E5A0', strokeWidth: 0 }}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}

// ── Strategies table ──────────────────────────────────────────────────────────

const colHelper = createColumnHelper<ApiActiveStrategy>()

const STRAT_COLUMNS = [
  colHelper.accessor('display_name', {
    header: 'Strategy',
    cell: ({ row }) => (
      <div>
        <div className="text-[13px] font-medium text-[var(--text)]">{row.original.display_name}</div>
        <div className="font-mono text-[10px] text-[var(--text3)]">{row.original.strategy_id}</div>
      </div>
    ),
  }),
  colHelper.accessor('status', {
    header: 'Mode',
    cell: ({ getValue }) => {
      const status = getValue()
      const labels: Record<typeof status, string> = { live: 'Live', paper: 'Paper', off: 'Off' }
      return (
        <StatusBadge variant={activeStatusBadgeVariant(status)}>
          {labels[status]}
        </StatusBadge>
      )
    },
  }),
  colHelper.display({
    id: 'sharpe',
    // TODO: sharpe_ratio is not available in GET /api/v1/strategies/active.
    // Backend dependency: extend ActiveStrategyResponse with sharpe_ratio from strategy metrics,
    // or add a separate /strategies/active/metrics endpoint.
    header: 'Sharpe',
    cell: () => <span className="font-mono text-[13px] text-[var(--text3)]">—</span>,
  }),
  colHelper.accessor('todays_return', {
    // NOTE: 30-day PnL is not available in GET /api/v1/strategies/active.
    // Showing today's return as the closest available substitute.
    // TODO: Wire to pnl_30d once ActiveStrategyResponse exposes it.
    header: 'Rtn (Today)',
    cell: ({ getValue }) => {
      const v = Number(getValue())
      return (
        <span
          className="font-mono text-[13px]"
          style={{ color: v >= 0 ? 'var(--accent)' : 'var(--red)' }}
        >
          {v >= 0 ? '+' : '-'}{fmtDollar(v)}
        </span>
      )
    },
  }),
  colHelper.accessor('allocated_capital', {
    header: 'Allocation',
    cell: ({ getValue }) => {
      const v = Number(getValue())
      return (
        <span className="font-mono text-[13px] text-[var(--text)]">
          {v > 0 ? fmtDollar(v) : '—'}
        </span>
      )
    },
  }),
  colHelper.accessor('enabled', {
    header: 'Status',
    cell: ({ getValue }) => <ToggleDisplay on={getValue()} />,
  }),
]

function StrategiesTable() {
  const { data = [], isLoading, error } = useQuery({
    queryKey: ['strategies', 'active'],
    queryFn: fetchActiveStrategies,
  })

  const table = useReactTable({
    data,
    columns: STRAT_COLUMNS,
    getCoreRowModel: getCoreRowModel(),
  })

  if (isLoading) return <CardSkeleton />
  if (error) return <CardError message={(error as Error).message} />

  return (
    <div
      className="rounded-lg p-5"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      <div
        className="font-mono text-[11px] font-medium text-[var(--text2)] uppercase tracking-[0.08em] mb-4 pb-3"
        style={{ borderBottom: '1px solid var(--border)' }}
      >
        Active Strategies
      </div>

      {data.length === 0 ? (
        <p className="font-mono text-[12px] text-[var(--text3)] text-center py-4">
          No active strategies
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id}>
                  {hg.headers.map((h) => (
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
                  {row.getVisibleCells().map((cell) => (
                    <td
                      key={cell.id}
                      className="text-[12px] text-[var(--text)] py-2.5 pr-3 align-middle"
                      style={{
                        borderBottom: i < arr.length - 1 ? '1px solid var(--border)' : 'none',
                      }}
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
    </div>
  )
}

// ── System health ─────────────────────────────────────────────────────────────

function SystemHealthCard() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['system', 'health'],
    queryFn: fetchSystemHealth,
    refetchInterval: 30_000,
  })

  if (isLoading) return <CardSkeleton />
  if (error) return <CardError message={(error as Error).message} />
  if (!data) return null

  const statusLabel = data.status.charAt(0).toUpperCase() + data.status.slice(1)

  return (
    <div
      className="rounded-lg p-4"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      <div
        className="font-mono text-[11px] font-medium text-[var(--text2)] uppercase tracking-[0.08em] mb-4 pb-3"
        style={{ borderBottom: '1px solid var(--border)' }}
      >
        System Health
      </div>

      {/* TODO: Per-component breakdown (Data Pipeline, Execution Engine, Feature Store, Governance)
          is not available. GET /api/v1/system/health returns aggregate status only.
          Backend dependency: extend SystemHealthResponse with a components map:
            { component: string; status: "healthy" | "degraded" | "critical" }[]
          Once available, replace this card body with the per-row component list. */}

      <div
        className="flex items-center justify-between py-2.5"
        style={{ borderBottom: '1px solid var(--border)' }}
      >
        <span className="text-[13px] text-[var(--text)]">Overall Status</span>
        <StatusBadge variant={systemStatusBadgeVariant(data.status)} dot>
          {statusLabel}
        </StatusBadge>
      </div>

      <div
        className="flex items-center justify-between py-2.5"
        style={{ borderBottom: data.alerts.length > 0 ? '1px solid var(--border)' : 'none' }}
      >
        <span className="text-[13px] text-[var(--text)]">Trading Mode</span>
        <span className="font-mono text-[13px] text-[var(--text2)] capitalize">
          {data.trading_mode}
        </span>
      </div>

      {data.alerts.length > 0 && (
        <div className="pt-2.5">
          <div className="font-mono text-[9px] text-[var(--text3)] uppercase tracking-[0.1em] mb-2">
            Alerts
          </div>
          {data.alerts.map((alert, i) => (
            <div key={i} className="flex gap-2 py-1">
              <span
                className="w-1.5 h-1.5 rounded-full shrink-0 mt-[5px]"
                style={{ background: 'var(--yellow)' }}
              />
              <span className="text-[12px] text-[var(--text2)]">{alert}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Risk snapshot ─────────────────────────────────────────────────────────────

function RiskSnapshotCard() {
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
    {
      // NOTE: Backend exposes current_drawdown (live), not max historical drawdown.
      label: 'Current Drawdown',
      value: `${Number(risk.current_drawdown).toFixed(1)}%`,
      color: 'var(--yellow)',
    },
    {
      label: 'Portfolio Volatility',
      value: `${Number(risk.portfolio_volatility).toFixed(1)}% ann.`,
    },
    {
      label: 'Sharpe (30d)',
      value: Number(perf.sharpe_ratio).toFixed(2),
      color: 'var(--accent)',
    },
    {
      label: 'VaR (95%, 1d)',
      value: `$${Number(risk.value_at_risk_1d_95).toLocaleString()}`,
    },
  ]

  return (
    <div
      className="rounded-lg p-4"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      <div
        className="font-mono text-[11px] font-medium text-[var(--text2)] uppercase tracking-[0.08em] mb-4 pb-3"
        style={{ borderBottom: '1px solid var(--border)' }}
      >
        Risk Snapshot
      </div>

      {rows.map(({ label, value, color }, i) => (
        <div
          key={label}
          className="flex items-center justify-between py-2.5"
          style={{ borderBottom: i < rows.length - 1 ? '1px solid var(--border)' : 'none' }}
        >
          <span className="text-[13px] text-[var(--text)]">{label}</span>
          <span className="font-mono text-[13px]" style={{ color: color ?? 'var(--text)' }}>
            {value}
          </span>
        </div>
      ))}
    </div>
  )
}

// ── Activity feed ─────────────────────────────────────────────────────────────

function RecentActivityCard() {
  const { data = [], isLoading, error } = useQuery({
    queryKey: ['activity', 'recent'],
    queryFn: () => fetchRecentActivity(10),
    refetchInterval: 30_000,
  })

  if (isLoading) return <CardSkeleton />
  if (error) return <CardError message={(error as Error).message} />

  return (
    <div
      className="rounded-lg p-4 flex-1"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      <div
        className="font-mono text-[11px] font-medium text-[var(--text2)] uppercase tracking-[0.08em] mb-4 pb-3"
        style={{ borderBottom: '1px solid var(--border)' }}
      >
        Recent Activity
      </div>

      {data.length === 0 ? (
        <p className="font-mono text-[12px] text-[var(--text3)] text-center py-4">
          No recent activity
        </p>
      ) : (
        data.map((item, i) => (
          <div
            key={`${item.timestamp}-${i}`}
            className="flex gap-3 py-2.5"
            style={{ borderBottom: i < data.length - 1 ? '1px solid var(--border)' : 'none' }}
          >
            <span
              className="w-1.5 h-1.5 rounded-full shrink-0 mt-[5px]"
              style={{ background: SEVERITY_DOT_COLOR[item.severity] }}
            />
            <div>
              <div className="text-[12px] text-[var(--text)]">{item.description}</div>
              <div className="font-mono text-[10px] text-[var(--text2)] mt-0.5">
                {fmtRelTime(item.timestamp)}
              </div>
            </div>
          </div>
        ))
      )}
    </div>
  )
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const { data: summary, isLoading: summaryLoading } = useQuery({
    queryKey: ['portfolio', 'summary'],
    queryFn: fetchPortfolioSummary,
  })

  const { data: activeStrategies = [] } = useQuery({
    queryKey: ['strategies', 'active'],
    queryFn: fetchActiveStrategies,
  })

  const { data: health } = useQuery({
    queryKey: ['system', 'health'],
    queryFn: fetchSystemHealth,
    refetchInterval: 30_000,
  })

  const portfolioValue = Number(summary?.current_portfolio_value ?? 0)
  const dayPnl        = Number(summary?.todays_pnl_amount ?? 0)
  const dayPnlPct     = Number(summary?.todays_pnl_percent ?? 0)
  const totalPnl      = Number(summary?.total_pnl_amount ?? 0)
  const totalPnlPct   = Number(summary?.total_pnl_percent ?? 0)

  const liveCount  = activeStrategies.filter((s) => s.status === 'live').length
  const paperCount = activeStrategies.filter((s) => s.status === 'paper').length
  const totalActive = health?.active_strategy_count ?? activeStrategies.length

  const systemStatus = health?.status ?? 'healthy'
  const statusColor  =
    systemStatus === 'healthy'  ? 'var(--accent)' :
    systemStatus === 'degraded' ? 'var(--yellow)' : 'var(--red)'
  const statusLabel = systemStatus.charAt(0).toUpperCase() + systemStatus.slice(1)
  const alertCount  = health?.alerts.length ?? 0

  return (
    <div className="p-6">
      {/* Top row — 4 metric cards */}
      <div className="grid grid-cols-4 gap-4 mb-4">
        <MetricCard
          label="Portfolio Value"
          value={
            summaryLoading
              ? <span className="text-[36px] text-[var(--text3)]">—</span>
              : <span className="text-[36px]">${portfolioValue.toLocaleString()}</span>
          }
          change={
            summaryLoading ? '—' : (
              <span style={{ color: dayPnl >= 0 ? 'var(--accent)' : 'var(--red)' }}>
                {dayPnl >= 0 ? '▲' : '▼'} {dayPnl >= 0 ? '+' : '-'}${Math.abs(dayPnl).toLocaleString()} today ({dayPnl >= 0 ? '+' : ''}{dayPnlPct.toFixed(2)}%)
              </span>
            )
          }
        />
        <MetricCard
          label="Total PnL"
          value={
            summaryLoading
              ? <span className="text-[28px] text-[var(--text3)]">—</span>
              : (
                <span
                  className="text-[28px]"
                  style={{ color: totalPnl >= 0 ? 'var(--accent)' : 'var(--red)' }}
                >
                  {totalPnl >= 0 ? '+' : '-'}${Math.abs(totalPnl).toLocaleString()}
                </span>
              )
          }
          change={
            summaryLoading
              ? '—'
              : `${totalPnlPct >= 0 ? '+' : ''}${totalPnlPct.toFixed(1)}% all time`
          }
        />
        <MetricCard
          label="Active Strategies"
          value={<span className="text-[28px]">{totalActive}</span>}
          change={
            <>
              <span style={{ color: 'var(--accent)' }}>{liveCount} live</span>
              {' · '}{paperCount} paper
            </>
          }
        />
        <MetricCard
          label="Risk Status"
          value={
            <span className="text-[20px] block mt-1" style={{ color: statusColor }}>
              ● {statusLabel}
            </span>
          }
          change={alertCount > 0 ? `${alertCount} alert(s) active` : 'All limits within range'}
        />
      </div>

      {/* Main grid — 2:1 */}
      <div className="grid gap-4" style={{ gridTemplateColumns: '2fr 1fr' }}>
        {/* Left column */}
        <div className="flex flex-col gap-4">
          <EquityChart />
          <StrategiesTable />
        </div>

        {/* Right column */}
        <div className="flex flex-col gap-4">
          <SystemHealthCard />
          <RiskSnapshotCard />
          <RecentActivityCard />
        </div>
      </div>
    </div>
  )
}
