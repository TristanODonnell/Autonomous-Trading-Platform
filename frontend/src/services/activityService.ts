import { http } from '../api/http'

export interface ApiActivityItem {
  event_type: string
  description: string
  timestamp: string
  severity: 'info' | 'warning' | 'error' | 'critical'
}

export async function fetchRecentActivity(limit = 10): Promise<ApiActivityItem[]> {
  const res = await http.get<{ data: { items: ApiActivityItem[] } }>('/api/v1/activity/recent', {
    params: { limit },
  })
  return res.data.data.items
}
