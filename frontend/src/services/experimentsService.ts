import { http } from '../api/http'
import type { ExperimentSummary, ExperimentStrategy } from '../types'

export interface ApiExperimentCreateRequest {
  name: string
  experiment_type: string
  symbols: string[]
  start_date: string
  end_date: string
  price_basis: 'RAW' | 'ADJUSTED'
  strategy_count: number
  parameter_ranges: Record<string, unknown>
}

export async function fetchExperiments(): Promise<ExperimentSummary[]> {
  const res = await http.get<{ data: { experiments: ExperimentSummary[] } }>('/api/v1/experiments')
  return res.data.data.experiments
}

export async function fetchExperimentStrategies(experimentId: string): Promise<ExperimentStrategy[]> {
  const res = await http.get<{ data: { strategies: ExperimentStrategy[] } }>(
    `/api/v1/experiments/${experimentId}/strategies`,
  )
  return res.data.data.strategies
}

export async function createExperiment(
  payload: ApiExperimentCreateRequest,
): Promise<{ experiment_id: string; status: string }> {
  const res = await http.post<{ data: { experiment_id: string; status: string } }>(
    '/api/v1/experiments',
    payload,
  )
  return res.data.data
}

export async function cancelExperiment(experimentId: string): Promise<void> {
  await http.post(`/api/v1/experiments/${experimentId}/cancel`)
}
