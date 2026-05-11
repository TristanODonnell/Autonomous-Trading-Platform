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

export async function fetchAllStrategies(): Promise<ApiStrategyListItem[]> {
  const res = await http.get<{ data: { strategies: ApiStrategyListItem[] } }>('/api/v1/strategies')
  return res.data.data.strategies
}

export async function updateStrategyEnabled(
  strategyId: string,
  enabled: boolean,
  reason: string,
): Promise<void> {
  await http.put(`/api/v1/strategies/${strategyId}/enabled`, { enabled, reason })
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
