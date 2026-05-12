import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { format, isToday, isYesterday, parseISO } from 'date-fns'
import StatusBadge from '../components/shared/StatusBadge'
import type { BadgeVariant } from '../components/shared/StatusBadge'
import Toggle from '../components/shared/Toggle'
import { cn } from '../lib/utils'
import { useAppStore } from '../store/useAppStore'
import { fetchSystemHealth, updateTradingMode } from '../services/systemService'
import {
  fetchControlsState,
  activateKillSwitch,
  resumeTrading,
  type ApiStrategyControlState,
} from '../services/controlsService'
import {
  fetchAllStrategies,
  updateStrategyEnabled,
  type ApiStrategyListItem,
} from '../services/strategiesService'
import { fetchAuditLog, type ApiAuditLogEvent } from '../services/auditLogService'
import { fetchOperatorSettings, updateOperatorSettings } from '../services/settingsService'
import type { Environment } from '../types'

// ── Card primitives (unchanged from original) ─────────────────────────────────

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

// ── Shared loading/error states ───────────────────────────────────────────────

function CardSkeleton({ title, color }: { title?: string; color?: string }) {
  return (
    <Card>
      {title && <CardTitle color={color}>{title}</CardTitle>}
      <div className="animate-pulse space-y-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-3 rounded" style={{ background: 'var(--border2)' }} />
        ))}
      </div>
    </Card>
  )
}

function CardError({ title, message, color }: { title?: string; message: string; color?: string }) {
  return (
    <Card>
      {title && <CardTitle color={color}>{title}</CardTitle>}
      <span className="font-mono text-[11px]" style={{ color: 'var(--red)' }}>
        Failed to load: {message}
      </span>
    </Card>
  )
}

// ── Inline reason input ───────────────────────────────────────────────────────

function ReasonInput({
  value,
  onChange,
  placeholder = 'Reason (required)',
}: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
}) {
  return (
    <input
      autoFocus
      className="w-full px-3 py-1.5 rounded font-mono text-[11px] outline-none"
      style={{
        background: 'var(--surface2)',
        border: '1px solid var(--border2)',
        color: 'var(--text)',
      }}
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
      onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--border2)')}
    />
  )
}

function ConfirmButton({
  onClick,
  disabled,
  children,
}: {
  onClick: () => void
  disabled?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="font-mono text-[10px] font-medium uppercase tracking-[0.08em] px-3 py-1.5 rounded border cursor-pointer transition-opacity disabled:opacity-40 disabled:cursor-not-allowed"
      style={{
        background: 'var(--accent-dim2)',
        color: 'var(--accent)',
        borderColor: 'rgba(0,229,160,0.3)',
      }}
    >
      {children}
    </button>
  )
}

function CancelButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="font-mono text-[10px] font-medium uppercase tracking-[0.08em] px-3 py-1.5 rounded border cursor-pointer transition-colors"
      style={{ background: 'transparent', color: 'var(--text2)', borderColor: 'var(--border2)' }}
    >
      Cancel
    </button>
  )
}

// ── Audit log helpers ─────────────────────────────────────────────────────────

function fmtAuditTime(iso: string): string {
  try {
    const d = parseISO(iso)
    if (isToday(d)) return format(d, 'HH:mm:ss')
    if (isYesterday(d)) return 'Yesterday'
    return format(d, 'MMM d')
  } catch {
    return iso
  }
}

function fmtAuditActor(event: ApiAuditLogEvent): string {
  const parts: string[] = []
  if (event.user) parts.push(event.user)
  if (event.rationale) parts.push(`reason: ${event.rationale}`)
  return parts.join(' · ') || 'system'
}

// ── Strategy badge helpers ────────────────────────────────────────────────────

function strategyBadge(
  status: ApiStrategyControlState['status'],
  enabled: boolean,
): { variant: BadgeVariant; label: string } {
  if (!enabled) return { variant: 'gray', label: 'Paused' }
  if (status === 'live') return { variant: 'green', label: 'Running' }
  if (status === 'paper') return { variant: 'blue', label: 'Paper' }
  return { variant: 'gray', label: 'Off' }
}

function strategySubInfo(
  status: ApiStrategyControlState['status'],
  enabled: boolean,
  reason: string | null,
): string {
  const modeLabel = status === 'live' ? 'Live' : status === 'paper' ? 'Paper' : 'Off'
  if (!enabled) return `${modeLabel} · Paused by operator`
  if (reason) return `${modeLabel} · ${reason}`
  return modeLabel
}

// ── Kill Switch card ──────────────────────────────────────────────────────────

function KillSwitchCard() {
  const { setKillSwitchActive } = useAppStore()
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const [reason, setReason] = useState('')

  const { data: controlsState, isLoading, error } = useQuery({
    queryKey: ['controls', 'state'],
    queryFn: fetchControlsState,
    refetchInterval: 15_000,
  })

  const killMutation = useMutation({
    mutationFn: () => activateKillSwitch(reason),
    onSuccess: () => {
      setKillSwitchActive(true)
      setConfirming(false)
      setReason('')
      void queryClient.invalidateQueries({ queryKey: ['controls', 'state'] })
    },
  })

  const resumeMutation = useMutation({
    mutationFn: () => resumeTrading(reason),
    onSuccess: () => {
      setKillSwitchActive(false)
      setConfirming(false)
      setReason('')
      void queryClient.invalidateQueries({ queryKey: ['controls', 'state'] })
    },
  })

  if (isLoading) return <CardSkeleton title="Global Kill Switch" color="var(--red)" />
  if (error) return <CardError title="Global Kill Switch" message={(error as Error).message} color="var(--red)" />

  const killSwitchActive = controlsState?.kill_switch_active ?? false
  const isPending = killMutation.isPending || resumeMutation.isPending
  const mutationError = killMutation.error ?? resumeMutation.error

  return (
    <Card style={{ border: `1px solid ${killSwitchActive ? 'var(--red)' : 'rgba(255,77,109,0.3)'}` }}>
      <CardTitle color="var(--red)">Global Kill Switch</CardTitle>

      <p className="font-mono text-[12px] text-[var(--text2)] mb-4">
        Immediately halts all order submission across all strategies and environments.
        Positions are not liquidated — only new orders are blocked.
      </p>

      {confirming ? (
        <div className="space-y-2">
          <p className="font-mono text-[11px] text-[var(--text2)]">
            {killSwitchActive
              ? 'Provide rationale for resuming trading:'
              : 'Provide reason for activating kill switch:'}
          </p>
          <ReasonInput value={reason} onChange={setReason} />
          {mutationError && (
            <p className="font-mono text-[10px]" style={{ color: 'var(--red)' }}>
              {(mutationError as Error).message}
            </p>
          )}
          <div className="flex gap-2">
            <ConfirmButton
              disabled={!reason.trim() || isPending}
              onClick={() => killSwitchActive ? resumeMutation.mutate() : killMutation.mutate()}
            >
              {isPending ? 'Processing…' : 'Confirm'}
            </ConfirmButton>
            <CancelButton onClick={() => { setConfirming(false); setReason('') }} />
          </div>
        </div>
      ) : (
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
            onClick={() => setConfirming(true)}
            className="font-mono text-[11px] font-medium uppercase tracking-[0.08em] rounded cursor-pointer border transition-all duration-150"
            style={{
              padding: '10px 20px',
              background:  killSwitchActive ? 'transparent'  : 'var(--red-dim)',
              color:       killSwitchActive ? 'var(--accent)' : 'var(--red)',
              borderColor: killSwitchActive ? 'var(--accent)' : 'var(--red)',
            }}
          >
            {killSwitchActive ? '▶ Resume Trading' : '⬛ Kill All Trading'}
          </button>
        </div>
      )}

      {/* TODO: POST /controls/kill-switch activates halt. POST /controls/resume resumes paused state.
          There is no dedicated /controls/release-kill-switch endpoint. If kill_switch_active and
          trading_paused are separate state dimensions, resuming may not clear kill_switch_active.
          Backend dependency: add a release-kill-switch action or clarify resume semantics. */}
    </Card>
  )
}

// ── Strategy Toggles card ─────────────────────────────────────────────────────

function StrategyTogglesCard() {
  const queryClient = useQueryClient()
  const [pendingToggle, setPendingToggle] = useState<{ id: string; newEnabled: boolean } | null>(null)
  const [pendingReason, setPendingReason] = useState('')

  const { data: controlsState, isLoading: controlsLoading, error: controlsError } = useQuery({
    queryKey: ['controls', 'state'],
    queryFn: fetchControlsState,
  })

  const { data: catalog = [], isLoading: catalogLoading } = useQuery({
    queryKey: ['strategies', 'all'],
    queryFn: fetchAllStrategies,
  })

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled, reason }: { id: string; enabled: boolean; reason: string }) =>
      updateStrategyEnabled(id, enabled, reason),
    onSuccess: () => {
      setPendingToggle(null)
      setPendingReason('')
      void queryClient.invalidateQueries({ queryKey: ['controls', 'state'] })
      void queryClient.invalidateQueries({ queryKey: ['strategies', 'active'] })
    },
  })

  const isLoading = controlsLoading || catalogLoading

  if (isLoading) return <CardSkeleton title="Strategy Toggles" />
  if (controlsError) return <CardError title="Strategy Toggles" message={(controlsError as Error).message} />

  // Build catalog map for O(1) display-name lookup
  const catalogMap = new Map<string, ApiStrategyListItem>(
    catalog.map((c) => [c.strategy_id, c])
  )

  // Only show strategies that are in active governance (paper/live).
  // Controls state is the authoritative list — strategies not in it have no
  // governance row and cannot be enabled/disabled by the API (would 404).
  const merged = (controlsState?.strategies ?? []).map((s) => {
    const cat = catalogMap.get(s.strategy_id)
    return {
      strategy_id:  s.strategy_id,
      display_name: cat?.display_name ?? s.strategy_id,
      enabled:      s.enabled,
      status:       s.status,
      reason:       s.reason,
    }
  })

  function requestToggle(id: string, currentEnabled: boolean) {
    setPendingToggle({ id, newEnabled: !currentEnabled })
    setPendingReason('')
  }

  function cancelToggle() {
    setPendingToggle(null)
    setPendingReason('')
  }

  function confirmToggle() {
    if (!pendingToggle || !pendingReason.trim()) return
    toggleMutation.mutate({ id: pendingToggle.id, enabled: pendingToggle.newEnabled, reason: pendingReason })
  }

  return (
    <Card>
      <CardTitle>Strategy Toggles</CardTitle>

      {merged.length === 0 ? (
        <p className="font-mono text-[12px] text-[var(--text3)] text-center py-4">
          No strategies available
        </p>
      ) : (
        merged.map((s, i) => {
          const isLast = i === merged.length - 1
          const badge  = strategyBadge(s.status, s.enabled)
          const info   = strategySubInfo(s.status, s.enabled, s.reason)
          const isPendingThis = pendingToggle?.id === s.strategy_id

          if (isPendingThis) {
            return (
              <div
                key={s.strategy_id}
                className="py-3 space-y-2"
                style={{ borderBottom: isLast ? 'none' : '1px solid var(--border)' }}
              >
                <div className="text-[13px] text-[var(--text)]">
                  {s.display_name}
                  <span className="font-mono text-[11px] text-[var(--text3)] ml-2">
                    → {pendingToggle.newEnabled ? 'enable' : 'pause'}
                  </span>
                </div>
                <ReasonInput value={pendingReason} onChange={setPendingReason} />
                {toggleMutation.error && (
                  <p className="font-mono text-[10px]" style={{ color: 'var(--red)' }}>
                    {(toggleMutation.error as Error).message}
                  </p>
                )}
                <div className="flex gap-2">
                  <ConfirmButton
                    disabled={!pendingReason.trim() || toggleMutation.isPending}
                    onClick={confirmToggle}
                  >
                    {toggleMutation.isPending ? 'Applying…' : 'Apply'}
                  </ConfirmButton>
                  <CancelButton onClick={cancelToggle} />
                </div>
              </div>
            )
          }

          return (
            <Row key={s.strategy_id} last={isLast}>
              <div>
                <div className="text-[13px] text-[var(--text)]">{s.display_name}</div>
                <div className="font-mono text-[11px] text-[var(--text2)] mt-0.5">{info}</div>
              </div>
              <div className="flex items-center gap-3">
                <StatusBadge variant={badge.variant}>{badge.label}</StatusBadge>
                <Toggle
                  on={s.enabled}
                  disabled={toggleMutation.isPending}
                  onChange={() => requestToggle(s.strategy_id, s.enabled)}
                />
              </div>
            </Row>
          )
        })
      )}
    </Card>
  )
}

// ── Environment card ──────────────────────────────────────────────────────────

const ENV_MODES: { key: Environment; label: string; variant: BadgeVariant; suffix?: string }[] = [
  { key: 'simulation', label: 'Simulation', variant: 'gray'  },
  { key: 'paper',      label: 'Paper',      variant: 'blue'  },
  { key: 'live',       label: 'Live',       variant: 'green', suffix: ' ●' },
]

function EnvironmentCard() {
  const { setActiveEnv } = useAppStore()
  const queryClient = useQueryClient()
  const [pendingMode, setPendingMode] = useState<Environment | null>(null)
  const [modeReason, setModeReason] = useState('')

  const { data: controlsState } = useQuery({
    queryKey: ['controls', 'state'],
    queryFn: fetchControlsState,
  })

  const { data: settings, isLoading: settingsLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: fetchOperatorSettings,
  })

  const modeMutation = useMutation({
    mutationFn: ({ mode, rationale }: { mode: Environment; rationale: string }) =>
      updateTradingMode(mode, rationale),
    onSuccess: (_data, { mode }) => {
      setActiveEnv(mode)
      setPendingMode(null)
      setModeReason('')
      void queryClient.invalidateQueries({ queryKey: ['controls', 'state'] })
    },
  })

  const autoPromoteMutation = useMutation({
    mutationFn: (enabled: boolean) => updateOperatorSettings({ auto_promote_enabled: enabled }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['settings'] })
    },
  })

  const currentMode = (controlsState?.trading_mode ?? 'paper') as Environment

  function selectMode(mode: Environment) {
    if (mode === currentMode) return
    setPendingMode(mode)
    setModeReason('')
  }

  function confirmMode() {
    if (!pendingMode || !modeReason.trim()) return
    modeMutation.mutate({ mode: pendingMode, rationale: modeReason })
  }

  return (
    <Card className="p-4">
      <CardTitle>Environment</CardTitle>

      {/* Trading Mode */}
      <Row>
        <span className="text-[13px] text-[var(--text)]">Trading Mode</span>
        <div className="flex gap-2">
          {ENV_MODES.map(({ key, label, variant, suffix }) => {
            const active = currentMode === key
            return (
              <button
                key={key}
                onClick={() => selectMode(key)}
                disabled={modeMutation.isPending}
                className={cn('transition-opacity disabled:cursor-not-allowed', !active && 'opacity-40 hover:opacity-70')}
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

      {/* Inline mode-change confirm */}
      {pendingMode && pendingMode !== currentMode && (
        <div className="py-2 space-y-2" style={{ borderBottom: '1px solid var(--border)' }}>
          <p className="font-mono text-[11px] text-[var(--text2)]">
            Rationale for switching to{' '}
            <span style={{ color: 'var(--text)' }}>{pendingMode}</span>:
          </p>
          <ReasonInput
            value={modeReason}
            onChange={setModeReason}
            placeholder="Rationale (required)"
          />
          {modeMutation.error && (
            <p className="font-mono text-[10px]" style={{ color: 'var(--red)' }}>
              {(modeMutation.error as Error).message}
            </p>
          )}
          <div className="flex gap-2">
            <ConfirmButton
              disabled={!modeReason.trim() || modeMutation.isPending}
              onClick={confirmMode}
            >
              {modeMutation.isPending ? 'Switching…' : 'Confirm'}
            </ConfirmButton>
            <CancelButton onClick={() => { setPendingMode(null); setModeReason('') }} />
          </div>
        </div>
      )}

      {/* Auto-promote */}
      <Row>
        <span className="text-[13px] text-[var(--text)]">Auto-promote enabled</span>
        {settingsLoading ? (
          <div className="w-9 h-5 rounded-[10px] animate-pulse" style={{ background: 'var(--border2)' }} />
        ) : (
          <Toggle
            on={settings?.auto_promote_enabled ?? false}
            disabled={autoPromoteMutation.isPending}
            onChange={(on) => autoPromoteMutation.mutate(on)}
          />
        )}
      </Row>

      {/* Risk checks — deferred */}
      <Row last>
        <div>
          <span className="text-[13px] text-[var(--text)]">Risk checks</span>
          {/* TODO: No backend support for risk_checks_enabled.
              OperatorSettingsResponse does not expose a risk-checks toggle.
              Backend dependency: add risk_checks_enabled field to operator_settings table,
              OperatorSettingsResponse, and OperatorSettingsUpdateRequest. */}
        </div>
        <Toggle on disabled />
      </Row>
    </Card>
  )
}

// ── Allocation Overrides card (deferred) ──────────────────────────────────────

function AllocationOverridesCard() {
  // TODO: No backend endpoint exposes current allocation override state.
  // GET /api/v1/portfolio/allocation returns live allocations (by_strategy) but does not
  // distinguish between policy-computed allocations and operator overrides.
  // PUT /api/v1/strategies/{strategy_id}/allocation exists for writing overrides.
  // Backend dependency: add a GET /api/v1/strategies/{id}/allocation endpoint (or extend
  // portfolio/allocation response) that returns { policy_allocation, override_allocation, is_overridden }
  // per strategy before this card can be wired.
  return (
    <Card>
      <CardTitle>Allocation Overrides</CardTitle>
      <p className="font-mono text-[11px] text-[var(--text2)] mb-3">
        Manual overrides take precedence over policy-computed allocations.
        All changes are logged.
      </p>
      <div
        className="rounded-md px-4 py-6 text-center"
        style={{ background: 'var(--surface2)', border: '1px solid var(--border)' }}
      >
        <p className="font-mono text-[11px] text-[var(--text3)]">
          Allocation override data not yet available.
        </p>
        <p className="font-mono text-[10px] text-[var(--text3)] mt-1">
          Awaiting GET endpoint for per-strategy override state.
        </p>
      </div>
      <div className="mt-4">
        <button
          disabled
          className="w-full font-mono text-[10px] font-medium uppercase tracking-[0.08em] py-2 px-4 rounded border opacity-40 cursor-not-allowed"
          style={{ background: 'transparent', color: 'var(--text2)', borderColor: 'var(--border2)' }}
        >
          + Add Override
        </button>
      </div>
    </Card>
  )
}

// ── Alert Thresholds card (deferred) ──────────────────────────────────────────

function AlertThresholdsCard() {
  // TODO: No backend endpoint for alert threshold configuration.
  // GET /api/v1/system/health returns active alerts (alerts: string[]) but not threshold definitions.
  // Backend dependency: a new /api/v1/alerts/thresholds endpoint (or settings extension) returning
  // [{ name, description, threshold_value, threshold_unit, status: "active"|"firing"|"disabled" }]
  // is required before this card can be wired.
  return (
    <Card>
      <CardTitle>Alert Thresholds</CardTitle>
      <div
        className="rounded-md px-4 py-6 text-center"
        style={{ background: 'var(--surface2)', border: '1px solid var(--border)' }}
      >
        <p className="font-mono text-[11px] text-[var(--text3)]">
          Alert threshold configuration not yet available.
        </p>
        <p className="font-mono text-[10px] text-[var(--text3)] mt-1">
          Awaiting GET /alerts/thresholds endpoint.
        </p>
      </div>
    </Card>
  )
}

// ── Audit Log card ────────────────────────────────────────────────────────────

function AuditLogCard() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['audit-log'],
    queryFn: () => fetchAuditLog(1, 20),
    refetchInterval: 30_000,
  })

  if (isLoading) return <CardSkeleton title="Audit Log" style={{ height: '100%' } as React.CSSProperties} />
  if (error) return <CardError title="Audit Log" message={(error as Error).message} />

  const events = data?.events ?? []

  return (
    <Card style={{ height: '100%' }}>
      <CardTitle>Audit Log</CardTitle>

      {events.length === 0 ? (
        <p className="font-mono text-[12px] text-[var(--text3)] text-center py-4">
          No audit events
        </p>
      ) : (
        events.map((entry, i) => (
          <div
            key={entry.event_id}
            className="flex items-start gap-3 py-2.5"
            style={{ borderBottom: i < events.length - 1 ? '1px solid var(--border)' : 'none' }}
          >
            <span
              className="font-mono text-[10px] text-[var(--text3)] shrink-0 pt-px"
              style={{ width: 72 }}
            >
              {fmtAuditTime(entry.timestamp)}
            </span>
            <div className="flex-1 min-w-0">
              <div className="text-[12px] text-[var(--text)]">{entry.description}</div>
              <div className="font-mono text-[10px] text-[var(--text2)] mt-0.5">
                {fmtAuditActor(entry)}
              </div>
            </div>
          </div>
        ))
      )}
    </Card>
  )
}

// ── Controls ──────────────────────────────────────────────────────────────────

export default function Controls() {
  const { data: health } = useQuery({
    queryKey: ['system', 'health'],
    queryFn: fetchSystemHealth,
    refetchInterval: 30_000,
  })

  const alerts = health?.alerts ?? []

  return (
    <div className="p-6">
      {/* Warning banner — driven by system health alerts */}
      {alerts.length > 0 && (
        <div
          className="flex items-center gap-3 px-4 py-2.5 rounded-md font-mono text-[11px] mb-4"
          style={{
            background: 'var(--yellow-dim)',
            border:     '1px solid rgba(232,168,56,0.3)',
            color:      'var(--yellow)',
          }}
        >
          ⚠&nbsp; {alerts[0]}
          {alerts.length > 1 && (
            <span className="ml-auto opacity-70">+{alerts.length - 1} more</span>
          )}
        </div>
      )}

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
