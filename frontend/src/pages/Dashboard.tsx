import { useState } from 'react'
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
import StatusBadge, { governanceBadgeVariant, healthBadgeVariant } from '../components/shared/StatusBadge'
import { cn } from '../lib/utils'
import {
  mockPortfolioSummary,
  mockEquityCurve,
  mockStrategies,
  mockSystemHealth,
  mockRiskMetrics,
  mockActivity,
} from '../mock/data'
import type { Strategy, ActivityItem } from '../types'

// ── Local helpers ─────────────────────────────────────────────────────────────

const MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']

function fmtMonth(dateStr: string) {
  return MONTH_NAMES[parseInt(dateStr.split('-')[1]) - 1]
}

function fmtDollar(n: number) {
  return `$${Math.abs(n).toLocaleString()}`
}

// ── Toggle display ────────────────────────────────────────────────────────────

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

// ── Equity chart ──────────────────────────────────────────────────────────────

type Period = '1W' | '1M' | '3M' | '1Y'
const PERIODS: Period[] = ['1W', '1M', '3M', '1Y']

// Month boundary ticks present in mockEquityCurve
const X_TICKS = ['2026-02-01', '2026-03-01', '2026-04-05', '2026-05-03']

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

  return (
    <div
      className="rounded-lg p-5"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <span
          className="font-mono text-[11px] font-medium text-[var(--text2)] uppercase tracking-[0.08em]"
        >
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

      {/* Chart */}
      <div
        className="rounded-md overflow-hidden"
        style={{ background: 'var(--surface2)', border: '1px solid var(--border)' }}
      >
        <ResponsiveContainer width="100%" height={130}>
          <AreaChart data={mockEquityCurve} margin={{ top: 8, right: 0, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#00E5A0" stopOpacity={0.2} />
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
    </div>
  )
}

// ── Strategies table ──────────────────────────────────────────────────────────

const colHelper = createColumnHelper<Strategy>()

const STRAT_COLUMNS = [
  colHelper.accessor('name', {
    header: 'Strategy',
    cell: ({ row }) => (
      <div>
        <div className="text-[13px] font-medium text-[var(--text)]">{row.original.name}</div>
        <div className="font-mono text-[10px] text-[var(--text3)]">{row.original.id}</div>
      </div>
    ),
  }),
  colHelper.accessor('governanceState', {
    header: 'Mode',
    cell: ({ getValue }) => {
      const state = getValue()
      const label = state === 'live' ? 'Live' : state === 'paper' ? 'Paper' : 'Paper'
      return <StatusBadge variant={governanceBadgeVariant(state)}>{label}</StatusBadge>
    },
  }),
  colHelper.accessor('sharpe', {
    header: 'Sharpe',
    cell: ({ getValue }) => {
      const v = getValue()
      const color = v >= 1.5 ? 'var(--accent)' : v >= 1.0 ? 'var(--yellow)' : 'var(--text2)'
      return <span className="font-mono text-[13px]" style={{ color }}>{v.toFixed(2)}</span>
    },
  }),
  colHelper.accessor('pnl30d', {
    header: 'PnL (30d)',
    cell: ({ getValue }) => {
      const v = getValue()
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
  colHelper.accessor('allocation', {
    header: 'Allocation',
    cell: ({ getValue }) => {
      const v = getValue()
      return (
        <span className="font-mono text-[13px] text-[var(--text)]">
          {v != null ? fmtDollar(v) : '—'}
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
  const table = useReactTable({
    data: mockStrategies,
    columns: STRAT_COLUMNS,
    getCoreRowModel: getCoreRowModel(),
  })

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
    </div>
  )
}

// ── System health ─────────────────────────────────────────────────────────────

const HEALTH_LABELS: Record<string, string> = {
  dataPipeline:    'Data Pipeline',
  executionEngine: 'Execution Engine',
  featureStore:    'Feature Store',
  governance:      'Governance',
}

function SystemHealthCard() {
  const health = mockSystemHealth
  const entries = Object.entries(health) as [keyof typeof health, typeof health[keyof typeof health]][]

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

      {entries.map(([key, status], i) => (
        <div
          key={key}
          className="flex items-center justify-between py-2.5"
          style={{ borderBottom: i < entries.length - 1 ? '1px solid var(--border)' : 'none' }}
        >
          <span className="text-[13px] text-[var(--text)]">{HEALTH_LABELS[key]}</span>
          <StatusBadge variant={healthBadgeVariant(status)} dot>
            {status.charAt(0).toUpperCase() + status.slice(1)}
          </StatusBadge>
        </div>
      ))}
    </div>
  )
}

// ── Risk snapshot ─────────────────────────────────────────────────────────────

function RiskSnapshotCard() {
  const m = mockRiskMetrics

  const rows: { label: string; value: string; color?: string }[] = [
    { label: 'Max Drawdown',      value: `${m.maxDrawdown}%`,        color: 'var(--yellow)' },
    { label: 'Portfolio Volatility', value: `${m.volatility}% ann.` },
    { label: 'Sharpe (30d)',      value: m.sharpe.toFixed(2),        color: 'var(--accent)' },
    { label: 'VaR (95%, 1d)',     value: `$${m.var95.toLocaleString()}` },
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
          <span
            className="font-mono text-[13px]"
            style={{ color: color ?? 'var(--text)' }}
          >
            {value}
          </span>
        </div>
      ))}
    </div>
  )
}

// ── Activity feed ─────────────────────────────────────────────────────────────

const ACTIVITY_DOT_COLOR: Record<ActivityItem['type'], string> = {
  fill:    'var(--accent)',
  paper:   'var(--blue)',
  warning: 'var(--yellow)',
  system:  'var(--text3)',
}

function RecentActivityCard() {
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

      {mockActivity.map((item, i) => (
        <div
          key={item.id}
          className="flex gap-3 py-2.5"
          style={{ borderBottom: i < mockActivity.length - 1 ? '1px solid var(--border)' : 'none' }}
        >
          <span
            className="w-1.5 h-1.5 rounded-full shrink-0 mt-[5px]"
            style={{ background: ACTIVITY_DOT_COLOR[item.type] }}
          />
          <div>
            <div className="text-[12px] text-[var(--text)]">{item.text}</div>
            <div className="font-mono text-[10px] text-[var(--text2)] mt-0.5">{item.meta}</div>
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const s = mockPortfolioSummary

  return (
    <div className="p-6">
      {/* Top row — 4 metric cards */}
      <div className="grid grid-cols-4 gap-4 mb-4">
        <MetricCard
          label="Portfolio Value"
          value={<span className="text-[36px]">${s.totalValue.toLocaleString()}</span>}
          change={
            <span style={{ color: 'var(--accent)' }}>
              ▲ +${s.dayPnl.toLocaleString()} today (+{s.dayPnlPct}%)
            </span>
          }
        />
        <MetricCard
          label="Total PnL"
          value={
            <span className="text-[28px]" style={{ color: 'var(--accent)' }}>
              +${s.totalPnl.toLocaleString()}
            </span>
          }
          change={`+${s.totalPnlPct}% all time`}
        />
        <MetricCard
          label="Active Strategies"
          value={<span className="text-[28px]">{s.activeStrategies}</span>}
          change={
            <>
              <span style={{ color: 'var(--accent)' }}>{s.liveStrategies} live</span>
              {' · '}{s.paperStrategies} paper
            </>
          }
        />
        <MetricCard
          label="Risk Status"
          value={
            <span
              className="text-[20px] block mt-1"
              style={{ color: 'var(--accent)' }}
            >
              ● Healthy
            </span>
          }
          change="All limits within range"
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
