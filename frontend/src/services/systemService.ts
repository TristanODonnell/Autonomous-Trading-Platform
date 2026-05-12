import { http } from '../api/http'

export interface ApiSystemHealth {
  status: 'healthy' | 'degraded' | 'critical'
  trading_mode: string
  active_strategy_count: number
  alerts: string[]
}

export async function fetchSystemHealth(): Promise<ApiSystemHealth> {
  const res = await http.get<{ data: ApiSystemHealth }>('/api/v1/system/health')
  return res.data.data
}

// Requires operator or admin role. PUT /api/v1/system/trading-mode
export async function updateTradingMode(
  mode: string,
  rationale?: string,
): Promise<void> {
  // Frontend uses 'backtesting' as the UI label; backend expects 'simulation'
  const apiMode = mode === 'backtesting' ? 'simulation' : mode
  await http.put('/api/v1/system/trading-mode', { mode: apiMode, rationale })
}
