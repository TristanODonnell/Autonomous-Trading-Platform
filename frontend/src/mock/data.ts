import type {
  Strategy,
  Holding,
  ActivityItem,
  AuditEntry,
  RiskMetrics,
  SystemHealth,
} from '../types'

// ── Strategies ────────────────────────────────────────────────────────────────

export const mockStrategies: Strategy[] = [
  {
    id: 'STR-00142',
    name: 'MA Crossover v3',
    type: 'MA(10,50)',
    governanceState: 'live',
    sharpe: 1.82,
    cagr: 22.4,
    maxDrawdown: -5.8,
    winRate: 58,
    trades30d: 142,
    allocation: 48000,
    pnl30d: 4210,
    stage: 4,
    enabled: true,
    sparkline: [30, 28, 24, 20, 16, 18, 13, 8, 10, 6, 4, 4],
  },
  {
    id: 'STR-00098',
    name: 'Momentum Factor',
    type: 'Cross-sectional',
    governanceState: 'live',
    sharpe: 1.54,
    cagr: 18.7,
    maxDrawdown: -8.1,
    winRate: 54,
    trades30d: 89,
    allocation: 36000,
    pnl30d: 2840,
    stage: 4,
    enabled: true,
    sparkline: [28, 26, 22, 24, 26, 19, 15, 11, 14, 9, 7, 7],
  },
  {
    id: 'STR-00201',
    name: 'Mean Reversion',
    type: 'Z-Score',
    governanceState: 'live',
    sharpe: 0.94,
    cagr: 9.1,
    maxDrawdown: -14.2,
    winRate: 47,
    trades30d: 312,
    allocation: 24000,
    pnl30d: -620,
    stage: 4,
    enabled: true,
    sparkline: [18, 16, 20, 22, 24, 18, 20, 22, 16, 19, 21, 20],
  },
  {
    id: 'STR-00187',
    name: 'Vol Breakout',
    type: 'ATR Breakout',
    governanceState: 'paper',
    sharpe: 2.11,
    cagr: 28.2,
    maxDrawdown: -12.4,
    winRate: 49,
    trades30d: 204,
    allocation: 20_000,
    pnl30d: 1180,
    stage: 3,
    enabled: true,
    sparkline: [32, 28, 30, 22, 14, 20, 16, 12, 18, 10, 4, 8],
  },
  {
    id: 'STR-00154',
    name: 'RSI Divergence',
    type: 'RSI(14)',
    governanceState: 'research',
    sharpe: 0.71,
    cagr: 6.4,
    maxDrawdown: -18.0,
    winRate: 45,
    trades30d: 48,
    allocation: null,
    pnl30d: -290,
    stage: 2,
    enabled: false,
    sparkline: [20, 22, 18, 24, 30, 20, 26, 32, 22, 28, 32, 30],
  },
]

// ── Holdings ──────────────────────────────────────────────────────────────────

export const mockHoldings: Holding[] = [
  {
    symbol: 'AAPL',
    qty: 120,
    avgPrice: 180.40,
    currentPrice: 187.40,
    value: 22488,
    pnl: 840,
    strategyName: 'MA Cross v3',
  },
  {
    symbol: 'MSFT',
    qty: 55,
    avgPrice: 395.20,
    currentPrice: 412.20,
    value: 22671,
    pnl: 935,
    strategyName: 'Momentum',
  },
  {
    symbol: 'NVDA',
    qty: 40,
    avgPrice: 890.00,
    currentPrice: 872.30,
    value: 34892,
    pnl: -708,
    strategyName: 'Momentum',
  },
  {
    symbol: 'GOOGL',
    qty: 80,
    avgPrice: 168.40,
    currentPrice: 171.10,
    value: 13688,
    pnl: 216,
    strategyName: 'MA Cross v3',
  },
  {
    symbol: 'META',
    qty: 30,
    avgPrice: 502.10,
    currentPrice: 491.80,
    value: 14754,
    pnl: -309,
    strategyName: 'Mean Revert',
  },
  {
    symbol: 'AMZN',
    qty: 65,
    avgPrice: 191.20,
    currentPrice: 198.40,
    value: 12896,
    pnl: 468,
    strategyName: 'Vol Breakout',
  },
]

// ── Equity Curve (Feb–May) ────────────────────────────────────────────────────

export const mockEquityCurve: { date: string; value: number }[] = [
  { date: '2026-02-01', value: 216000 },
  { date: '2026-02-08', value: 219400 },
  { date: '2026-02-15', value: 221800 },
  { date: '2026-02-22', value: 218600 },
  { date: '2026-03-01', value: 223200 },
  { date: '2026-03-08', value: 226500 },
  { date: '2026-03-15', value: 224100 },
  { date: '2026-03-22', value: 229800 },
  { date: '2026-03-29', value: 233600 },
  { date: '2026-04-05', value: 231400 },
  { date: '2026-04-12', value: 237200 },
  { date: '2026-04-19', value: 240800 },
  { date: '2026-04-26', value: 244100 },
  { date: '2026-05-03', value: 246628 },
  { date: '2026-05-09', value: 247832 },
]

// ── Activity Feed ─────────────────────────────────────────────────────────────

export const mockActivity: ActivityItem[] = [
  {
    id: 'act-001',
    text: 'MA Crossover v3 filled BUY AAPL ×120',
    meta: '2 min ago · $187.40 avg · slippage 0.02%',
    type: 'fill',
    timestamp: '2 min ago',
  },
  {
    id: 'act-002',
    text: 'Vol Breakout paper trade: SELL MSFT ×80',
    meta: '11 min ago · $412.20 avg',
    type: 'paper',
    timestamp: '11 min ago',
  },
  {
    id: 'act-003',
    text: 'Feature pipeline delayed 4m 12s',
    meta: '18 min ago · features_v43',
    type: 'warning',
    timestamp: '18 min ago',
  },
  {
    id: 'act-004',
    text: 'Momentum Factor filled SELL GOOGL ×45',
    meta: '34 min ago · $171.10 avg',
    type: 'fill',
    timestamp: '34 min ago',
  },
  {
    id: 'act-005',
    text: 'adjusted_bars_v43 registered',
    meta: '6:31 AM · CA pipeline complete',
    type: 'system',
    timestamp: '6:31 AM',
  },
]

// ── Audit Log ─────────────────────────────────────────────────────────────────

export const mockAuditLog: AuditEntry[] = [
  {
    id: 'aud-001',
    time: '09:42:11',
    text: 'Allocation override applied: Mean Reversion → $12,000',
    actor: 'operator: tristan · reason: DD breach watchlist',
  },
  {
    id: 'aud-002',
    time: '09:18:04',
    text: 'Strategy RSI Divergence paused',
    actor: 'operator: tristan',
  },
  {
    id: 'aud-003',
    time: '08:55:30',
    text: 'Vol Breakout approved for paper trading',
    actor: 'governance: auto-approved · Sharpe 2.11 ≥ 1.5',
  },
  {
    id: 'aud-004',
    time: '07:01:12',
    text: 'adjusted_bars_v43 registered — CA pipeline complete',
    actor: 'system',
  },
  {
    id: 'aud-005',
    time: '06:58:44',
    text: 'features_v43 written — feature pipeline complete',
    actor: 'system',
  },
  {
    id: 'aud-006',
    time: 'Yesterday',
    text: 'Kill switch engaged — manual intervention',
    actor: 'operator: tristan · released 2m 14s later',
  },
  {
    id: 'aud-007',
    time: 'Yesterday',
    text: 'MA Crossover v3 promoted to live trading',
    actor: 'governance: auto-promoted after 30d paper',
  },
]

// ── Risk Metrics ──────────────────────────────────────────────────────────────

export const mockRiskMetrics: RiskMetrics = {
  maxDrawdown: -6.2,
  volatility: 11.4,
  sharpe: 1.61,
  sortino: 2.14,
  beta: 0.72,
  var95: 2840,
}

// ── System Health ─────────────────────────────────────────────────────────────

export const mockSystemHealth: SystemHealth = {
  dataPipeline: 'healthy',
  executionEngine: 'healthy',
  featureStore: 'delayed',
  governance: 'healthy',
}

// ── Portfolio Summary ─────────────────────────────────────────────────────────

export const mockPortfolioSummary = {
  totalValue: 247_832,
  dayPnl: 1_204,
  dayPnlPct: 0.49,
  totalPnl: 31_420,
  totalPnlPct: 14.5,
  cash: 31_420,
  invested: 216_412,
  openPositions: 18,
  activeStrategies: 6,
  liveStrategies: 4,
  paperStrategies: 2,
}

// ── Allocation by Strategy ────────────────────────────────────────────────────

export const mockStrategyAllocation: { name: string; pct: number; color: string }[] = [
  { name: 'MA Crossover v3', pct: 29, color: 'accent' },
  { name: 'Momentum Factor', pct: 22, color: 'blue' },
  { name: 'Mean Reversion', pct: 15, color: 'purple' },
  { name: 'Vol Breakout', pct: 12, color: 'yellow' },
  { name: 'Cash Reserve', pct: 13, color: 'border2' },
]

// ── Sector Exposure ───────────────────────────────────────────────────────────

export const mockSectorAllocation: { sector: string; pct: number; warning?: boolean }[] = [
  { sector: 'Technology', pct: 61, warning: true },
  { sector: 'Consumer Disc.', pct: 18 },
  { sector: 'Financials', pct: 8 },
  { sector: 'Healthcare', pct: 6 },
]

// ── Drawdown Chart ────────────────────────────────────────────────────────────

export const mockDrawdownSeries: { date: string; drawdown: number }[] = [
  { date: '2026-02-01', drawdown: -1.2 },
  { date: '2026-02-08', drawdown: -2.4 },
  { date: '2026-02-15', drawdown: -1.8 },
  { date: '2026-02-22', drawdown: -3.6 },
  { date: '2026-03-01', drawdown: -2.1 },
  { date: '2026-03-08', drawdown: -1.4 },
  { date: '2026-03-15', drawdown: -4.2 },
  { date: '2026-03-22', drawdown: -2.8 },
  { date: '2026-03-29', drawdown: -1.6 },
  { date: '2026-04-05', drawdown: -5.1 },
  { date: '2026-04-12', drawdown: -3.3 },
  { date: '2026-04-19', drawdown: -2.0 },
  { date: '2026-04-26', drawdown: -4.8 },
  { date: '2026-05-03', drawdown: -6.2 },
  { date: '2026-05-09', drawdown: -6.2 },
]
