import { http } from '../api/http'

export interface ApiAuditLogEvent {
  event_id: string
  action_type: string
  description: string
  user: string | null
  timestamp: string
  strategy_id: string | null
  rationale: string | null
}

export interface ApiAuditLogPagination {
  page: number
  page_size: number
  total: number
}

export interface ApiAuditLogResponse {
  events: ApiAuditLogEvent[]
  pagination: ApiAuditLogPagination
}

export async function fetchAuditLog(page = 1, pageSize = 20): Promise<ApiAuditLogResponse> {
  const res = await http.get<{ data: ApiAuditLogResponse }>('/api/v1/audit-log', {
    params: { page, page_size: pageSize },
  })
  return res.data.data
}
