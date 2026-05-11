import { http } from '../api/http'

export interface ApiPortfolioSummary {
  current_portfolio_value: number
  todays_pnl_amount: number
  todays_pnl_percent: number
  total_pnl_amount: number
  total_pnl_percent: number
  cash_balance: number
}

export interface ApiEquityCurvePoint {
  timestamp: string
  value: number
}

export type ApiEquityCurvePeriod = 'today' | '1w' | '1m' | '3m' | 'ytd'

export interface ApiEquityCurveResponse {
  period: ApiEquityCurvePeriod
  points: ApiEquityCurvePoint[]
  drawdown_points: Array<{ timestamp: string; value: number }>
}

export interface ApiPortfolioRisk {
  portfolio_volatility: number
  beta: number
  value_at_risk_1d_95: number
  average_pairwise_correlation: number
  current_drawdown: number
}

export interface ApiPortfolioPerformance {
  total_return: number
  cagr: number
  sharpe_ratio: number
  sortino_ratio: number
  max_drawdown: number
  volatility: number
  win_rate: number
}

export async function fetchPortfolioSummary(): Promise<ApiPortfolioSummary> {
  const res = await http.get<{ data: ApiPortfolioSummary }>('/api/v1/portfolio/summary')
  return res.data.data
}

export async function fetchEquityCurve(period: ApiEquityCurvePeriod): Promise<ApiEquityCurveResponse> {
  const res = await http.get<{ data: ApiEquityCurveResponse }>('/api/v1/portfolio/equity-curve', {
    params: { period },
  })
  return res.data.data
}

export interface ApiPortfolioHolding {
  symbol: string
  company_name: string
  market_value: number
  quantity: number
  average_entry_price: number
  current_price: number
  todays_change_percent: number
  todays_change_absolute: number
  strategy_id: string
}

export interface ApiAllocationItem {
  name: string
  allocated_capital: number
  percent_of_portfolio: number
}

export interface ApiPortfolioAllocation {
  by_strategy: ApiAllocationItem[]
  by_asset: ApiAllocationItem[]
}

export async function fetchPortfolioHoldings(): Promise<ApiPortfolioHolding[]> {
  const res = await http.get<{ data: { holdings: ApiPortfolioHolding[] } }>('/api/v1/portfolio/holdings')
  return res.data.data.holdings
}

export async function fetchPortfolioAllocation(): Promise<ApiPortfolioAllocation> {
  const res = await http.get<{ data: ApiPortfolioAllocation }>('/api/v1/portfolio/allocation')
  return res.data.data
}

export async function fetchPortfolioRisk(): Promise<ApiPortfolioRisk> {
  const res = await http.get<{ data: ApiPortfolioRisk }>('/api/v1/portfolio/risk')
  return res.data.data
}

export async function fetchPortfolioPerformance(): Promise<ApiPortfolioPerformance> {
  const res = await http.get<{ data: ApiPortfolioPerformance }>('/api/v1/portfolio/performance')
  return res.data.data
}
