export type GovernanceState = 'proposed' | 'research' | 'paper' | 'live' | 'rejected' | 'retired'
export type Environment = 'simulation' | 'paper' | 'live'
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
