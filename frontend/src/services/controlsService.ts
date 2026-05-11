import { http } from '../api/http'

export interface ApiStrategyControlState {
  strategy_id: string
  enabled: boolean
  status: 'live' | 'paper' | 'off'
  reason: string | null
  updated_by: string | null
  updated_at: string | null
}

export interface ApiControlsState {
  kill_switch_active: boolean
  trading_enabled: boolean
  trading_paused: boolean
  trading_mode: 'simulation' | 'paper' | 'live'
  reason: string | null
  updated_by: string | null
  updated_at: string
  strategies: ApiStrategyControlState[]
}

export async function fetchControlsState(): Promise<ApiControlsState> {
  const res = await http.get<{ data: ApiControlsState }>('/api/v1/controls/state')
  return res.data.data
}

export async function activateKillSwitch(reason: string): Promise<void> {
  await http.post('/api/v1/controls/kill-switch', { reason })
}

// NOTE: 'reason' vs 'rationale' difference is intentional — matches backend schemas:
// KillSwitchRequest uses 'reason', RuntimeControlActionRequest uses 'rationale'.
export async function resumeTrading(rationale: string): Promise<void> {
  await http.post('/api/v1/controls/resume', { rationale })
}
