import { useState } from 'react'
import StatusBadge from '../components/shared/StatusBadge'
import type { BadgeVariant } from '../components/shared/StatusBadge'
import Toggle from '../components/shared/Toggle'
import { cn } from '../lib/utils'
import { useAppStore } from '../store/useAppStore'
import { mockStrategies, mockAuditLog } from '../mock/data'
import type { Strategy, Environment } from '../types'

// ── Static page data ──────────────────────────────────────────────────────────

// Override amounts only — names and policy allocations come from mockStrategies
const OVERRIDE_AMOUNTS: Record<string, number | null> = {
  'STR-00142': null,
  'STR-00098': null,
  'STR-00201': 12_000,
  'STR-00187': null,
}

const ALLOC_OVERRIDE_IDS = ['STR-00142', 'STR-00098', 'STR-00201', 'STR-00187']

const ALLOC_OVERRIDES = ALLOC_OVERRIDE_IDS.map(id => {
  const s = mockStrategies.find(x => x.id === id)!
  return { id, name: s.name, policy: s.allocation ?? 0, override: OVERRIDE_AMOUNTS[id] ?? null }
})

const ALERT_THRESHOLDS = [
  { label: 'Drawdown alert',     desc: 'Notify when portfolio DD > 8%',       status: 'active' as const },
  { label: 'Sharpe degradation', desc: 'Alert if Sharpe drops below 0.5',      status: 'active' as const },
  { label: 'VaR breach',         desc: 'Alert when daily VaR exceeds $5,000',  status: 'firing' as const },
  { label: 'Pipeline delay',     desc: 'Feature pipeline > 5min late',          status: 'active' as const },
]

const ENV_MODES: { key: Environment; label: string; variant: BadgeVariant; suffix?: string }[] = [
  { key: 'simulation', label: 'Simulation', variant: 'gray'  },
  { key: 'paper',      label: 'Paper',      variant: 'blue'  },
  { key: 'live',       label: 'Live',       variant: 'green', suffix: ' ●' },
]

// ── Helpers ───────────────────────────────────────────────────────────────────

function toggleBadge(s: Strategy, enabled: boolean): { variant: BadgeVariant; label: string } {
  if (!enabled) return { variant: 'gray', label: 'Paused' }
  if (s.governanceState === 'paper') return { variant: 'blue', label: 'Paper' }
  if (s.governanceState === 'live')
    return s.sharpe < 1.0
      ? { variant: 'yellow', label: 'Warning' }
      : { variant: 'green',  label: 'Running' }
  return { variant: 'gray', label: 'Idle' }
}

function subInfo(s: Strategy, enabled: boolean): string {
  const mode = s.governanceState === 'live' ? 'Live' : 'Paper'

  if (!enabled) return `${mode} · Paused by operator`

  const allocStr = s.allocation != null
    ? `$${s.allocation.toLocaleString()} ${s.governanceState === 'live' ? 'allocated' : 'notional'}`
    : null

  const base = allocStr ? `${mode} · ${allocStr}` : mode
  return s.id === 'STR-00201' ? `${base} · ⚠ DD breach` : base
}

// ── Shared card primitives ────────────────────────────────────────────────────

function Card({
  children,
  className,
  style,
}: {
  children: React.ReactNode
  className?: string
  style?: React.CSSProperties
}) {
  return (
    <div
      className={cn('rounded-lg p-5', className)}
      style={{ background: 'var(--surface)', border: '1px solid var(--border)', ...style }}
    >
      {children}
    </div>
  )
}

function CardTitle({ children, color }: { children: React.ReactNode; color?: string }) {
  return (
    <div
      className="font-mono text-[11px] font-medium uppercase tracking-[0.08em] mb-4 pb-3"
      style={{ color: color ?? 'var(--text2)', borderBottom: '1px solid var(--border)' }}
    >
      {children}
    </div>
  )
}

function Row({
  children,
  last = false,
}: {
  children: React.ReactNode
  last?: boolean
}) {
  return (
    <div
      className="flex items-center justify-between py-2.5"
      style={{ borderBottom: last ? 'none' : '1px solid var(--border)' }}
    >
      {children}
    </div>
  )
}

// ── Kill Switch card ──────────────────────────────────────────────────────────

function KillSwitchCard() {
  const { killSwitchActive, toggleKillSwitch } = useAppStore()

  return (
    <Card style={{ border: `1px solid ${killSwitchActive ? 'var(--red)' : 'rgba(255,77,109,0.3)'}` }}>
      <CardTitle color="var(--red)">Global Kill Switch</CardTitle>

      <p className="font-mono text-[12px] text-[var(--text2)] mb-4">
        Immediately halts all order submission across all strategies and environments.
        Positions are not liquidated — only new orders are blocked.
      </p>

      <div className="flex items-center justify-between">
        <div>
          <div className="text-[13px] text-[var(--text)] mb-0.5">System Status</div>
          <div className="flex items-center gap-2">
            <span
              className={cn('w-1.5 h-1.5 rounded-full shrink-0', !killSwitchActive && 'animate-pulse')}
              style={{ background: killSwitchActive ? 'var(--red)' : 'var(--accent)' }}
            />
            <span
              className="font-mono text-[11px]"
              style={{ color: killSwitchActive ? 'var(--red)' : 'var(--accent)' }}
            >
              {killSwitchActive ? 'HALTED — All Orders Blocked' : 'Active — Trading Enabled'}
            </span>
          </div>
        </div>

        <button
          onClick={toggleKillSwitch}
          className="font-mono text-[11px] font-medium uppercase tracking-[0.08em] rounded cursor-pointer border transition-all duration-150"
          style={{
            padding: '10px 20px',
            background:   killSwitchActive ? 'transparent'       : 'var(--red-dim)',
            color:        killSwitchActive ? 'var(--accent)'      : 'var(--red)',
            borderColor:  killSwitchActive ? 'var(--accent)'      : 'var(--red)',
          }}
        >
          {killSwitchActive ? '▶ Resume Trading' : '⬛ Kill All Trading'}
        </button>
      </div>
    </Card>
  )
}

// ── Strategy Toggles card ─────────────────────────────────────────────────────

function StrategyTogglesCard() {
  const [toggles, setToggles] = useState<Record<string, boolean>>(
    Object.fromEntries(mockStrategies.map(s => [s.id, s.enabled]))
  )

  function flip(id: string) {
    setToggles(prev => ({ ...prev, [id]: !prev[id] }))
  }

  return (
    <Card>
      <CardTitle>Strategy Toggles</CardTitle>
      {mockStrategies.map((s, i) => {
        const enabled = toggles[s.id] ?? false
        const badge   = toggleBadge(s, enabled)
        const info    = subInfo(s, enabled)
        return (
          <Row key={s.id} last={i === mockStrategies.length - 1}>
            <div>
              <div className="text-[13px] text-[var(--text)]">{s.name}</div>
              <div className="font-mono text-[11px] text-[var(--text2)] mt-0.5">{info}</div>
            </div>
            <div className="flex items-center gap-3">
              <StatusBadge variant={badge.variant}>{badge.label}</StatusBadge>
              <Toggle on={enabled} onChange={() => flip(s.id)} />
            </div>
          </Row>
        )
      })}
    </Card>
  )
}

// ── Environment card ──────────────────────────────────────────────────────────

function EnvironmentCard() {
  const { activeEnv, setActiveEnv } = useAppStore()
  const [autoPromote, setAutoPromote] = useState(false)
  const [riskChecks,  setRiskChecks]  = useState(true)

  return (
    <Card className="p-4">
      <CardTitle>Environment</CardTitle>

      {/* Trading mode */}
      <Row>
        <span className="text-[13px] text-[var(--text)]">Trading Mode</span>
        <div className="flex gap-2">
          {ENV_MODES.map(({ key, label, variant, suffix }) => {
            const active = activeEnv === key
            return (
              <button
                key={key}
                onClick={() => setActiveEnv(key)}
                className={cn('transition-opacity', !active && 'opacity-40 hover:opacity-70')}
              >
                <StatusBadge
                  variant={active ? variant : 'gray'}
                  className="cursor-pointer px-2.5 py-0.5"
                >
                  {label}{suffix ?? ''}
                </StatusBadge>
              </button>
            )
          })}
        </div>
      </Row>

      {/* Auto-promote */}
      <Row>
        <span className="text-[13px] text-[var(--text)]">Auto-promote enabled</span>
        <Toggle on={autoPromote} onChange={setAutoPromote} />
      </Row>

      {/* Risk checks */}
      <Row last>
        <span className="text-[13px] text-[var(--text)]">Risk checks</span>
        <Toggle on={riskChecks} onChange={setRiskChecks} />
      </Row>
    </Card>
  )
}

// ── Allocation Overrides card ─────────────────────────────────────────────────

function AllocationOverridesCard() {
  return (
    <Card>
      <CardTitle>Allocation Overrides</CardTitle>
      <p className="font-mono text-[11px] text-[var(--text2)] mb-3">
        Manual overrides take precedence over policy-computed allocations.
        All changes are logged.
      </p>

      {ALLOC_OVERRIDES.map((row, i) => {
        const hasOverride = row.override !== null
        return (
          <Row key={row.id} last={i === ALLOC_OVERRIDES.length - 1}>
            <div>
              <div className="text-[13px] text-[var(--text)]">{row.name}</div>
              <div
                className="font-mono text-[11px] mt-0.5"
                style={{ color: hasOverride ? 'var(--yellow)' : 'var(--text2)' }}
              >
                Policy: ${row.policy.toLocaleString()}
                {hasOverride && ' · Override active'}
              </div>
            </div>
            <StatusBadge variant={hasOverride ? 'yellow' : 'gray'} className="font-mono">
              {hasOverride ? `$${row.override!.toLocaleString()}` : 'No override'}
            </StatusBadge>
          </Row>
        )
      })}

      <div className="mt-4">
        <button
          className="w-full font-mono text-[10px] font-medium uppercase tracking-[0.08em] py-2 px-4 rounded cursor-pointer border transition-all duration-150"
          style={{
            background: 'transparent',
            color: 'var(--text2)',
            borderColor: 'var(--border2)',
          }}
          onMouseEnter={e => {
            ;(e.currentTarget as HTMLButtonElement).style.color = 'var(--text)'
            ;(e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--text3)'
          }}
          onMouseLeave={e => {
            ;(e.currentTarget as HTMLButtonElement).style.color = 'var(--text2)'
            ;(e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--border2)'
          }}
        >
          + Add Override
        </button>
      </div>
    </Card>
  )
}

// ── Alert Thresholds card ─────────────────────────────────────────────────────

function AlertThresholdsCard() {
  return (
    <Card>
      <CardTitle>Alert Thresholds</CardTitle>
      {ALERT_THRESHOLDS.map(({ label, desc, status }, i) => (
        <Row key={label} last={i === ALERT_THRESHOLDS.length - 1}>
          <div>
            <div className="text-[13px] text-[var(--text)]">{label}</div>
            <div className="font-mono text-[11px] text-[var(--text2)] mt-0.5">{desc}</div>
          </div>
          <StatusBadge variant={status === 'firing' ? 'yellow' : 'green'}>
            {status === 'firing' ? 'Firing' : 'Active'}
          </StatusBadge>
        </Row>
      ))}
    </Card>
  )
}

// ── Audit Log card ────────────────────────────────────────────────────────────

function AuditLogCard() {
  return (
    <Card style={{ height: '100%' }}>
      <CardTitle>Audit Log</CardTitle>
      {mockAuditLog.map((entry, i) => (
        <div
          key={entry.id}
          className="flex items-start gap-3 py-2.5"
          style={{ borderBottom: i < mockAuditLog.length - 1 ? '1px solid var(--border)' : 'none' }}
        >
          <span
            className="font-mono text-[10px] text-[var(--text3)] shrink-0 pt-px"
            style={{ width: 72 }}
          >
            {entry.time}
          </span>
          <div className="flex-1 min-w-0">
            <div className="text-[12px] text-[var(--text)]">{entry.text}</div>
            <div className="font-mono text-[10px] text-[var(--text2)] mt-0.5">{entry.actor}</div>
          </div>
        </div>
      ))}
    </Card>
  )
}

// ── Controls ──────────────────────────────────────────────────────────────────

export default function Controls() {
  return (
    <div className="p-6">
      {/* Warning banner */}
      <div
        className="flex items-center gap-3 px-4 py-2.5 rounded-md font-mono text-[11px] mb-4"
        style={{
          background:  'var(--yellow-dim)',
          border:      '1px solid rgba(232,168,56,0.3)',
          color:       'var(--yellow)',
        }}
      >
        ⚠&nbsp; Mean Reversion (STR-00201) has breached the -12% drawdown threshold.
        Review recommended.
      </div>

      {/* 3-column grid (1.4 : 1 : 1) */}
      <div className="grid gap-4" style={{ gridTemplateColumns: '1.4fr 1fr 1fr', alignItems: 'start' }}>
        {/* Left column */}
        <div className="flex flex-col gap-4">
          <KillSwitchCard />
          <StrategyTogglesCard />
          <EnvironmentCard />
        </div>

        {/* Middle column */}
        <div className="flex flex-col gap-4">
          <AllocationOverridesCard />
          <AlertThresholdsCard />
        </div>

        {/* Right column */}
        <AuditLogCard />
      </div>
    </div>
  )
}
