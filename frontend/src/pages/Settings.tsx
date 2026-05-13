import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { cn } from '../lib/utils'
import Toggle from '../components/shared/Toggle'
import StatusBadge from '../components/shared/StatusBadge'
import {
  fetchOperatorSettings,
  updateOperatorSettings,
  fetchLatestDatasetVersion,
  fetchLatestFeatureVersion,
  type ApiOperatorSettings,
  type ApiOperatorSettingsUpdate,
  type SlippageModel,
  type TransactionCostModel,
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

function InlineNote({
  children,
  tone = 'neutral',
}: {
  children: React.ReactNode
  tone?: 'neutral' | 'warning'
}) {
  const styles = tone === 'warning'
    ? {
        background: 'var(--yellow-dim)',
        border: '1px solid rgba(232,168,56,0.25)',
        color: 'var(--yellow)',
      }
    : {
        background: 'var(--surface2)',
        border: '1px solid var(--border)',
        color: 'var(--text2)',
      }

  return (
    <div className="rounded-md px-3 py-2 font-mono text-[11px]" style={styles}>
      {children}
    </div>
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

  // API-backed — GET/PUT /settings: drawdown limits, capital cap, target volatility, risk tolerance
  const [portfolioDD,   setPortfolioDD]   = useState(10)
  const [strategyDD,    setStrategyDD]    = useState(12)
  const [maxCapital,    setMaxCapital]    = useState(30)
  const [targetVol,     setTargetVol]     = useState(15)
  const [riskTolerance, setRiskTolerance] = useState(2)

  useEffect(() => {
    if (!data) return
    setPortfolioDD(Math.round(Number(data.max_drawdown_limit) * 100))
    setStrategyDD(Math.round(Number(data.max_strategy_drawdown) * 100))
    setMaxCapital(Math.round(Number(data.per_strategy_cap) * 100))
    setTargetVol(Math.round(Number(data.target_portfolio_volatility) * 100))
    setRiskTolerance(RISK_TO_NUM[data.risk_tolerance])
  }, [data])

  const apiPortfolioDD   = data ? Math.round(Number(data.max_drawdown_limit) * 100) : 10
  const apiStrategyDD    = data ? Math.round(Number(data.max_strategy_drawdown) * 100) : 12
  const apiMaxCapital    = data ? Math.round(Number(data.per_strategy_cap) * 100) : 30
  const apiTargetVol     = data ? Math.round(Number(data.target_portfolio_volatility) * 100) : 15
  const apiRiskTolerance = data ? RISK_TO_NUM[data.risk_tolerance] : 2
  const isDirty =
    portfolioDD   !== apiPortfolioDD   ||
    strategyDD    !== apiStrategyDD    ||
    maxCapital    !== apiMaxCapital    ||
    targetVol     !== apiTargetVol     ||
    riskTolerance !== apiRiskTolerance

  const mutation = useMutation({
    mutationFn: (updates: ApiOperatorSettingsUpdate) => updateOperatorSettings(updates),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  })

  function handleSave() {
    mutation.mutate({
      max_drawdown_limit:          portfolioDD / 100,
      max_strategy_drawdown:       strategyDD / 100,
      per_strategy_cap:            maxCapital / 100,
      target_portfolio_volatility: targetVol / 100,
      risk_tolerance:              NUM_TO_RISK[riskTolerance],
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

  const [autoPromote, setAutoPromote] = useState(false)
  const [autoRebalance, setAutoRebalance] = useState(false)
  const [rebalanceFreq, setRebalanceFreq] = useState('Daily')
  const [autoDemote, setAutoDemote] = useState(true)

  useEffect(() => {
    if (!data) return
    setAutoPromote(data.auto_promote_enabled)
    setAutoRebalance(data.auto_rebalance_enabled)
    setRebalanceFreq(FREQ_TO_UI[data.rebalance_frequency])
    setAutoDemote(data.auto_demote_on_breach)
  }, [data])

  const apiAutoPromote = data?.auto_promote_enabled ?? false
  const apiAutoRebalance = data?.auto_rebalance_enabled ?? false
  const apiRebalanceFreq = data ? FREQ_TO_UI[data.rebalance_frequency] : 'Daily'
  const apiAutoDemote = data?.auto_demote_on_breach ?? true
  const automationSource = data?.metadata?.source_of_truth?.automation_controls_source ?? 'settings'
  const promotionSource = data?.metadata?.source_of_truth?.promotion_thresholds_source ?? 'promotion_rules'
  const allocationSource = data?.metadata?.source_of_truth?.allocation_targets_source ?? 'capital_allocation_policies + allocation_overrides'
  const isDirty =
    autoPromote !== apiAutoPromote ||
    autoRebalance !== apiAutoRebalance ||
    rebalanceFreq !== apiRebalanceFreq ||
    autoDemote !== apiAutoDemote

  const mutation = useMutation({
    mutationFn: (updates: ApiOperatorSettingsUpdate) => updateOperatorSettings(updates),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  })

  function handleSave() {
    mutation.mutate({
      auto_promote_enabled: autoPromote,
      auto_rebalance_enabled: autoRebalance,
      rebalance_frequency: UI_TO_FREQ[rebalanceFreq],
      auto_demote_on_breach: autoDemote,
    })
  }

  if (isLoading || !data) return <CardSkeleton />
  if (isError) return <CardError message="Failed to load governance settings" />

  return (
    <Card>
      <CardTitle action={isDirty ? <SaveButton onClick={handleSave} isPending={mutation.isPending} /> : undefined}>
        Governance & Allocation Automation
      </CardTitle>

      <div className="mb-4 space-y-2">
        <InlineNote tone="warning">
          Manual promotion eligibility now reads active {promotionSource}. The legacy threshold fields in operator settings are compatibility-only.
        </InlineNote>
        <InlineNote>
          Automation flags are stored in {automationSource}. Allocation targets come from {allocationSource}.
        </InlineNote>
      </div>

      <SettingRow label="Auto-promote strategies" desc="Stores the automation flag. No automatic promotion runner is wired yet.">
        <Toggle on={autoPromote} onChange={setAutoPromote} />
      </SettingRow>

      <SettingRow label="Auto-rebalance allocation" desc="Stores the automation flag. Quality-based reallocation is not wired yet.">
        <Toggle on={autoRebalance} onChange={setAutoRebalance} />
      </SettingRow>

      <SettingRow label="Rebalance frequency" desc="Stored cadence for future automated allocation review.">
        <Select
          options={['Daily', 'Weekly', 'Monthly']}
          value={rebalanceFreq}
          onChange={setRebalanceFreq}
        />
      </SettingRow>

      <SettingRow label="Legacy promotion thresholds" desc="Kept for compatibility. These values do not drive current promotion decisions.">
        <StatusBadge variant="yellow" className="font-mono">Deprecated</StatusBadge>
      </SettingRow>

      <SettingRow label="Auto-demote on breach" desc="Stores the automation flag. No automatic demotion service is wired yet." last>
        <Toggle on={autoDemote} onChange={setAutoDemote} />
      </SettingRow>

      <div className="mt-4 space-y-2">
        <InlineNote>
          Stored only: min_sharpe_for_promotion = {Number(data.min_sharpe_for_promotion).toFixed(1)}, min_paper_trading_period_days = {data.min_paper_trading_period_days}.
        </InlineNote>
        <InlineNote>
          Use promotion rules for threshold changes and the Controls page for manual governance transitions.
        </InlineNote>
      </div>
    </Card>
  )
}

// Slippage / cost model maps ────────────────────────────────────────────────

const SLIPPAGE_TO_UI: Record<SlippageModel, string> = {
  fixed:        'Fixed (0.02%)',
  volume_based: 'Volume-based',
  spread:       'Spread model',
}
const UI_TO_SLIPPAGE: Record<string, SlippageModel> = {
  'Fixed (0.02%)': 'fixed',
  'Volume-based':  'volume_based',
  'Spread model':  'spread',
}
const TX_TO_UI: Record<TransactionCostModel, string> = {
  per_share:    '$0.005 / share',
  per_trade:    '$1.00 / trade',
  notional_pct: '0.1% notional',
}
const UI_TO_TX: Record<string, TransactionCostModel> = {
  '$0.005 / share': 'per_share',
  '$1.00 / trade':  'per_trade',
  '0.1% notional':  'notional_pct',
}

// ── Data & Simulation card ────────────────────────────────────────────────────

function DataSimulationCard() {
  const qc = useQueryClient()

  const { data, isLoading, isError } = useQuery({
    queryKey: ['settings'],
    queryFn: fetchOperatorSettings,
  })
  const { data: datasetVersion } = useQuery({
    queryKey: ['metadata', 'dataset-version-latest'],
    queryFn: fetchLatestDatasetVersion,
  })
  const { data: featureVersion } = useQuery({
    queryKey: ['metadata', 'feature-version-latest'],
    queryFn: fetchLatestFeatureVersion,
  })

  const [slippage, setSlippage] = useState('Fixed (0.02%)')
  const [txCost,   setTxCost]   = useState('$0.005 / share')

  useEffect(() => {
    if (!data) return
    setSlippage(SLIPPAGE_TO_UI[data.slippage_model] ?? 'Fixed (0.02%)')
    setTxCost(TX_TO_UI[data.transaction_cost_model] ?? '$0.005 / share')
  }, [data])

  const apiSlippage = data ? SLIPPAGE_TO_UI[data.slippage_model] : 'Fixed (0.02%)'
  const apiTxCost   = data ? TX_TO_UI[data.transaction_cost_model] : '$0.005 / share'
  const isDirty = slippage !== apiSlippage || txCost !== apiTxCost

  const mutation = useMutation({
    mutationFn: (updates: ApiOperatorSettingsUpdate) => updateOperatorSettings(updates),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  })

  function handleSave() {
    mutation.mutate({
      slippage_model:        UI_TO_SLIPPAGE[slippage],
      transaction_cost_model: UI_TO_TX[txCost],
    })
  }

  if (isLoading || !data) return <CardSkeleton />
  if (isError)            return <CardError message="Failed to load data & simulation settings" />

  return (
    <Card>
      <CardTitle action={isDirty ? <SaveButton onClick={handleSave} isPending={mutation.isPending} /> : undefined}>
        Data & Simulation
      </CardTitle>

      <SettingRow label="Active Dataset Version" desc="Most recently registered market data version">
        {datasetVersion ? (
          <StatusBadge variant="green" className="font-mono">{datasetVersion.dataset_version_id}</StatusBadge>
        ) : (
          <StatusBadge variant="gray" className="font-mono">—</StatusBadge>
        )}
      </SettingRow>

      <SettingRow label="Feature Version" desc="Most recently registered feature dataset">
        {featureVersion ? (
          <StatusBadge variant="blue" className="font-mono">{featureVersion.dataset_version_id}</StatusBadge>
        ) : (
          <StatusBadge variant="gray" className="font-mono">—</StatusBadge>
        )}
      </SettingRow>

      <SettingRow label="Slippage Model" desc="Applied to all simulated fills">
        <Select
          options={['Fixed (0.02%)', 'Volume-based', 'Spread model']}
          value={slippage}
          onChange={setSlippage}
        />
      </SettingRow>

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
  const qc = useQueryClient()
  const { data, isLoading, isError } = useQuery({
    queryKey: ['settings'],
    queryFn: fetchOperatorSettings,
  })

  const [drawdown,  setDrawdown]  = useState(true)
  const [promotion, setPromotion] = useState(true)
  const [pipeline,  setPipeline]  = useState(true)

  useEffect(() => {
    if (!data) return
    setDrawdown(data.notify_drawdown_alerts)
    setPromotion(data.notify_strategy_promotion_events)
    setPipeline(data.notify_pipeline_failures)
  }, [data])

  const apiDrawdown  = data?.notify_drawdown_alerts ?? true
  const apiPromotion = data?.notify_strategy_promotion_events ?? true
  const apiPipeline  = data?.notify_pipeline_failures ?? true
  const isDirty =
    drawdown  !== apiDrawdown  ||
    promotion !== apiPromotion ||
    pipeline  !== apiPipeline

  const mutation = useMutation({
    mutationFn: (updates: ApiOperatorSettingsUpdate) => updateOperatorSettings(updates),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  })

  function handleSave() {
    mutation.mutate({
      notify_drawdown_alerts: drawdown,
      notify_strategy_promotion_events: promotion,
      notify_pipeline_failures: pipeline,
    })
  }

  if (isLoading || !data) return <CardSkeleton />
  if (isError)            return <CardError message="Failed to load notification settings" />

  return (
    <Card>
      <CardTitle action={isDirty ? <SaveButton onClick={handleSave} isPending={mutation.isPending} /> : undefined}>
        Notifications
      </CardTitle>

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

      <SettingRow label="Daily PnL summary" desc="Stubbed until email/report delivery exists" last>
        <div style={{ opacity: 0.6 }}>
          <Toggle on={false} disabled />
        </div>
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
