import { http } from '../api/http'

export type SlippageModel = 'fixed' | 'volume_based' | 'spread'
export type TransactionCostModel = 'per_share' | 'per_trade' | 'notional_pct'

export interface ApiSettingsControlMetadataItem {
  value: boolean | string | number
  label: string
  description: string
  source: string
}

export interface ApiDeprecatedSettingMetadataItem {
  value: boolean | string | number
  status: string
  ignored_for: string
  active_source: string
}

export interface ApiOperatorSettingsMetadata {
  source_of_truth?: {
    automation_controls_source?: string
    promotion_thresholds_source?: string
    allocation_targets_source?: string
  }
  automation_controls?: {
    auto_promote_enabled?: ApiSettingsControlMetadataItem
    auto_demote_on_breach?: ApiSettingsControlMetadataItem
    auto_rebalance_enabled?: ApiSettingsControlMetadataItem
    rebalance_frequency?: ApiSettingsControlMetadataItem
  }
  deprecated_or_ignored_settings?: {
    min_sharpe_for_promotion?: ApiDeprecatedSettingMetadataItem
    min_paper_trading_period_days?: ApiDeprecatedSettingMetadataItem
  }
}

export interface ApiOperatorSettings {
  risk_tolerance: 'low' | 'medium' | 'high'
  max_drawdown_limit: number
  max_strategy_drawdown: number
  rebalance_frequency: 'daily' | 'weekly' | 'monthly'
  auto_promote_enabled: boolean
  auto_rebalance_enabled: boolean
  min_sharpe_for_promotion: number
  min_paper_trading_period_days: number
  auto_demote_on_breach: boolean
  notify_drawdown_alerts: boolean
  notify_strategy_promotion_events: boolean
  notify_pipeline_failures: boolean
  per_strategy_cap: number
  target_portfolio_volatility: number
  slippage_model: SlippageModel
  transaction_cost_model: TransactionCostModel
  metadata?: ApiOperatorSettingsMetadata
}

export interface ApiOperatorSettingsUpdate {
  risk_tolerance?: 'low' | 'medium' | 'high'
  max_drawdown_limit?: number
  max_strategy_drawdown?: number
  rebalance_frequency?: 'daily' | 'weekly' | 'monthly'
  auto_promote_enabled?: boolean
  auto_rebalance_enabled?: boolean
  auto_demote_on_breach?: boolean
  notify_drawdown_alerts?: boolean
  notify_strategy_promotion_events?: boolean
  notify_pipeline_failures?: boolean
  per_strategy_cap?: number
  target_portfolio_volatility?: number
  slippage_model?: SlippageModel
  transaction_cost_model?: TransactionCostModel
  reason?: string
}

export interface ApiLatestDatasetVersion {
  dataset_version_id: string
  dataset_name: string
  created_at: string
  source: string
  price_basis: string
  interval: string
  schema_version: string
  symbol_coverage: number | null
  date_coverage_start: string | null
  date_coverage_end: string | null
  validation_status: string
}

export interface ApiLatestFeatureVersion {
  dataset_version_id: string
  feature_name: string
  dataset_name: string
  created_at: string
  schema_version: string
  source_dataset_version: string
  underlying_price_basis: string
  symbol_coverage: number | null
  date_coverage_start: string | null
  date_coverage_end: string | null
  validation_status: string
}

export async function fetchLatestDatasetVersion(): Promise<ApiLatestDatasetVersion | null> {
  const res = await http.get<ApiLatestDatasetVersion | null>('/api/v1/metadata/dataset-versions/latest')
  return res.data
}

export async function fetchLatestFeatureVersion(): Promise<ApiLatestFeatureVersion | null> {
  const res = await http.get<ApiLatestFeatureVersion | null>('/api/v1/metadata/feature-dataset-versions/latest')
  return res.data
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
