import { useState } from 'react'
import StatusBadge from '../components/shared/StatusBadge'
import type { BadgeVariant } from '../components/shared/StatusBadge'
import { cn } from '../lib/utils'
import { mockStrategies } from '../mock/data'
import type { GovernanceState, Strategy } from '../types'

// ── Value colour helpers ──────────────────────────────────────────────────────

function sharpeColor(v: number, state: GovernanceState): string {
  if (state === 'research' || state === 'proposed') return 'var(--text2)'
  return v >= 1.5 ? 'var(--accent)' : 'var(--yellow)'
}

function cagrColor(v: number, state: GovernanceState): string {
  if (state === 'research' || state === 'proposed') return 'var(--text2)'
  return v >= 15 ? 'var(--accent)' : 'var(--yellow)'
}

function maxDDColor(v: number): string {
  const abs = Math.abs(v)
  if (abs <= 10) return 'var(--text)'
  if (abs <= 13) return 'var(--yellow)'
  return 'var(--red)'
}

// ── Per-card derived display ──────────────────────────────────────────────────

function isUnderperforming(s: Strategy) {
  return s.governanceState === 'live' && s.sharpe < 1.0
}

function headerBadge(s: Strategy): { variant: BadgeVariant; label: string } {
  if (s.governanceState === 'live') {
    return isUnderperforming(s)
      ? { variant: 'yellow', label: 'Watch' }
      : { variant: 'green', label: 'Live' }
  }
  const MAP: Record<GovernanceState, { variant: BadgeVariant; label: string }> = {
    paper:    { variant: 'blue',   label: 'Paper' },
    research: { variant: 'purple', label: 'Research' },
    proposed: { variant: 'gray',   label: 'Proposed' },
    rejected: { variant: 'red',    label: 'Rejected' },
    retired:  { variant: 'gray',   label: 'Retired' },
    live:     { variant: 'green',  label: 'Live' },
  }
  return MAP[s.governanceState]
}

function sparklineColor(s: Strategy): string {
  if (s.governanceState === 'live')     return isUnderperforming(s) ? 'var(--yellow)' : 'var(--accent)'
  if (s.governanceState === 'paper')    return 'var(--blue)'
  if (s.governanceState === 'research') return 'var(--purple)'
  return 'var(--text3)'
}

function sparklineHex(s: Strategy): string {
  if (s.governanceState === 'live')     return isUnderperforming(s) ? '#E8A838' : '#00E5A0'
  if (s.governanceState === 'paper')    return '#3B9EFF'
  if (s.governanceState === 'research') return '#9B72FF'
  return '#4A5568'
}

function bottomBadge(s: Strategy): { variant: BadgeVariant; label: string } {
  if (s.governanceState === 'live') {
    return isUnderperforming(s)
      ? { variant: 'yellow', label: '⚠ Underperforming' }
      : { variant: 'green',  label: `Stage ${s.stage} Passed` }
  }
  if (s.governanceState === 'paper')    return { variant: 'blue',   label: 'Paper / 30d req.' }
  if (s.governanceState === 'research') return { variant: 'purple', label: 'Simulation only' }
  if (s.governanceState === 'proposed') return { variant: 'gray',   label: 'Proposed' }
  return { variant: 'gray', label: s.governanceState }
}

type BtnStyle = 'ghost' | 'primary' | 'danger'
function actionButtons(s: Strategy): { label: string; style: BtnStyle }[] {
  const detail = { label: 'Detail', style: 'ghost' as BtnStyle }
  if (s.governanceState === 'live') {
    return isUnderperforming(s)
      ? [detail, { label: 'Demote',  style: 'danger'  }]
      : [detail, { label: 'Pause',   style: 'ghost'   }]
  }
  if (s.governanceState === 'paper')    return [detail, { label: 'Promote', style: 'primary' }]
  if (s.governanceState === 'research') return [detail, { label: 'Reject',  style: 'ghost'   }]
  return [detail]
}

function allocLabel(s: Strategy): string {
  if (s.allocation == null) return 'Paper'
  return `$${(s.allocation / 1000).toFixed(0)}k`
}

// ── Sparkline SVG ─────────────────────────────────────────────────────────────

function SparklineChart({ strategy, selected }: { strategy: Strategy; selected: boolean }) {
  const data = strategy.sparkline
  const W = 200
  const H = 36
  const n = data.length
  const step = W / (n - 1)
  const hex = sparklineHex(strategy)
  const gradId = `sp-${strategy.id}`

  const linePts = data.map((y, i) => `${(i * step).toFixed(1)},${y}`).join(' L')
  const linePath = `M${linePts}`
  // fill: close path to bottom-left corner
  const fillPath = `${linePath} L${W},${H} L0,${H} Z`

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 36 }}>
      {selected && (
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={hex} stopOpacity={0.3} />
            <stop offset="100%" stopColor={hex} stopOpacity={0} />
          </linearGradient>
        </defs>
      )}
      {selected && <path d={fillPath} fill={`url(#${gradId})`} />}
      <path d={linePath} fill="none" stroke={hex} strokeWidth="1.5" />
    </svg>
  )
}

// ── Small action button ───────────────────────────────────────────────────────

const BTN_BASE =
  'py-1 px-2.5 rounded font-mono text-[9px] font-medium uppercase tracking-[0.08em] cursor-pointer border transition-all duration-150'

const BTN_STYLES: Record<BtnStyle, string> = {
  ghost:   'bg-transparent text-[var(--text2)] border-[var(--border2)] hover:text-[var(--text)] hover:border-[var(--text3)]',
  primary: 'bg-[var(--accent)] text-black border-[var(--accent)] hover:bg-[var(--accent2)]',
  danger:  'bg-[var(--red-dim)] text-[var(--red)] border-[var(--red)] hover:bg-[rgba(255,77,109,0.2)]',
}

// ── Strategy card ─────────────────────────────────────────────────────────────

function StrategyCard({
  strategy: s,
  selected,
  onToggle,
}: {
  strategy: Strategy
  selected: boolean
  onToggle: (id: string) => void
}) {
  const hb    = headerBadge(s)
  const bb    = bottomBadge(s)
  const btns  = actionButtons(s)
  const isRes = s.governanceState === 'research' || s.governanceState === 'proposed'

  const row1 = [
    { label: 'Sharpe',  value: s.sharpe.toFixed(2),    color: sharpeColor(s.sharpe, s.governanceState) },
    { label: 'CAGR',    value: `+${s.cagr}%`,          color: cagrColor(s.cagr, s.governanceState) },
    { label: 'Max DD',  value: `${s.maxDrawdown}%`,    color: maxDDColor(s.maxDrawdown) },
  ]

  const row2 = isRes
    ? [
        { label: 'Stage',   value: `Stage ${s.stage}` },
        { label: 'Runs',    value: String(s.trades30d) },
        { label: 'Dataset', value: 'v43' },
      ]
    : [
        { label: 'Win Rate',    value: `${s.winRate}%` },
        { label: 'Trades (30d)', value: String(s.trades30d) },
        { label: 'Allocation',  value: allocLabel(s) },
      ]

  return (
    <div
      className="rounded-md p-4 cursor-pointer transition-all duration-150"
      style={{
        background:   selected ? 'var(--accent-dim)' : 'var(--surface2)',
        border:       `1px solid ${selected ? 'var(--accent)' : 'var(--border)'}`,
        borderRadius: 6,
      }}
      onMouseEnter={(e) => {
        if (!selected) (e.currentTarget as HTMLDivElement).style.borderColor = 'var(--border2)'
      }}
      onMouseLeave={(e) => {
        if (!selected) (e.currentTarget as HTMLDivElement).style.borderColor = 'var(--border)'
      }}
      onClick={() => onToggle(s.id)}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-1">
        <span className="text-[13px] font-medium text-[var(--text)]">{s.name}</span>
        <StatusBadge variant={hb.variant}>{hb.label}</StatusBadge>
      </div>

      {/* ID + type */}
      <div className="font-mono text-[10px] text-[var(--text3)] mb-3">
        {s.id} · {s.type} · Equity
      </div>

      {/* Metrics row 1 */}
      <div className="grid grid-cols-3 gap-2 mb-3">
        {row1.map(({ label, value, color }) => (
          <div key={label}>
            <div className="font-mono text-[9px] text-[var(--text3)] uppercase tracking-[0.1em]">{label}</div>
            <div className="font-mono text-[14px] font-medium mt-0.5" style={{ color }}>
              {value}
            </div>
          </div>
        ))}
      </div>

      {/* Metrics row 2 */}
      <div className="grid grid-cols-3 gap-2 mb-3">
        {row2.map(({ label, value }) => (
          <div key={label}>
            <div className="font-mono text-[9px] text-[var(--text3)] uppercase tracking-[0.1em]">{label}</div>
            <div
              className="font-mono font-medium mt-0.5 text-[var(--text)]"
              style={{ fontSize: isRes ? 12 : 14 }}
            >
              {value}
            </div>
          </div>
        ))}
      </div>

      {/* Sparkline */}
      <SparklineChart strategy={s} selected={selected} />

      {/* Bottom row */}
      <div className="flex items-center justify-between mt-2">
        <StatusBadge variant={bb.variant}>{bb.label}</StatusBadge>
        <div className="flex gap-2" onClick={(e) => e.stopPropagation()}>
          {btns.map(({ label, style }) => (
            <button key={label} className={cn(BTN_BASE, BTN_STYLES[style])}>
              {label}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Comparison table ──────────────────────────────────────────────────────────

function govLabel(s: Strategy): { variant: BadgeVariant; label: string } {
  if (s.governanceState === 'live') {
    return isUnderperforming(s)
      ? { variant: 'yellow', label: 'Under Review' }
      : { variant: 'green',  label: 'Live Approved' }
  }
  if (s.governanceState === 'paper')    return { variant: 'blue',   label: 'Paper Approved' }
  if (s.governanceState === 'research') return { variant: 'purple', label: 'Research' }
  return { variant: 'gray', label: s.governanceState }
}

function stageLabel(s: Strategy): { variant: BadgeVariant; label: string } {
  if (s.governanceState === 'paper') return { variant: 'blue',  label: 'Paper' }
  return { variant: 'green', label: `Stage ${s.stage}` }
}

function ComparisonTable({ selectedIds }: { selectedIds: Set<string> }) {
  const selected = mockStrategies.filter(s => selectedIds.has(s.id))

  const title = selected.length === 0
    ? 'Strategy Comparison'
    : selected.length === 1
    ? `Strategy Comparison — ${selected[0].name}`
    : `Strategy Comparison — Selected: ${selected.map(s => s.name).join(' vs ')}`

  return (
    <div
      className="rounded-lg p-5"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      <div
        className="font-mono text-[11px] font-medium text-[var(--text2)] uppercase tracking-[0.08em] mb-4 pb-3"
        style={{ borderBottom: '1px solid var(--border)' }}
      >
        {title}
      </div>

      {selected.length === 0 ? (
        <p className="font-mono text-[11px] text-[var(--text3)]">
          Click strategy cards above to compare them side by side.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr>
                <th
                  className="font-mono text-[9px] font-medium text-[var(--text3)] uppercase tracking-[0.1em] text-left pb-2.5 pr-4"
                  style={{ width: 160, borderBottom: '1px solid var(--border)' }}
                >
                  Metric
                </th>
                {selected.map(s => (
                  <th
                    key={s.id}
                    className="font-mono text-[9px] font-medium text-[var(--text3)] uppercase tracking-[0.1em] text-left pb-2.5 pr-4"
                    style={{ borderBottom: '1px solid var(--border)' }}
                  >
                    {s.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {/* Sharpe */}
              <tr>
                <td className="font-mono text-[11px] text-[var(--text2)] py-2.5 pr-4" style={{ borderBottom: '1px solid var(--border)' }}>
                  Sharpe Ratio
                </td>
                {selected.map((s, i, arr) => (
                  <td
                    key={s.id}
                    className="font-mono text-[13px] py-2.5 pr-4"
                    style={{
                      color: sharpeColor(s.sharpe, s.governanceState),
                      borderBottom: '1px solid var(--border)',
                    }}
                  >
                    {s.sharpe.toFixed(2)}
                  </td>
                ))}
              </tr>
              {/* CAGR */}
              <tr>
                <td className="font-mono text-[11px] text-[var(--text2)] py-2.5 pr-4" style={{ borderBottom: '1px solid var(--border)' }}>
                  CAGR
                </td>
                {selected.map(s => (
                  <td key={s.id} className="font-mono text-[13px] text-[var(--text)] py-2.5 pr-4" style={{ borderBottom: '1px solid var(--border)' }}>
                    +{s.cagr}%
                  </td>
                ))}
              </tr>
              {/* Max Drawdown */}
              <tr>
                <td className="font-mono text-[11px] text-[var(--text2)] py-2.5 pr-4" style={{ borderBottom: '1px solid var(--border)' }}>
                  Max Drawdown
                </td>
                {selected.map(s => (
                  <td
                    key={s.id}
                    className="font-mono text-[13px] py-2.5 pr-4"
                    style={{ color: maxDDColor(s.maxDrawdown), borderBottom: '1px solid var(--border)' }}
                  >
                    {s.maxDrawdown}%
                  </td>
                ))}
              </tr>
              {/* Win Rate */}
              <tr>
                <td className="font-mono text-[11px] text-[var(--text2)] py-2.5 pr-4" style={{ borderBottom: '1px solid var(--border)' }}>
                  Win Rate
                </td>
                {selected.map(s => (
                  <td key={s.id} className="font-mono text-[13px] text-[var(--text)] py-2.5 pr-4" style={{ borderBottom: '1px solid var(--border)' }}>
                    {s.winRate}%
                  </td>
                ))}
              </tr>
              {/* Simulation Stage */}
              <tr>
                <td className="font-mono text-[11px] text-[var(--text2)] py-2.5 pr-4" style={{ borderBottom: '1px solid var(--border)' }}>
                  Simulation Stage
                </td>
                {selected.map(s => {
                  const sl = stageLabel(s)
                  return (
                    <td key={s.id} className="py-2.5 pr-4" style={{ borderBottom: '1px solid var(--border)' }}>
                      <StatusBadge variant={sl.variant}>{sl.label}</StatusBadge>
                    </td>
                  )
                })}
              </tr>
              {/* Governance State */}
              <tr>
                <td className="font-mono text-[11px] text-[var(--text2)] py-2.5 pr-4">
                  Governance State
                </td>
                {selected.map(s => {
                  const gl = govLabel(s)
                  return (
                    <td key={s.id} className="py-2.5 pr-4">
                      <StatusBadge variant={gl.variant}>{gl.label}</StatusBadge>
                    </td>
                  )
                })}
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Filter config ─────────────────────────────────────────────────────────────

type FilterKey = 'all' | 'live' | 'paper' | 'research' | 'retired'

const FILTERS: { key: FilterKey; label: string; variant: BadgeVariant; dot?: boolean }[] = [
  { key: 'all',      label: 'All',      variant: 'gray'   },
  { key: 'live',     label: 'Live',     variant: 'green', dot: true },
  { key: 'paper',    label: 'Paper',    variant: 'blue'   },
  { key: 'research', label: 'Research', variant: 'purple' },
  { key: 'retired',  label: 'Retired',  variant: 'gray'   },
]

// ── StrategyLab ───────────────────────────────────────────────────────────────

export default function StrategyLab() {
  const [filter,   setFilter]   = useState<FilterKey>('all')
  const [selected, setSelected] = useState<Set<string>>(new Set(['STR-00142']))

  function toggleSelected(id: string) {
    setSelected(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const visible = filter === 'all'
    ? mockStrategies
    : mockStrategies.filter(s => s.governanceState === filter)

  return (
    <div className="p-6">
      {/* Filter bar */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex gap-2">
          {FILTERS.map(f => {
            const active = filter === f.key
            return (
              <button
                key={f.key}
                onClick={() => setFilter(f.key)}
                className={cn('transition-opacity', !active && 'opacity-40 hover:opacity-70')}
              >
                <StatusBadge
                  variant={active ? f.variant : 'gray'}
                  dot={f.dot && active}
                  className="px-3 py-1.5 cursor-pointer"
                >
                  {f.label}
                </StatusBadge>
              </button>
            )
          })}
        </div>

        <div className="flex gap-2">
          <button
            className={cn(BTN_BASE, BTN_STYLES.ghost)}
            style={{ padding: '8px 16px', fontSize: 10 }}
          >
            Compare Selected
          </button>
          <button
            className={cn(BTN_BASE, BTN_STYLES.primary)}
            style={{ padding: '8px 16px', fontSize: 10 }}
          >
            + New Experiment
          </button>
        </div>
      </div>

      {/* Strategy grid */}
      <div className="grid grid-cols-3 gap-4 mb-4">
        {visible.map(s => (
          <StrategyCard
            key={s.id}
            strategy={s}
            selected={selected.has(s.id)}
            onToggle={toggleSelected}
          />
        ))}

        {/* Placeholder card — only shown when filter is 'all' */}
        {filter === 'all' && (
          <div
            className="flex flex-col items-center justify-center rounded-md p-4 cursor-pointer opacity-50"
            style={{
              minHeight: 200,
              background: 'var(--surface2)',
              border: '1px dashed var(--border)',
              borderRadius: 6,
            }}
          >
            <div className="text-[28px] text-[var(--text3)] mb-2">+</div>
            <div className="font-mono text-[10px] text-[var(--text3)] text-center">Run New Experiment</div>
          </div>
        )}
      </div>

      {/* Comparison table */}
      <ComparisonTable selectedIds={selected} />
    </div>
  )
}
