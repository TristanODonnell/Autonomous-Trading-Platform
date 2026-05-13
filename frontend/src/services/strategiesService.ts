import { http } from '../api/http'

export interface ApiStrategyListItem {
  strategy_id: string
  display_name: string
  strategy_type: string
  status: 'live' | 'paper' | 'research' | 'off'
  current_return: number
  sharpe_ratio: number
  max_drawdown: number
  win_rate: number
  trade_count: number
  composite_score: number
}

export async function fetchAllStrategies(status?: string): Promise<ApiStrategyListItem[]> {
  const params = status ? { status } : {}
  const res = await http.get<{ data: { strategies: ApiStrategyListItem[] } }>('/api/v1/strategies', { params })
  return res.data.data.strategies
}

export async function updateStrategyEnabled(
  strategyId: string,
  enabled: boolean,
  reason: string,
): Promise<void> {
  await http.put(`/api/v1/strategies/${strategyId}/enabled`, { enabled, reason })
}

export async function transitionStrategyGovernance(
  strategyId: string,
  toState: string,
  reason: string,
): Promise<void> {
  await http.post(`/api/v1/strategies/${strategyId}/governance/transition`, {
    to_state: toState,
    reason,
  })
}

export interface ApiStrategyAllocationState {
  strategy_id: string
  display_name: string
  allocation_pct: number | null
  allocated_capital: number | null
  total_portfolio_capital: number
  is_overridden: boolean
  overridden_by: string | null
  reason: string | null
  updated_at: string | null
}

export async function fetchStrategyAllocations(): Promise<ApiStrategyAllocationState[]> {
  const res = await http.get<{ data: { strategies: ApiStrategyAllocationState[] } }>(
    '/api/v1/strategies/allocations',
  )
  return res.data.data.strategies
}

export async function updateStrategyAllocation(
  strategyId: string,
  allocationPct: number,
  reason: string,
): Promise<void> {
  await http.put(`/api/v1/strategies/${strategyId}/allocation`, {
    allocation_pct: allocationPct,
    reason,
  })
}

export interface ApiActiveStrategy {
  strategy_id: string
  display_name: string
  strategy_type: string
  status: 'live' | 'paper' | 'off'
  todays_return: number
  trade_count_today: number
  allocated_capital: number
  enabled: boolean
}

export async function fetchActiveStrategies(): Promise<ApiActiveStrategy[]> {
  const res = await http.get<{ data: { strategies: ApiActiveStrategy[] } }>('/api/v1/strategies/active')
  return res.data.data.strategies
}
