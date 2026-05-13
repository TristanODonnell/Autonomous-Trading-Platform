export type GovernanceState = 'proposed' | 'research' | 'paper' | 'live' | 'rejected' | 'retired'
export type ExperimentType = 'backtest' | 'parameter_sweep' | 'ab_comparison' | 'rolling_window'
export type ExperimentStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface ExperimentSummary {
  experiment_id: string
  experiment_name: string
  experiment_type: ExperimentType
  status: ExperimentStatus
  created_at: string
  symbols: string[]
  start_date: string | null
  end_date: string | null
  dataset_version: string | null
  total_strategies: number
  strategies_passed_filters: number
  best_sharpe: number | null
  best_return: number | null
  progress: number | null
}

export interface ExperimentStrategy {
  strategy_id: string
  sharpe_ratio: number
  total_return: number
  max_drawdown: number
  simulation_stage: string | null
  governance_state: string
  composite_score: number
  status: string
}
export type Environment = 'backtesting' | 'paper' | 'live'
export type SimulationStage = 1 | 2 | 3 | 4

export interface Strategy {
  id: string
  name: string
  type: string
  governanceState: GovernanceState
  sharpe: number
  cagr: number
  maxDrawdown: number
  winRate: number
  trades30d: number
  allocation: number | null
  pnl30d: number
  stage: SimulationStage
  enabled: boolean
  sparkline: number[]
}

export interface Holding {
  symbol: string
  qty: number
  avgPrice: number
  currentPrice: number
  value: number
  pnl: number
  strategyName: string
}

export interface ActivityItem {
  id: string
  text: string
  meta: string
  type: 'fill' | 'paper' | 'warning' | 'system'
  timestamp: string
}

export interface AuditEntry {
  id: string
  time: string
  text: string
  actor: string
}

export interface RiskMetrics {
  maxDrawdown: number
  volatility: number
  sharpe: number
  sortino: number
  beta: number
  var95: number
}

export interface SystemHealth {
  dataPipeline: 'healthy' | 'delayed' | 'error'
  executionEngine: 'healthy' | 'delayed' | 'error'
  featureStore: 'healthy' | 'delayed' | 'error'
  governance: 'healthy' | 'delayed' | 'error'
}
