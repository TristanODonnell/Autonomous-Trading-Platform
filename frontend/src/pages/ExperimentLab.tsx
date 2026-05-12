import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import StatusBadge from '../components/shared/StatusBadge'
import type { BadgeVariant } from '../components/shared/StatusBadge'
import { cn } from '../lib/utils'
import {
  fetchExperiments,
  fetchExperimentStrategies,
  createExperiment,
  cancelExperiment,
} from '../services'
import type { ExperimentSummary, ExperimentStrategy, ExperimentType, ExperimentStatus } from '../types'

// ── Constants ─────────────────────────────────────────────────────────────────

const STATUS_VARIANT: Record<ExperimentStatus, BadgeVariant> = {
  pending:   'gray',
  running:   'blue',
  completed: 'green',
  failed:    'red',
  cancelled: 'gray',
}

const STATUS_DOT: Partial<Record<ExperimentStatus, true>> = { running: true }

const TYPE_LABEL: Record<ExperimentType, string> = {
  backtest:        'Backtest',
  parameter_sweep: 'Param Sweep',
  ab_comparison:   'A/B Compare',
  rolling_window:  'Rolling Window',
}

const BTN_BASE =
  'py-1 px-2.5 rounded font-mono text-[9px] font-medium uppercase tracking-[0.08em] cursor-pointer border transition-all duration-150'

const BTN: Record<'ghost' | 'primary' | 'danger' | 'outline', string> = {
  ghost:   'bg-transparent text-[var(--text2)] border-[var(--border2)] hover:text-[var(--text)] hover:border-[var(--text3)]',
  primary: 'bg-[var(--accent)] text-black border-[var(--accent)] hover:bg-[var(--accent2)]',
  danger:  'bg-[var(--red-dim)] text-[var(--red)] border-[var(--red)] hover:bg-[rgba(255,77,109,0.2)]',
  outline: 'bg-transparent text-[var(--accent)] border-[var(--accent)] hover:bg-[rgba(0,229,160,0.08)]',
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function govBadge(state: string): { variant: BadgeVariant; label: string } {
  if (state.includes('live'))   return { variant: 'green',  label: 'Live'     }
  if (state.includes('paper'))  return { variant: 'blue',   label: 'Paper'    }
  if (state === 'research')     return { variant: 'purple', label: 'Research' }
  if (state === 'rejected')     return { variant: 'red',    label: 'Rejected' }
  return { variant: 'gray', label: state || '—' }
}

function fmt(n: number, decimals = 2) {
  return n.toFixed(decimals)
}

function fmtPct(n: number) {
  const pct = n * 100
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`
}

function fmtDate(d: string | null) {
  if (!d) return '—'
  return d.slice(0, 10)
}

// ── Progress bar ──────────────────────────────────────────────────────────────

function ProgressBar({ value }: { value: number }) {
  return (
    <div className="w-full rounded-full overflow-hidden" style={{ height: 4, background: 'var(--border2)' }}>
      <div
        className="h-full rounded-full transition-all duration-500"
        style={{ width: `${Math.min(100, value)}%`, background: 'var(--blue)' }}
      />
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
      <div className="flex justify-between items-center mb-2">
        <div className="h-4 rounded w-3/5" style={{ background: 'var(--border2)' }} />
        <div className="h-4 rounded w-16" style={{ background: 'var(--border2)' }} />
      </div>
      <div className="h-3 rounded w-2/5 mb-4" style={{ background: 'var(--border2)' }} />
      <div className="h-3 rounded w-full mb-2" style={{ background: 'var(--border)' }} />
      <div className="h-3 rounded w-4/5 mb-4" style={{ background: 'var(--border)' }} />
      <div className="grid grid-cols-2 gap-2 mb-4">
        {[...Array(4)].map((_, i) => <div key={i} className="h-10 rounded" style={{ background: 'var(--border)' }} />)}
      </div>
      <div className="flex justify-between">
        <div className="h-5 rounded w-24" style={{ background: 'var(--border2)' }} />
        <div className="h-5 rounded w-16" style={{ background: 'var(--border2)' }} />
      </div>
    </div>
  )
}

// ── Experiment card ───────────────────────────────────────────────────────────

function ExperimentCard({
  exp,
  expanded,
  onToggle,
  onCancel,
}: {
  exp: ExperimentSummary
  expanded: boolean
  onToggle: () => void
  onCancel: () => void
}) {
  const canCancel = exp.status === 'pending' || exp.status === 'running'
  const hasResults = exp.total_strategies > 0

  return (
    <div
      className="rounded-md p-4 cursor-pointer transition-colors duration-150"
      style={{
        background:    expanded ? 'rgba(0,229,160,0.04)' : 'var(--surface2)',
        border:        `1px solid ${expanded ? 'var(--accent)' : 'var(--border)'}`,
        borderRadius:  6,
      }}
      onMouseEnter={e => { if (!expanded) (e.currentTarget as HTMLDivElement).style.borderColor = 'var(--border2)' }}
      onMouseLeave={e => { if (!expanded) (e.currentTarget as HTMLDivElement).style.borderColor = 'var(--border)' }}
      onClick={onToggle}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2 mb-1">
        <span className="text-[13px] font-medium text-[var(--text)] leading-snug">{exp.experiment_name}</span>
        <StatusBadge variant={STATUS_VARIANT[exp.status]} dot={STATUS_DOT[exp.status]}>
          {exp.status}
        </StatusBadge>
      </div>

      {/* ID + type */}
      <div className="font-mono text-[10px] text-[var(--text3)] mb-3">
        {exp.experiment_id} · <span style={{ color: 'var(--text2)' }}>{TYPE_LABEL[exp.experiment_type]}</span>
      </div>

      {/* Symbols */}
      <div className="flex flex-wrap gap-1 mb-3">
        {exp.symbols.slice(0, 6).map(s => (
          <span
            key={s}
            className="font-mono text-[9px] px-1.5 py-0.5 rounded"
            style={{ background: 'var(--border)', color: 'var(--text2)', border: '1px solid var(--border2)' }}
          >
            {s}
          </span>
        ))}
        {exp.symbols.length > 6 && (
          <span className="font-mono text-[9px] text-[var(--text3)]">+{exp.symbols.length - 6}</span>
        )}
      </div>

      {/* Date range */}
      <div className="font-mono text-[10px] text-[var(--text3)] mb-3">
        {fmtDate(exp.start_date)} → {fmtDate(exp.end_date)}
        {exp.dataset_version && (
          <span className="ml-2 text-[var(--text3)]">· {exp.dataset_version}</span>
        )}
      </div>

      {/* Progress bar (running only) */}
      {exp.status === 'running' && exp.progress != null && (
        <div className="mb-3">
          <div className="flex justify-between font-mono text-[9px] text-[var(--text3)] mb-1">
            <span>Progress</span>
            <span style={{ color: 'var(--blue)' }}>{exp.progress}%</span>
          </div>
          <ProgressBar value={exp.progress} />
        </div>
      )}

      {/* Metrics grid */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-2 mb-3">
        <div>
          <div className="font-mono text-[9px] text-[var(--text3)] uppercase tracking-[0.1em]">Strategies</div>
          <div className="font-mono text-[13px] font-medium text-[var(--text)]">
            {hasResults ? `${exp.total_strategies.toLocaleString()}` : '—'}
          </div>
        </div>
        <div>
          <div className="font-mono text-[9px] text-[var(--text3)] uppercase tracking-[0.1em]">Passed</div>
          <div className="font-mono text-[13px] font-medium" style={{ color: hasResults ? 'var(--accent)' : 'var(--text3)' }}>
            {hasResults ? exp.strategies_passed_filters : '—'}
          </div>
        </div>
        <div>
          <div className="font-mono text-[9px] text-[var(--text3)] uppercase tracking-[0.1em]">Best Sharpe</div>
          <div
            className="font-mono text-[13px] font-medium"
            style={{ color: exp.best_sharpe != null ? (exp.best_sharpe >= 1.5 ? 'var(--accent)' : 'var(--yellow)') : 'var(--text3)' }}
          >
            {exp.best_sharpe != null ? fmt(exp.best_sharpe) : '—'}
          </div>
        </div>
        <div>
          <div className="font-mono text-[9px] text-[var(--text3)] uppercase tracking-[0.1em]">Best Return</div>
          <div
            className="font-mono text-[13px] font-medium"
            style={{ color: exp.best_return != null ? (exp.best_return >= 0.15 ? 'var(--accent)' : 'var(--yellow)') : 'var(--text3)' }}
          >
            {exp.best_return != null ? fmtPct(exp.best_return) : '—'}
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between" onClick={e => e.stopPropagation()}>
        <button
          onClick={onToggle}
          className={cn(BTN_BASE, expanded ? BTN.outline : BTN.ghost)}
        >
          {expanded ? '↑ Collapse' : '↓ Strategies'}
        </button>
        {canCancel && (
          <button onClick={onCancel} className={cn(BTN_BASE, BTN.danger)}>
            Cancel
          </button>
        )}
      </div>
    </div>
  )
}

// ── Strategies panel ──────────────────────────────────────────────────────────

function StrategiesPanel({
  experimentName,
  experimentId,
}: {
  experimentName: string
  experimentId: string
}) {
  const { data: strategies = [], isLoading } = useQuery({
    queryKey: ['experiment-strategies', experimentId],
    queryFn: () => fetchExperimentStrategies(experimentId),
    staleTime: 60_000,
  })

  const tdBorder: React.CSSProperties = { borderBottom: '1px solid var(--border)' }

  return (
    <div
      className="rounded-lg p-5 mt-0"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      <div
        className="font-mono text-[11px] font-medium text-[var(--text2)] uppercase tracking-[0.08em] mb-4 pb-3"
        style={{ borderBottom: '1px solid var(--border)' }}
      >
        {experimentName}
        {!isLoading && (
          <span className="ml-2 text-[var(--text3)] normal-case">
            — {strategies.length} strateg{strategies.length === 1 ? 'y' : 'ies'}
          </span>
        )}
      </div>

      {isLoading && (
        <div className="font-mono text-[11px] text-[var(--text3)] animate-pulse">Loading strategies…</div>
      )}

      {!isLoading && strategies.length === 0 && (
        <p className="font-mono text-[11px] text-[var(--text3)]">
          No strategies generated yet for this experiment.
        </p>
      )}

      {!isLoading && strategies.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr>
                {['Strategy ID', 'Sharpe', 'Return', 'Max DD', 'Stage', 'Governance', ''].map(col => (
                  <th
                    key={col}
                    className="font-mono text-[9px] font-medium text-[var(--text3)] uppercase tracking-[0.1em] text-left pb-2.5 pr-4"
                    style={tdBorder}
                  >
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {strategies.map(s => {
                const gov = govBadge(s.governance_state)
                const canPromote = s.governance_state === 'research' || s.governance_state === 'approved_research'
                return (
                  <tr key={s.strategy_id}>
                    <td className="font-mono text-[11px] text-[var(--text2)] py-2.5 pr-4" style={tdBorder}>
                      {s.strategy_id}
                    </td>
                    <td
                      className="font-mono text-[13px] py-2.5 pr-4"
                      style={{
                        color: s.sharpe_ratio >= 1.5 ? 'var(--accent)' : s.sharpe_ratio >= 0.8 ? 'var(--yellow)' : 'var(--red)',
                        ...tdBorder,
                      }}
                    >
                      {fmt(s.sharpe_ratio)}
                    </td>
                    <td
                      className="font-mono text-[13px] py-2.5 pr-4"
                      style={{ color: s.total_return >= 0 ? 'var(--accent)' : 'var(--red)', ...tdBorder }}
                    >
                      {fmtPct(s.total_return)}
                    </td>
                    <td
                      className="font-mono text-[13px] py-2.5 pr-4"
                      style={{
                        color: Math.abs(s.max_drawdown) <= 0.1 ? 'var(--text)' : Math.abs(s.max_drawdown) <= 0.13 ? 'var(--yellow)' : 'var(--red)',
                        ...tdBorder,
                      }}
                    >
                      {fmtPct(s.max_drawdown)}
                    </td>
                    <td className="font-mono text-[11px] text-[var(--text2)] py-2.5 pr-4" style={tdBorder}>
                      {s.simulation_stage != null ? `Stage ${s.simulation_stage}` : '—'}
                    </td>
                    <td className="py-2.5 pr-4" style={tdBorder}>
                      <StatusBadge variant={gov.variant}>{gov.label}</StatusBadge>
                    </td>
                    <td className="py-2.5" style={tdBorder}>
                      {canPromote && (
                        <button className={cn(BTN_BASE, BTN.primary)}>
                          Promote →
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── New experiment modal ──────────────────────────────────────────────────────

const EXPERIMENT_TYPES: { value: ExperimentType; label: string }[] = [
  { value: 'backtest',        label: 'Backtest'       },
  { value: 'parameter_sweep', label: 'Parameter Sweep' },
  { value: 'ab_comparison',   label: 'A/B Comparison' },
  { value: 'rolling_window',  label: 'Rolling Window' },
]

const INPUT =
  'w-full px-3 py-2 rounded font-mono text-[11px] text-[var(--text)] bg-[var(--bg)] border border-[var(--border2)] focus:outline-none focus:border-[var(--accent)] transition-colors duration-150'

const LABEL = 'block font-mono text-[9px] text-[var(--text3)] uppercase tracking-[0.1em] mb-1'

interface FormState {
  name: string
  experiment_type: ExperimentType
  symbols: string
  start_date: string
  end_date: string
  price_basis: 'RAW' | 'ADJUSTED'
  strategy_count: string
  parameter_ranges: string
}

function NewExperimentModal({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: () => void
}) {
  const [form, setForm] = useState<FormState>({
    name: '',
    experiment_type: 'parameter_sweep',
    symbols: '',
    start_date: '',
    end_date: '',
    price_basis: 'RAW',
    strategy_count: '100',
    parameter_ranges: '{}',
  })
  const [formError, setFormError] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: createExperiment,
    onSuccess: () => {
      onCreated()
      onClose()
    },
    onError: (err: Error) => {
      setFormError(err.message || 'Failed to create experiment')
    },
  })

  function set(key: keyof FormState, value: string) {
    setForm(prev => ({ ...prev, [key]: value }))
    setFormError(null)
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setFormError(null)

    const symbols = form.symbols.split(',').map(s => s.trim().toUpperCase()).filter(Boolean)
    if (!form.name.trim())         return setFormError('Name is required')
    if (symbols.length === 0)      return setFormError('At least one symbol is required')
    if (!form.start_date)          return setFormError('Start date is required')
    if (!form.end_date)            return setFormError('End date is required')
    if (form.start_date >= form.end_date) return setFormError('End date must be after start date')

    let parameter_ranges: Record<string, unknown> = {}
    try {
      parameter_ranges = JSON.parse(form.parameter_ranges)
    } catch {
      return setFormError('Parameter ranges must be valid JSON')
    }

    mutation.mutate({
      name: form.name.trim(),
      experiment_type: form.experiment_type,
      symbols,
      start_date: form.start_date,
      end_date: form.end_date,
      price_basis: form.price_basis,
      strategy_count: parseInt(form.strategy_count, 10) || 100,
      parameter_ranges,
    })
  }

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center"
      style={{ background: 'rgba(7,11,15,0.8)' }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className="w-full max-w-lg rounded-lg p-6 relative"
        style={{ background: 'var(--surface)', border: '1px solid var(--border2)' }}
      >
        {/* Title */}
        <div className="flex items-center justify-between mb-5">
          <span className="font-mono text-[13px] font-semibold text-[var(--text)]">New Experiment</span>
          <button
            onClick={onClose}
            className="font-mono text-[14px] text-[var(--text3)] hover:text-[var(--text)] transition-colors"
          >
            ✕
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Name */}
          <div>
            <label className={LABEL}>Name</label>
            <input
              className={INPUT}
              placeholder="e.g. Q1 2024 Momentum Sweep"
              value={form.name}
              onChange={e => set('name', e.target.value)}
            />
          </div>

          {/* Type */}
          <div>
            <label className={LABEL}>Experiment Type</label>
            <select
              className={INPUT}
              value={form.experiment_type}
              onChange={e => set('experiment_type', e.target.value)}
            >
              {EXPERIMENT_TYPES.map(t => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
          </div>

          {/* Symbols */}
          <div>
            <label className={LABEL}>Symbols</label>
            <input
              className={INPUT}
              placeholder="AAPL, MSFT, GOOGL"
              value={form.symbols}
              onChange={e => set('symbols', e.target.value)}
            />
          </div>

          {/* Date range */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={LABEL}>Start Date</label>
              <input
                type="date"
                className={INPUT}
                value={form.start_date}
                onChange={e => set('start_date', e.target.value)}
              />
            </div>
            <div>
              <label className={LABEL}>End Date</label>
              <input
                type="date"
                className={INPUT}
                value={form.end_date}
                onChange={e => set('end_date', e.target.value)}
              />
            </div>
          </div>

          {/* Price basis + strategy count */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={LABEL}>Price Basis</label>
              <select
                className={INPUT}
                value={form.price_basis}
                onChange={e => set('price_basis', e.target.value)}
              >
                <option value="RAW">RAW</option>
                <option value="ADJUSTED">ADJUSTED</option>
              </select>
            </div>
            <div>
              <label className={LABEL}>Strategy Count</label>
              <input
                type="number"
                className={INPUT}
                min={1}
                max={10000}
                value={form.strategy_count}
                onChange={e => set('strategy_count', e.target.value)}
              />
            </div>
          </div>

          {/* Parameter ranges */}
          <div>
            <label className={LABEL}>Parameter Ranges (JSON)</label>
            <textarea
              className={cn(INPUT, 'resize-none')}
              rows={4}
              placeholder={'{\n  "fast_period": [5, 10, 20],\n  "slow_period": [50, 100]\n}'}
              value={form.parameter_ranges}
              onChange={e => set('parameter_ranges', e.target.value)}
            />
          </div>

          {/* Error */}
          {formError && (
            <div className="font-mono text-[10px] px-3 py-2 rounded" style={{ background: 'var(--red-dim)', color: 'var(--red)', border: '1px solid var(--red)' }}>
              {formError}
            </div>
          )}

          {/* Actions */}
          <div className="flex justify-end gap-2 pt-1">
            <button type="button" onClick={onClose} className={cn(BTN_BASE, BTN.ghost)} style={{ padding: '8px 16px', fontSize: 10 }}>
              Cancel
            </button>
            <button
              type="submit"
              disabled={mutation.isPending}
              className={cn(BTN_BASE, BTN.primary, mutation.isPending && 'opacity-60 cursor-not-allowed')}
              style={{ padding: '8px 16px', fontSize: 10 }}
            >
              {mutation.isPending ? 'Creating…' : 'Create →'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── Filter / sort types ───────────────────────────────────────────────────────

type FilterKey = 'all' | ExperimentStatus
type SortKey   = 'created' | 'best_sharpe' | 'strategies'

const FILTERS: { key: FilterKey; label: string; variant: BadgeVariant }[] = [
  { key: 'all',       label: 'All',       variant: 'gray'   },
  { key: 'running',   label: 'Running',   variant: 'blue'   },
  { key: 'completed', label: 'Completed', variant: 'green'  },
  { key: 'failed',    label: 'Failed',    variant: 'red'    },
  { key: 'pending',   label: 'Pending',   variant: 'gray'   },
]

const SORTS: { key: SortKey; label: string }[] = [
  { key: 'created',     label: 'Created'    },
  { key: 'best_sharpe', label: 'Best Sharpe' },
  { key: 'strategies',  label: 'Strategies' },
]

function applySort(exps: ExperimentSummary[], sort: SortKey): ExperimentSummary[] {
  return [...exps].sort((a, b) => {
    if (sort === 'best_sharpe') return (b.best_sharpe ?? -Infinity) - (a.best_sharpe ?? -Infinity)
    if (sort === 'strategies')  return b.total_strategies - a.total_strategies
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  })
}

// ── ExperimentLab ─────────────────────────────────────────────────────────────

export default function ExperimentLab() {
  const queryClient = useQueryClient()
  const [filter,     setFilter]     = useState<FilterKey>('all')
  const [sort,       setSort]       = useState<SortKey>('created')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [showModal,  setShowModal]  = useState(false)

  const { data: experiments = [], isLoading, isError } = useQuery({
    queryKey: ['experiments'],
    queryFn: fetchExperiments,
  })

  const cancelMutation = useMutation({
    mutationFn: cancelExperiment,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['experiments'] }),
  })

  function toggleExpand(id: string) {
    setExpandedId(prev => (prev === id ? null : id))
  }

  const visible = applySort(
    filter === 'all' ? experiments : experiments.filter(e => e.status === filter),
    sort,
  )

  const expandedExp = experiments.find(e => e.experiment_id === expandedId) ?? null

  return (
    <div className="p-6">
      {/* Filter / sort bar */}
      <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
        {/* Status filters */}
        <div className="flex gap-2 flex-wrap">
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
                  dot={active && f.key === 'running' ? true : undefined}
                  className="px-3 py-1.5 cursor-pointer"
                >
                  {f.label}
                </StatusBadge>
              </button>
            )
          })}
        </div>

        {/* Sort + action */}
        <div className="flex items-center gap-2">
          <span className="font-mono text-[9px] text-[var(--text3)] uppercase tracking-[0.08em]">Sort</span>
          <select
            className="font-mono text-[10px] text-[var(--text2)] bg-[var(--surface2)] border border-[var(--border2)] rounded px-2 py-1.5 focus:outline-none focus:border-[var(--accent)] cursor-pointer"
            value={sort}
            onChange={e => setSort(e.target.value as SortKey)}
          >
            {SORTS.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <button
            className={cn(BTN_BASE, BTN.primary)}
            style={{ padding: '8px 16px', fontSize: 10 }}
            onClick={() => setShowModal(true)}
          >
            + New Experiment
          </button>
        </div>
      </div>

      {/* Grid */}
      <div className="grid grid-cols-3 gap-4 mb-4">
        {isLoading && [...Array(6)].map((_, i) => <SkeletonCard key={i} />)}

        {isError && (
          <div
            className="col-span-3 font-mono text-[11px] p-6 text-center rounded-lg"
            style={{ color: 'var(--red)', background: 'var(--surface)', border: '1px solid var(--border)' }}
          >
            Failed to load experiments. Check backend connection.
          </div>
        )}

        {!isLoading && !isError && visible.length === 0 && (
          <div
            className="col-span-3 font-mono text-[11px] p-6 text-center rounded-lg"
            style={{ color: 'var(--text3)', background: 'var(--surface)', border: '1px solid var(--border)' }}
          >
            {filter === 'all' ? 'No experiments yet.' : `No ${filter} experiments.`}
          </div>
        )}

        {!isLoading && !isError && visible.map(exp => (
          <ExperimentCard
            key={exp.experiment_id}
            exp={exp}
            expanded={expandedId === exp.experiment_id}
            onToggle={() => toggleExpand(exp.experiment_id)}
            onCancel={() => cancelMutation.mutate(exp.experiment_id)}
          />
        ))}

        {/* New experiment placeholder */}
        {!isLoading && !isError && filter === 'all' && (
          <div
            className="flex flex-col items-center justify-center rounded-md p-4 cursor-pointer opacity-40 hover:opacity-70 transition-opacity"
            style={{ minHeight: 200, background: 'var(--surface2)', border: '1px dashed var(--border)', borderRadius: 6 }}
            onClick={() => setShowModal(true)}
          >
            <div className="text-[28px] text-[var(--text3)] mb-2">+</div>
            <div className="font-mono text-[10px] text-[var(--text3)] text-center">New Experiment</div>
          </div>
        )}
      </div>

      {/* Strategies panel — shown below grid when a card is expanded */}
      {expandedId != null && expandedExp != null && (
        <StrategiesPanel
          experimentId={expandedId}
          experimentName={expandedExp.experiment_name}
        />
      )}

      {/* Modal */}
      {showModal && (
        <NewExperimentModal
          onClose={() => setShowModal(false)}
          onCreated={() => queryClient.invalidateQueries({ queryKey: ['experiments'] })}
        />
      )}
    </div>
  )
}
