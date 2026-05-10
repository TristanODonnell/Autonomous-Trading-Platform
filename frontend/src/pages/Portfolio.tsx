import { useState } from 'react'
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
  mockPortfolioSummary,
  mockEquityCurve,
  mockDrawdownSeries,
  mockHoldings,
  mockStrategyAllocation,
  mockRiskMetrics,
  mockSectorAllocation,
} from '../mock/data'
import type { Holding } from '../types'

// ── Constants ─────────────────────────────────────────────────────────────────

const MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
const X_TICKS = ['2026-02-01', '2026-03-01', '2026-04-05', '2026-05-03']

function fmtMonth(d: string) {
  return MONTH_NAMES[parseInt(d.split('-')[1]) - 1]
}
function fmtPrice(n: number) {
  return `$${n.toFixed(2)}`
}
function fmtDollar(n: number) {
  return `$${Math.abs(n).toLocaleString()}`
}

// Merge equity + drawdown into one array for ComposedChart
const combinedData = mockEquityCurve.map((eq, i) => ({
  date: eq.date,
  equity: eq.value,
  drawdown: mockDrawdownSeries[i]?.drawdown ?? 0,
}))

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
      <span
        className="font-mono text-[13px] text-right"
        style={{ color: valueColor ?? 'var(--text)' }}
      >
        {value}
      </span>
    </div>
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
  const [visible, setVisible] = useState<Set<ChartView>>(new Set(['equity', 'drawdown']))

  function toggle(v: ChartView) {
    setVisible(prev => {
      const next = new Set(prev)
      next.has(v) ? next.delete(v) : next.add(v)
      if (next.size === 0) return prev        // keep at least one
      return next
    })
  }

  const showEq = visible.has('equity')
  const showDd = visible.has('drawdown')

  return (
    <Card>
      <div className="flex items-center justify-between mb-4">
        <span className="font-mono text-[11px] font-medium text-[var(--text2)] uppercase tracking-[0.08em]">
          Performance
        </span>
        <div className="flex gap-2">
          {(['equity', 'drawdown'] as ChartView[]).map((v) => {
            const active = visible.has(v)
            return (
              <button
                key={v}
                onClick={() => toggle(v)}
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

      <div
        className="rounded-md overflow-hidden"
        style={{ background: 'var(--surface2)', border: '1px solid var(--border)' }}
      >
        <ResponsiveContainer width="100%" height={120}>
          <ComposedChart data={combinedData} margin={{ top: 8, right: 0, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="pfEqGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#00E5A0" stopOpacity={0.15} />
                <stop offset="100%" stopColor="#00E5A0" stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="date"
              ticks={X_TICKS}
              tickFormatter={fmtMonth}
              axisLine={false}
              tickLine={false}
              interval={0}
              tick={{ fill: '#4A5568', fontFamily: 'IBM Plex Mono', fontSize: 8 }}
              height={20}
            />
            {/* Left axis: equity scale */}
            <YAxis yAxisId="eq" hide domain={['auto', 'auto']} />
            {/* Right axis: drawdown — domain [-100,0] so -6% appears near the top */}
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
    </Card>
  )
}

// ── Holdings table ────────────────────────────────────────────────────────────

const colHelper = createColumnHelper<Holding>()

const HOLDING_COLS = [
  colHelper.accessor('symbol', {
    header: 'Symbol',
    cell: ({ getValue }) => <strong className="text-[13px] text-[var(--text)]">{getValue()}</strong>,
  }),
  colHelper.accessor('qty', {
    header: 'Qty',
    cell: ({ getValue }) => <span className="font-mono text-[12px]">{getValue()}</span>,
  }),
  colHelper.accessor('avgPrice', {
    header: 'Avg Price',
    cell: ({ getValue }) => <span className="font-mono text-[12px]">{fmtPrice(getValue())}</span>,
  }),
  colHelper.accessor('currentPrice', {
    header: 'Current',
    cell: ({ getValue }) => <span className="font-mono text-[12px]">{fmtPrice(getValue())}</span>,
  }),
  colHelper.accessor('value', {
    header: 'Value',
    cell: ({ getValue }) => <span className="font-mono text-[12px]">{fmtDollar(getValue())}</span>,
  }),
  colHelper.accessor('pnl', {
    header: 'PnL',
    cell: ({ getValue }) => {
      const v = getValue()
      return (
        <span
          className="font-mono text-[12px]"
          style={{ color: v >= 0 ? 'var(--accent)' : 'var(--red)' }}
        >
          {v >= 0 ? '+' : '-'}{fmtDollar(v)}
        </span>
      )
    },
  }),
  colHelper.accessor('strategyName', {
    header: 'Strategy',
    cell: ({ getValue }) => <StatusBadge variant="gray">{getValue()}</StatusBadge>,
  }),
]

function HoldingsTable() {
  const table = useReactTable({
    data: mockHoldings,
    columns: HOLDING_COLS,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <Card>
      <CardTitle>Holdings</CardTitle>
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
    </Card>
  )
}

// ── Allocation by strategy ────────────────────────────────────────────────────

const BAR_COLOR: Record<string, string> = {
  accent:  'var(--accent)',
  blue:    'var(--blue)',
  purple:  'var(--purple)',
  yellow:  'var(--yellow)',
  border2: 'var(--border2)',
}

function AllocationByStrategy() {
  return (
    <div
      className="rounded-lg p-4"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      <CardTitle>Allocation by Strategy</CardTitle>
      {mockStrategyAllocation.map(({ name, pct, color }, i) => (
        <div
          key={name}
          className="flex items-center justify-between py-2.5"
          style={{ borderBottom: i < mockStrategyAllocation.length - 1 ? '1px solid var(--border)' : 'none' }}
        >
          <div className="flex-1 mr-4 min-w-0">
            <div className="text-[13px] text-[var(--text)] mb-1.5">{name}</div>
            <div
              className="h-[5px] rounded-[3px] overflow-hidden"
              style={{ background: 'var(--border2)' }}
            >
              <div
                className="h-full rounded-[3px] transition-all duration-300"
                style={{ width: `${pct}%`, background: BAR_COLOR[color] ?? 'var(--accent)' }}
              />
            </div>
          </div>
          <span className="font-mono text-[13px] text-[var(--text)] shrink-0">{pct}%</span>
        </div>
      ))}
    </div>
  )
}

// ── Risk metrics ──────────────────────────────────────────────────────────────

function RiskMetricsCard() {
  const m = mockRiskMetrics
  const rows: { label: string; value: string; color?: string }[] = [
    { label: 'Sharpe Ratio',     value: m.sharpe.toFixed(2),             color: 'var(--accent)' },
    { label: 'Sortino Ratio',    value: m.sortino.toFixed(2),            color: 'var(--accent)' },
    { label: 'Max Drawdown',     value: `${m.maxDrawdown}%`,             color: 'var(--yellow)' },
    { label: 'Volatility (ann.)',value: `${m.volatility}%` },
    { label: 'Beta (vs SPY)',    value: m.beta.toFixed(2) },
    { label: 'VaR 95% (1d)',    value: `$${m.var95.toLocaleString()}` },
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

// ── Sector exposure ───────────────────────────────────────────────────────────

function SectorExposureCard() {
  return (
    <div
      className="rounded-lg p-4"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      <CardTitle>Sector Exposure</CardTitle>
      {mockSectorAllocation.map(({ sector, pct, warning }) => (
        <div
          key={sector}
          className="flex items-center justify-between py-2.5"
          style={{ borderBottom: '1px solid var(--border)' }}
        >
          <span className="text-[13px]" style={{ color: warning ? 'var(--yellow)' : 'var(--text)' }}>
            {sector}
          </span>
          <span className="font-mono text-[13px]" style={{ color: warning ? 'var(--yellow)' : 'var(--text)' }}>
            {pct}%
          </span>
        </div>
      ))}
      {/* Warning row */}
      <div className="flex items-center justify-between py-2.5">
        <span className="text-[13px]" style={{ color: 'var(--yellow)' }}>⚠ Tech concentration</span>
        <span className="font-mono text-[10px]" style={{ color: 'var(--yellow)' }}>Limit: 60%</span>
      </div>
    </div>
  )
}

// ── Portfolio ─────────────────────────────────────────────────────────────────

export default function Portfolio() {
  const s = mockPortfolioSummary

  return (
    <div className="p-6">
      {/* Top row — 4 metric cards */}
      <div className="grid grid-cols-4 gap-4 mb-4">
        <MetricCard
          label="Total Value"
          value={<span className="text-[36px]">${s.totalValue.toLocaleString()}</span>}
          change={
            <span style={{ color: 'var(--accent)' }}>
              ▲ +${s.dayPnl.toLocaleString()} today
            </span>
          }
        />
        <MetricCard
          label="Invested Capital"
          value={<span className="text-[28px]">${s.invested.toLocaleString()}</span>}
          change={`${((s.invested / s.totalValue) * 100).toFixed(1)}% deployed`}
        />
        <MetricCard
          label="Cash Reserve"
          value={<span className="text-[28px]">${s.cash.toLocaleString()}</span>}
          change={`${((s.cash / s.totalValue) * 100).toFixed(1)}% available`}
        />
        <MetricCard
          label="Open Positions"
          value={<span className="text-[28px]">{s.openPositions}</span>}
          change={`across ${s.activeStrategies} strategies`}
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
