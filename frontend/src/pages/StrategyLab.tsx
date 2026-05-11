import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import StatusBadge from '../components/shared/StatusBadge'
import type { BadgeVariant } from '../components/shared/StatusBadge'
import { cn } from '../lib/utils'
import { fetchAllStrategies, type ApiStrategyListItem } from '../services'

type ApiStatus = ApiStrategyListItem['status']

// ── Value colour helpers ──────────────────────────────────────────────────────

function sharpeColor(v: number, status: ApiStatus): string {
  if (status === 'research' || status === 'off') return 'var(--text2)'
  return v >= 1.5 ? 'var(--accent)' : 'var(--yellow)'
}

function returnColor(v: number, status: ApiStatus): string {
  if (status === 'research' || status === 'off') return 'var(--text2)'
  return v >= 0.15 ? 'var(--accent)' : 'var(--yellow)'
}

// max_drawdown arrives as a fraction (e.g. -0.12 = -12%)
function maxDDColor(fraction: number): string {
  const pct = Math.abs(fraction) * 100
  if (pct <= 10) return 'var(--text)'
  if (pct <= 13) return 'var(--yellow)'
  return 'var(--red)'
}

// ── Per-card derived display ──────────────────────────────────────────────────

function isUnderperforming(s: ApiStrategyListItem): boolean {
  return s.status === 'live' && s.sharpe_ratio < 1.0
}

function headerBadge(s: ApiStrategyListItem): { variant: BadgeVariant; label: string } {
  if (s.status === 'live') {
    return isUnderperforming(s)
      ? { variant: 'yellow', label: 'Watch' }
      : { variant: 'green',  label: 'Live' }
  }
  const MAP: Record<ApiStatus, { variant: BadgeVariant; label: string }> = {
    paper:    { variant: 'blue',   label: 'Paper'    },
    research: { variant: 'purple', label: 'Research' },
    off:      { variant: 'gray',   label: 'Off'      },
    live:     { variant: 'green',  label: 'Live'     },
  }
  return MAP[s.status]
}

function sparklineHex(s: ApiStrategyListItem): string {
  if (s.status === 'live')     return isUnderperforming(s) ? '#E8A838' : '#00E5A0'
  if (s.status === 'paper')    return '#3B9EFF'
  if (s.status === 'research') return '#9B72FF'
  return '#4A5568'
}

function bottomBadge(s: ApiStrategyListItem): { variant: BadgeVariant; label: string } {
  if (s.status === 'live') {
    return isUnderperforming(s)
      ? { variant: 'yellow', label: '⚠ Underperforming' }
      // TODO: no simulation stage field on GET /strategies — showing generic label
      : { variant: 'green',  label: 'Live Approved' }
  }
  if (s.status === 'paper')    return { variant: 'blue',   label: 'Paper / 30d req.' }
  if (s.status === 'research') return { variant: 'purple', label: 'Simulation only'  }
  return { variant: 'gray', label: 'Off' }
}

type BtnStyle = 'ghost' | 'primary' | 'danger'

function actionButtons(s: ApiStrategyListItem): { label: string; style: BtnStyle }[] {
  const detail = { label: 'Detail', style: 'ghost' as BtnStyle }
  if (s.status === 'live') {
    return isUnderperforming(s)
      ? [detail, { label: 'Demote',  style: 'danger'  }]
      : [detail, { label: 'Pause',   style: 'ghost'   }]
  }
  if (s.status === 'paper')    return [detail, { label: 'Promote', style: 'primary' }]
  if (s.status === 'research') return [detail, { label: 'Reject',  style: 'ghost'   }]
  return [detail]
}

// ── Sparkline SVG ─────────────────────────────────────────────────────────────

// TODO: no sparkline/equity-history endpoint for individual strategies — showing flat placeholder
const FLAT_SPARKLINE = Array.from<number>({ length: 12 }, () => 18)

function SparklineChart({
  strategy,
  selected,
}: {
  strategy: ApiStrategyListItem
  selected: boolean
}) {
  const data = FLAT_SPARKLINE
  const W = 200
  const H = 36
  const n = data.length
  const step = W / (n - 1)
  const hex = sparklineHex(strategy)
  const gradId = `sp-${strategy.strategy_id}`

  const linePts = data.map((y, i) => `${(i * step).toFixed(1)},${y}`).join(' L')
  const linePath = `M${linePts}`
  const fillPath = `${linePath} L${W},${H} L0,${H} Z`

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 36 }}>
      {selected && (
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"   stopColor={hex} stopOpacity={0.3} />
            <stop offset="100%" stopColor={hex} stopOpacity={0}   />
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
  strategy: ApiStrategyListItem
  selected: boolean
  onToggle: (id: string) => void
}) {
  const hb   = headerBadge(s)
  const bb   = bottomBadge(s)
  const btns = actionButtons(s)
  const isResearch = s.status === 'research'

  const returnPct  = Number(s.current_return) * 100
  const maxDDPct   = Math.abs(Number(s.max_drawdown)) * 100
  const winRatePct = Math.round(Number(s.win_rate) * 100)

  const row1 = [
    {
      label: 'Sharpe',
      value: Number(s.sharpe_ratio).toFixed(2),
      color: sharpeColor(Number(s.sharpe_ratio), s.status),
    },
    {
      label: 'Return',
      value: `${returnPct >= 0 ? '+' : ''}${returnPct.toFixed(1)}%`,
      color: returnColor(Number(s.current_return), s.status),
    },
    {
      label: 'Max DD',
      value: `-${maxDDPct.toFixed(1)}%`,
      color: maxDDColor(Number(s.max_drawdown)),
    },
  ]

  const row2 = isResearch
    ? [
        // TODO: no simulation stage field on GET /strategies
        { label: 'Stage',   value: '—'                    },
        { label: 'Runs',    value: String(s.trade_count)  },
        // TODO: no active dataset version from GET /strategies
        { label: 'Dataset', value: '—'                    },
      ]
    : [
        { label: 'Win Rate',     value: `${winRatePct}%`          },
        { label: 'Trades (30d)', value: String(s.trade_count)      },
        // TODO: no allocation field on GET /strategies (only available on GET /strategies/active)
        { label: 'Allocation',   value: '—'                        },
      ]

  return (
    <div
      className="rounded-md p-4 cursor-pointer transition-all duration-150"
      style={{
        background:   selected ? 'var(--accent-dim)' : 'var(--surface2)',
        border:       `1px solid ${selected ? 'var(--accent)' : 'var(--border)'}`,
        borderRadius: 6,
      }}
      onMouseEnter={e => {
        if (!selected) (e.currentTarget as HTMLDivElement).style.borderColor = 'var(--border2)'
      }}
      onMouseLeave={e => {
        if (!selected) (e.currentTarget as HTMLDivElement).style.borderColor = 'var(--border)'
      }}
      onClick={() => onToggle(s.strategy_id)}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-1">
        <span className="text-[13px] font-medium text-[var(--text)]">{s.display_name}</span>
        <StatusBadge variant={hb.variant}>{hb.label}</StatusBadge>
      </div>

      {/* ID + type */}
      <div className="font-mono text-[10px] text-[var(--text3)] mb-3">
        {s.strategy_id} · {s.strategy_type} · Equity
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
              style={{ fontSize: isResearch ? 12 : 14 }}
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
        <div className="flex gap-2" onClick={e => e.stopPropagation()}>
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

// ── Skeleton card ─────────────────────────────────────────────────────────────

function SkeletonCard() {
  return (
    <div
      className="rounded-md p-4 animate-pulse"
      style={{ background: 'var(--surface2)', border: '1px solid var(--border)', minHeight: 220 }}
    >
      <div className="flex justify-between items-center mb-1">
        <div className="h-4 rounded w-1/2" style={{ background: 'var(--border2)' }} />
        <div className="h-4 rounded w-12" style={{ background: 'var(--border2)' }} />
      </div>
      <div className="h-3 rounded w-1/3 mb-4" style={{ background: 'var(--border2)' }} />
      <div className="grid grid-cols-3 gap-2 mb-3">
        {[...Array(3)].map((_, i) => (
          <div key={i} className="h-8 rounded" style={{ background: 'var(--border)' }} />
        ))}
      </div>
      <div className="grid grid-cols-3 gap-2 mb-3">
        {[...Array(3)].map((_, i) => (
          <div key={i} className="h-8 rounded" style={{ background: 'var(--border)' }} />
        ))}
      </div>
      <div className="h-9 rounded mb-2" style={{ background: 'var(--border)' }} />
      <div className="flex justify-between">
        <div className="h-5 rounded w-20" style={{ background: 'var(--border2)' }} />
        <div className="h-5 rounded w-24" style={{ background: 'var(--border2)' }} />
      </div>
    </div>
  )
}

// ── Comparison table ──────────────────────────────────────────────────────────

function govLabel(s: ApiStrategyListItem): { variant: BadgeVariant; label: string } {
  if (s.status === 'live') {
    return isUnderperforming(s)
      ? { variant: 'yellow', label: 'Under Review'   }
      : { variant: 'green',  label: 'Live Approved'  }
  }
  if (s.status === 'paper')    return { variant: 'blue',   label: 'Paper Approved' }
  if (s.status === 'research') return { variant: 'purple', label: 'Research'       }
  return { variant: 'gray', label: 'Off' }
}

function ComparisonTable({
  strategies,
  selectedIds,
}: {
  strategies: ApiStrategyListItem[]
  selectedIds: Set<string>
}) {
  const selected = strategies.filter(s => selectedIds.has(s.strategy_id))

  const title =
    selected.length === 0
      ? 'Strategy Comparison'
      : selected.length === 1
      ? `Strategy Comparison — ${selected[0].display_name}`
      : `Strategy Comparison — ${selected.map(s => s.display_name).join(' vs ')}`

  const tdBorder: React.CSSProperties = { borderBottom: '1px solid var(--border)' }

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
                  style={{ width: 160, ...tdBorder }}
                >
                  Metric
                </th>
                {selected.map(s => (
                  <th
                    key={s.strategy_id}
                    className="font-mono text-[9px] font-medium text-[var(--text3)] uppercase tracking-[0.1em] text-left pb-2.5 pr-4"
                    style={tdBorder}
                  >
                    {s.display_name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {/* Sharpe */}
              <tr>
                <td className="font-mono text-[11px] text-[var(--text2)] py-2.5 pr-4" style={tdBorder}>
                  Sharpe Ratio
                </td>
                {selected.map(s => (
                  <td
                    key={s.strategy_id}
                    className="font-mono text-[13px] py-2.5 pr-4"
                    style={{ color: sharpeColor(Number(s.sharpe_ratio), s.status), ...tdBorder }}
                  >
                    {Number(s.sharpe_ratio).toFixed(2)}
                  </td>
                ))}
              </tr>

              {/* Return — replaces CAGR; GET /strategies has current_return, not annualised CAGR */}
              <tr>
                <td className="font-mono text-[11px] text-[var(--text2)] py-2.5 pr-4" style={tdBorder}>
                  Return
                </td>
                {selected.map(s => {
                  const pct = Number(s.current_return) * 100
                  return (
                    <td
                      key={s.strategy_id}
                      className="font-mono text-[13px] py-2.5 pr-4"
                      style={{ color: returnColor(Number(s.current_return), s.status), ...tdBorder }}
                    >
                      {pct >= 0 ? '+' : ''}{pct.toFixed(1)}%
                    </td>
                  )
                })}
              </tr>

              {/* Max Drawdown */}
              <tr>
                <td className="font-mono text-[11px] text-[var(--text2)] py-2.5 pr-4" style={tdBorder}>
                  Max Drawdown
                </td>
                {selected.map(s => (
                  <td
                    key={s.strategy_id}
                    className="font-mono text-[13px] py-2.5 pr-4"
                    style={{ color: maxDDColor(Number(s.max_drawdown)), ...tdBorder }}
                  >
                    -{(Math.abs(Number(s.max_drawdown)) * 100).toFixed(1)}%
                  </td>
                ))}
              </tr>

              {/* Win Rate */}
              <tr>
                <td className="font-mono text-[11px] text-[var(--text2)] py-2.5 pr-4" style={tdBorder}>
                  Win Rate
                </td>
                {selected.map(s => (
                  <td
                    key={s.strategy_id}
                    className="font-mono text-[13px] text-[var(--text)] py-2.5 pr-4"
                    style={tdBorder}
                  >
                    {Math.round(Number(s.win_rate) * 100)}%
                  </td>
                ))}
              </tr>

              {/* Simulation Stage — deferred */}
              <tr>
                <td className="font-mono text-[11px] text-[var(--text2)] py-2.5 pr-4" style={tdBorder}>
                  Simulation Stage
                </td>
                {selected.map(s => (
                  // TODO: no simulation stage field on GET /strategies
                  <td
                    key={s.strategy_id}
                    className="font-mono text-[13px] text-[var(--text3)] py-2.5 pr-4"
                    style={tdBorder}
                  >
                    —
                  </td>
                ))}
              </tr>

              {/* Governance State */}
              <tr>
                <td className="font-mono text-[11px] text-[var(--text2)] py-2.5 pr-4">
                  Governance State
                </td>
                {selected.map(s => {
                  const gl = govLabel(s)
                  return (
                    <td key={s.strategy_id} className="py-2.5 pr-4">
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

type FilterKey = 'all' | ApiStatus

const FILTERS: { key: FilterKey; label: string; variant: BadgeVariant; dot?: boolean }[] = [
  { key: 'all',      label: 'All',      variant: 'gray'             },
  { key: 'live',     label: 'Live',     variant: 'green', dot: true },
  { key: 'paper',    label: 'Paper',    variant: 'blue'             },
  { key: 'research', label: 'Research', variant: 'purple'           },
  { key: 'off',      label: 'Off',      variant: 'gray'             },
]

// ── StrategyLab ───────────────────────────────────────────────────────────────

export default function StrategyLab() {
  const [filter,   setFilter]   = useState<FilterKey>('all')
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const { data: strategies = [], isLoading, isError } = useQuery({
    queryKey: ['strategies'],
    queryFn: fetchAllStrategies,
  })

  function toggleSelected(id: string) {
    setSelected(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const visible = filter === 'all'
    ? strategies
    : strategies.filter(s => s.status === filter)

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
        {isLoading && [...Array(6)].map((_, i) => <SkeletonCard key={i} />)}

        {isError && (
          <div
            className="col-span-3 font-mono text-[11px] p-6 text-center rounded-lg"
            style={{ color: 'var(--red)', background: 'var(--surface)', border: '1px solid var(--border)' }}
          >
            Failed to load strategies. Check backend connection.
          </div>
        )}

        {!isLoading && !isError && visible.length === 0 && (
          <div
            className="col-span-3 font-mono text-[11px] p-6 text-center rounded-lg"
            style={{ color: 'var(--text3)', background: 'var(--surface)', border: '1px solid var(--border)' }}
          >
            No strategies match this filter.
          </div>
        )}

        {!isLoading && !isError && visible.map(s => (
          <StrategyCard
            key={s.strategy_id}
            strategy={s}
            selected={selected.has(s.strategy_id)}
            onToggle={toggleSelected}
          />
        ))}

        {/* Placeholder card — only shown when filter is 'all' and data loaded */}
        {!isLoading && !isError && filter === 'all' && (
          <div
            className="flex flex-col items-center justify-center rounded-md p-4 cursor-pointer opacity-50"
            style={{
              minHeight: 200,
              background:   'var(--surface2)',
              border:       '1px dashed var(--border)',
              borderRadius: 6,
            }}
          >
            <div className="text-[28px] text-[var(--text3)] mb-2">+</div>
            <div className="font-mono text-[10px] text-[var(--text3)] text-center">Run New Experiment</div>
          </div>
        )}
      </div>

      {/* Comparison table */}
      {!isLoading && !isError && (
        <ComparisonTable strategies={strategies} selectedIds={selected} />
      )}
    </div>
  )
}
