import { useState } from 'react'
import { cn } from '../lib/utils'
import Toggle from '../components/shared/Toggle'
import StatusBadge from '../components/shared/StatusBadge'

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

function CardTitle({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="font-mono text-[11px] font-medium uppercase tracking-[0.08em] mb-4 pb-3"
      style={{ color: 'var(--text2)', borderBottom: '1px solid var(--border)' }}
    >
      {children}
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

function Select({ options, value, onChange }: { options: string[]; value: string; onChange: (v: string) => void }) {
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

// ── Risk Parameters card ──────────────────────────────────────────────────────

function RiskParametersCard() {
  const [portfolioDD,   setPortfolioDD]   = useState(10)
  const [strategyDD,   setStrategyDD]    = useState(12)
  const [riskTolerance, setRiskTolerance] = useState(2)
  const [maxCapital,    setMaxCapital]    = useState(30)
  const [targetVol,     setTargetVol]     = useState(15)

  return (
    <Card>
      <CardTitle>Risk Parameters</CardTitle>

      <SettingRow label="Max Portfolio Drawdown" desc="System pauses all trading if portfolio DD exceeds this">
        <Slider min={5} max={30} value={portfolioDD} onChange={setPortfolioDD} format={v => `${v}%`} />
      </SettingRow>

      <SettingRow label="Max Strategy Drawdown" desc="Per-strategy limit before it triggers a warning">
        <Slider min={5} max={25} value={strategyDD} onChange={setStrategyDD} format={v => `${v}%`} />
      </SettingRow>

      <SettingRow label="Risk Tolerance" desc="Affects position sizing and allocation aggressiveness">
        <Slider
          min={1} max={3} value={riskTolerance} onChange={setRiskTolerance}
          labelLeft="Low" labelRight="High"
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
  const [autoPromote,   setAutoPromote]  = useState(false)
  const [minSharpe,     setMinSharpe]    = useState(1.5)
  const [paperPeriod,   setPaperPeriod]  = useState('30 days')
  const [rebalanceFreq, setRebalanceFreq] = useState('Daily')
  const [autoDemote,    setAutoDemote]   = useState(true)

  return (
    <Card>
      <CardTitle>Governance & Promotion</CardTitle>

      <SettingRow label="Auto-promote strategies" desc="Promote paper → live when all criteria are met">
        <Toggle on={autoPromote} onChange={setAutoPromote} />
      </SettingRow>

      <SettingRow label="Minimum Sharpe for promotion" desc="Paper strategies must exceed this to be eligible">
        <Slider
          min={0.5} max={3} step={0.1} value={minSharpe} onChange={setMinSharpe}
          format={v => v.toFixed(1)}
        />
      </SettingRow>

      <SettingRow label="Min paper trading period" desc="Days of paper trading required before promotion">
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
        <Toggle on={autoDemote} onChange={setAutoDemote} />
      </SettingRow>
    </Card>
  )
}

// ── Data & Simulation card ────────────────────────────────────────────────────

function DataSimulationCard() {
  const [slippage,    setSlippage]    = useState('Fixed (0.02%)')
  const [txCost,      setTxCost]      = useState('$0.005 / share')

  return (
    <Card>
      <CardTitle>Data & Simulation</CardTitle>

      <SettingRow label="Active Dataset Version" desc="Currently loaded market data version">
        <StatusBadge variant="green" className="font-mono">adjusted_bars_v43</StatusBadge>
      </SettingRow>

      <SettingRow label="Feature Version" desc="Currently loaded feature dataset">
        <StatusBadge variant="green" className="font-mono">features_v43</StatusBadge>
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
  const [drawdown,   setDrawdown]   = useState(true)
  const [promotion,  setPromotion]  = useState(true)
  const [pipeline,   setPipeline]   = useState(true)
  const [dailyPnl,   setDailyPnl]   = useState(false)

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
