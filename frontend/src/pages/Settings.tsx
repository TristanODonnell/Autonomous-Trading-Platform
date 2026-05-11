import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { cn } from '../lib/utils'
import Toggle from '../components/shared/Toggle'
import StatusBadge from '../components/shared/StatusBadge'
import {
  fetchOperatorSettings,
  updateOperatorSettings,
  type ApiOperatorSettings,
  type ApiOperatorSettingsUpdate,
} from '../services'

// ── Helpers ───────────────────────────────────────────────────────────────────

const RISK_TO_NUM: Record<ApiOperatorSettings['risk_tolerance'], number> = {
  low: 1, medium: 2, high: 3,
}
const NUM_TO_RISK: Record<number, ApiOperatorSettings['risk_tolerance']> = {
  1: 'low', 2: 'medium', 3: 'high',
}
const FREQ_TO_UI: Record<ApiOperatorSettings['rebalance_frequency'], string> = {
  daily: 'Daily', weekly: 'Weekly', monthly: 'Monthly',
}
const UI_TO_FREQ: Record<string, ApiOperatorSettings['rebalance_frequency']> = {
  Daily: 'daily', Weekly: 'weekly', Monthly: 'monthly',
}

// ── Shared primitives ─────────────────────────────────────────────────────────

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="rounded-lg p-5"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      {children}
    </div>
  )
}

function CardTitle({
  children,
  action,
}: {
  children: React.ReactNode
  action?: React.ReactNode
}) {
  return (
    <div
      className="font-mono text-[11px] font-medium uppercase tracking-[0.08em] mb-4 pb-3 flex items-center justify-between"
      style={{ color: 'var(--text2)', borderBottom: '1px solid var(--border)' }}
    >
      <span>{children}</span>
      {action}
    </div>
  )
}

function SettingRow({
  label,
  desc,
  children,
  last = false,
  className,
}: {
  label: string
  desc: string
  children: React.ReactNode
  last?: boolean
  className?: string
}) {
  return (
    <div
      className={cn('flex items-center justify-between py-3.5 gap-6', className)}
      style={{ borderBottom: last ? 'none' : '1px solid var(--border)' }}
    >
      <div>
        <div className="text-[13px] text-[var(--text)] mb-0.5">{label}</div>
        <div className="font-mono text-[11px] text-[var(--text2)]">{desc}</div>
      </div>
      {children}
    </div>
  )
}

// ── Slider ────────────────────────────────────────────────────────────────────

interface SliderProps {
  min: number
  max: number
  step?: number
  value: number
  onChange: (v: number) => void
  format?: (v: number) => string
  labelLeft?: string
  labelRight?: string
}

function Slider({ min, max, step = 1, value, onChange, format, labelLeft, labelRight }: SliderProps) {
  const display = format ? format(value) : `${value}`
  return (
    <div className="flex items-center gap-2.5 shrink-0">
      {labelLeft && (
        <span className="font-mono text-[11px] text-[var(--text2)]">{labelLeft}</span>
      )}
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={e => onChange(Number(e.target.value))}
        className="slider-range"
        style={{ width: 100 }}
      />
      {labelRight && (
        <span className="font-mono text-[11px] text-[var(--text2)]">{labelRight}</span>
      )}
      {!labelRight && (
        <span className="font-mono text-[12px] shrink-0" style={{ color: 'var(--accent)', width: 36 }}>
          {display}
        </span>
      )}
    </div>
  )
}

// ── Select ────────────────────────────────────────────────────────────────────

function Select({
  options,
  value,
  onChange,
}: {
  options: string[]
  value: string
  onChange: (v: string) => void
}) {
  return (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      className="font-mono text-[11px] rounded cursor-pointer outline-none"
      style={{
        background: 'var(--surface2)',
        border: '1px solid var(--border2)',
        color: 'var(--text)',
        padding: '6px 10px',
      }}
    >
      {options.map(opt => (
        <option key={opt} value={opt}>{opt}</option>
      ))}
    </select>
  )
}

// ── CardSkeleton / CardError ──────────────────────────────────────────────────

function CardSkeleton() {
  return (
    <Card>
      <div className="animate-pulse space-y-4">
        <div className="h-3 rounded w-1/3" style={{ background: 'var(--border2)' }} />
        {[...Array(5)].map((_, i) => (
          <div key={i} className="h-10 rounded" style={{ background: 'var(--surface2)' }} />
        ))}
      </div>
    </Card>
  )
}

function CardError({ message }: { message: string }) {
  return (
    <Card>
      <div
        className="font-mono text-[11px] p-4 text-center rounded"
        style={{ color: 'var(--red)', background: 'var(--surface2)' }}
      >
        {message}
      </div>
    </Card>
  )
}

// ── SaveButton ────────────────────────────────────────────────────────────────

function SaveButton({ onClick, isPending }: { onClick: () => void; isPending: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={isPending}
      className="font-mono text-[10px] uppercase tracking-wide px-2.5 py-1 rounded transition-opacity"
      style={{
        background: 'var(--accent)',
        color: '#000',
        opacity: isPending ? 0.6 : 1,
        cursor: isPending ? 'not-allowed' : 'pointer',
      }}
    >
      {isPending ? 'Saving…' : 'Save'}
    </button>
  )
}

// ── Risk Parameters card ──────────────────────────────────────────────────────

function RiskParametersCard() {
  const qc = useQueryClient()
  const { data, isLoading, isError } = useQuery({
    queryKey: ['settings'],
    queryFn: fetchOperatorSettings,
  })

  // API-backed — GET/PUT /settings: max_drawdown_limit, per_strategy_cap, risk_tolerance
  const [portfolioDD,   setPortfolioDD]   = useState(10)
  const [maxCapital,    setMaxCapital]    = useState(30)
  const [riskTolerance, setRiskTolerance] = useState(2)

  // TODO: no max_strategy_drawdown or target_volatility fields on GET/PUT /settings — local only, does not persist
  const [strategyDD, setStrategyDD] = useState(12)
  const [targetVol,  setTargetVol]  = useState(15)

  useEffect(() => {
    if (!data) return
    setPortfolioDD(Math.round(Number(data.max_drawdown_limit) * 100))
    setMaxCapital(Math.round(Number(data.per_strategy_cap) * 100))
    setRiskTolerance(RISK_TO_NUM[data.risk_tolerance])
  }, [data])

  const apiPortfolioDD   = data ? Math.round(Number(data.max_drawdown_limit) * 100) : 10
  const apiMaxCapital    = data ? Math.round(Number(data.per_strategy_cap) * 100) : 30
  const apiRiskTolerance = data ? RISK_TO_NUM[data.risk_tolerance] : 2
  const isDirty =
    portfolioDD   !== apiPortfolioDD   ||
    maxCapital    !== apiMaxCapital    ||
    riskTolerance !== apiRiskTolerance

  const mutation = useMutation({
    mutationFn: (updates: ApiOperatorSettingsUpdate) => updateOperatorSettings(updates),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  })

  function handleSave() {
    mutation.mutate({
      max_drawdown_limit: portfolioDD / 100,
      per_strategy_cap:   maxCapital / 100,
      risk_tolerance:     NUM_TO_RISK[riskTolerance],
    })
  }

  if (isLoading || !data) return <CardSkeleton />
  if (isError)            return <CardError message="Failed to load risk parameters" />

  return (
    <Card>
      <CardTitle action={isDirty ? <SaveButton onClick={handleSave} isPending={mutation.isPending} /> : undefined}>
        Risk Parameters
      </CardTitle>

      <SettingRow label="Max Portfolio Drawdown" desc="System pauses all trading if portfolio DD exceeds this">
        <Slider min={5} max={30} value={portfolioDD} onChange={setPortfolioDD} format={v => `${v}%`} />
      </SettingRow>

      <SettingRow label="Max Strategy Drawdown" desc="Per-strategy limit before it triggers a warning">
        {/* TODO: no per-strategy drawdown limit on GET/PUT /settings; value is local-only and does not persist */}
        <Slider min={5} max={25} value={strategyDD} onChange={setStrategyDD} format={v => `${v}%`} />
      </SettingRow>

      <SettingRow label="Risk Tolerance" desc="Affects position sizing and allocation aggressiveness">
        <Slider
          min={1}
          max={3}
          value={riskTolerance}
          onChange={setRiskTolerance}
          labelLeft="Low"
          labelRight="High"
          format={v => String(v)}
        />
      </SettingRow>

      <SettingRow label="Max Capital Per Strategy" desc="Hard cap on any single strategy allocation">
        <Slider min={10} max={50} value={maxCapital} onChange={setMaxCapital} format={v => `${v}%`} />
      </SettingRow>

      <SettingRow label="Target Portfolio Volatility" desc="Annualized volatility target for position sizing" last>
        {/* TODO: no target_volatility field on GET/PUT /settings; value is local-only and does not persist */}
        <Slider min={5} max={30} value={targetVol} onChange={setTargetVol} format={v => `${v}%`} />
      </SettingRow>
    </Card>
  )
}

// ── Governance & Promotion card ───────────────────────────────────────────────

function GovernanceCard() {
  const qc = useQueryClient()
  const { data, isLoading, isError } = useQuery({
    queryKey: ['settings'],
    queryFn: fetchOperatorSettings,
  })

  // API-backed — GET/PUT /settings: auto_promote_enabled, rebalance_frequency
  const [autoPromote,   setAutoPromote]   = useState(false)
  const [rebalanceFreq, setRebalanceFreq] = useState('Daily')

  // TODO: min_sharpe_for_promotion, min_paper_period, auto_demote_on_breach fields not on GET/PUT /settings — local only
  const [minSharpe,   setMinSharpe]   = useState(1.5)
  const [paperPeriod, setPaperPeriod] = useState('30 days')
  const [autoDemote,  setAutoDemote]  = useState(true)

  useEffect(() => {
    if (!data) return
    setAutoPromote(data.auto_promote_enabled)
    setRebalanceFreq(FREQ_TO_UI[data.rebalance_frequency])
  }, [data])

  const apiAutoPromote   = data?.auto_promote_enabled ?? false
  const apiRebalanceFreq = data ? FREQ_TO_UI[data.rebalance_frequency] : 'Daily'
  const isDirty =
    autoPromote   !== apiAutoPromote   ||
    rebalanceFreq !== apiRebalanceFreq

  const mutation = useMutation({
    mutationFn: (updates: ApiOperatorSettingsUpdate) => updateOperatorSettings(updates),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  })

  function handleSave() {
    mutation.mutate({
      auto_promote_enabled: autoPromote,
      rebalance_frequency:  UI_TO_FREQ[rebalanceFreq],
    })
  }

  if (isLoading || !data) return <CardSkeleton />
  if (isError)            return <CardError message="Failed to load governance settings" />

  return (
    <Card>
      <CardTitle action={isDirty ? <SaveButton onClick={handleSave} isPending={mutation.isPending} /> : undefined}>
        Governance & Promotion
      </CardTitle>

      <SettingRow label="Auto-promote strategies" desc="Promote paper → live when all criteria are met">
        <Toggle on={autoPromote} onChange={setAutoPromote} />
      </SettingRow>

      <SettingRow label="Minimum Sharpe for promotion" desc="Paper strategies must exceed this to be eligible">
        {/* TODO: no min_sharpe_for_promotion field on GET/PUT /settings; value is local-only and does not persist */}
        <Slider
          min={0.5}
          max={3}
          step={0.1}
          value={minSharpe}
          onChange={setMinSharpe}
          format={v => v.toFixed(1)}
        />
      </SettingRow>

      <SettingRow label="Min paper trading period" desc="Days of paper trading required before promotion">
        {/* TODO: no min_paper_period field on GET/PUT /settings; value is local-only and does not persist */}
        <Select
          options={['14 days', '30 days', '60 days', '90 days']}
          value={paperPeriod}
          onChange={setPaperPeriod}
        />
      </SettingRow>

      <SettingRow label="Rebalance frequency" desc="How often portfolio allocations are rebalanced">
        <Select
          options={['Daily', 'Weekly', 'Monthly']}
          value={rebalanceFreq}
          onChange={setRebalanceFreq}
        />
      </SettingRow>

      <SettingRow label="Auto-demote on breach" desc="Auto-demote live strategy to paper if DD limit exceeded" last>
        {/* TODO: no auto_demote_on_breach field on GET/PUT /settings; value is local-only and does not persist */}
        <Toggle on={autoDemote} onChange={setAutoDemote} />
      </SettingRow>
    </Card>
  )
}

// ── Data & Simulation card ────────────────────────────────────────────────────

function DataSimulationCard() {
  // TODO: no GET endpoint returns the active dataset/feature version — metadata routes (/metadata/datasets, /metadata/features) are POST-only (create new versions)
  // TODO: slippage model and transaction cost selects have no configurable PUT endpoint; cost_model_configuration in GET /settings/advanced is read-only
  const [slippage, setSlippage] = useState('Fixed (0.02%)')
  const [txCost,   setTxCost]   = useState('$0.005 / share')

  return (
    <Card>
      <CardTitle>Data & Simulation</CardTitle>

      {/* TODO: active dataset version not available from any GET endpoint */}
      <SettingRow label="Active Dataset Version" desc="Currently loaded market data version">
        <StatusBadge variant="green" className="font-mono">—</StatusBadge>
      </SettingRow>

      {/* TODO: active feature version not available from any GET endpoint */}
      <SettingRow label="Feature Version" desc="Currently loaded feature dataset">
        <StatusBadge variant="green" className="font-mono">—</StatusBadge>
      </SettingRow>

      {/* TODO: slippage model is not configurable via API — local only, does not persist */}
      <SettingRow label="Slippage Model" desc="Applied to all simulated fills">
        <Select
          options={['Fixed (0.02%)', 'Volume-based', 'Spread model']}
          value={slippage}
          onChange={setSlippage}
        />
      </SettingRow>

      {/* TODO: transaction cost is not configurable via API — local only, does not persist */}
      <SettingRow label="Transaction Cost" desc="Per-trade cost applied in simulation and live" last>
        <Select
          options={['$0.005 / share', '$1.00 / trade', '0.1% notional']}
          value={txCost}
          onChange={setTxCost}
        />
      </SettingRow>
    </Card>
  )
}

// ── Notifications card ────────────────────────────────────────────────────────

function NotificationsCard() {
  // TODO: no notifications endpoint on any route — all toggles are local-only and do not persist
  const [drawdown,  setDrawdown]  = useState(true)
  const [promotion, setPromotion] = useState(true)
  const [pipeline,  setPipeline]  = useState(true)
  const [dailyPnl,  setDailyPnl]  = useState(false)

  return (
    <Card>
      <CardTitle>Notifications</CardTitle>

      <SettingRow label="Drawdown alerts" desc="Notify when any drawdown threshold is hit">
        <Toggle on={drawdown} onChange={setDrawdown} />
      </SettingRow>

      <SettingRow label="Strategy promotion events" desc="Notify on paper → live promotions">
        <Toggle on={promotion} onChange={setPromotion} />
      </SettingRow>

      <SettingRow label="Pipeline failures" desc="Alert on data or feature pipeline errors">
        <Toggle on={pipeline} onChange={setPipeline} />
      </SettingRow>

      <SettingRow label="Kill switch events" desc="Always notify on global kill switch">
        <div style={{ opacity: 0.6 }}>
          <Toggle on={true} disabled />
        </div>
      </SettingRow>

      <SettingRow label="Daily PnL summary" desc="End of day performance email" last>
        <Toggle on={dailyPnl} onChange={setDailyPnl} />
      </SettingRow>
    </Card>
  )
}

// ── Settings ──────────────────────────────────────────────────────────────────

export default function Settings() {
  return (
    <div className="p-6">
      <div className="grid grid-cols-2 gap-4">
        <RiskParametersCard />
        <GovernanceCard />
        <DataSimulationCard />
        <NotificationsCard />
      </div>
    </div>
  )
}
