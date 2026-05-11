import { http } from '../api/http'

export interface ApiOperatorSettings {
  risk_tolerance: 'low' | 'medium' | 'high'
  max_drawdown_limit: number
  rebalance_frequency: 'daily' | 'weekly' | 'monthly'
  auto_promote_enabled: boolean
  per_strategy_cap: number
}

export interface ApiOperatorSettingsUpdate {
  risk_tolerance?: 'low' | 'medium' | 'high'
  max_drawdown_limit?: number
  rebalance_frequency?: 'daily' | 'weekly' | 'monthly'
  auto_promote_enabled?: boolean
  per_strategy_cap?: number
  reason?: string
}

export async function fetchOperatorSettings(): Promise<ApiOperatorSettings> {
  const res = await http.get<{ data: ApiOperatorSettings }>('/api/v1/settings')
  return res.data.data
}

// Requires admin role. PUT /api/v1/settings → OperatorSettingsUpdateRequest
export async function updateOperatorSettings(updates: ApiOperatorSettingsUpdate): Promise<ApiOperatorSettings> {
  const res = await http.put<{ data: ApiOperatorSettings }>('/api/v1/settings', updates)
  return res.data.data
}

export interface ApiCostModelConfig {
  fixed_commission: number
  slippage_rate: number
  [key: string]: number
}

export interface ApiAdvancedSettings {
  cost_model_configuration: ApiCostModelConfig
}

export async function fetchAdvancedSettings(): Promise<ApiAdvancedSettings> {
  const res = await http.get<{ data: ApiAdvancedSettings }>('/api/v1/settings/advanced')
  return res.data.data
}
