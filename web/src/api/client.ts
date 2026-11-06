export interface EventItem {
  id?: number
  name: string
  name_norm?: string
  start_date: string
  end_date: string
  venue: string
  price_text: string
  is_free: boolean | null
  category: string
  genre?: string
  price_tier?: string
  price_tier_label?: string
  derived_tags?: string[]
  this_weekend?: boolean
  days_left?: number
  url: string
  source: string
  deadline?: string
  has_goods: boolean | null
  note?: string
  extra?: Record<string, any>
}

export interface WeekendGroup {
  label: string
  count: number
  items: EventItem[]
}

export interface GroupedEventsResponse {
  city: string
  data_city?: string
  today: string
  window_days: number
  total_active: number
  weekends: WeekendGroup[]
  longrun: EventItem[]
  expo: EventItem[]
  urgent: EventItem[]
  unplaced: EventItem[]
}

export interface ModelListResponse {
  ok: boolean
  models: string[]
  count: number
  current: string
  note: string
}

export interface TaskStatus {
  running: boolean
  task_type: string | null
  city: string | null
  started_at: string | null
  last_result?: Record<string, any>
}

export interface SourceProbeItem {
  name: string
  enabled: boolean
  ok: boolean
  status: number
  bytes: number
  note: string
  url: string
}

export interface ProbeResponse {
  city: string
  ok_count: number
  fail_count: number
  total: number
  sources: SourceProbeItem[]
  llm_probe: Array<{ protocol: string; ok: boolean; note: string }>
}

export interface PresetsResponse {
  cities: Array<{ name: string; adcode: string; damai_city: string; bendibao_city: string; douban_city: string }>
  providers: Array<{ name: string; base_url: string; mode: string; model: string }>
  genres: Array<{ id: string; label: string }>
}

export interface GenrePref {
  id: string
  label: string
  enabled: boolean
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
    ...options,
  })
  if (!res.ok) {
    let errMessage = `HTTP ${res.status}`
    try {
      const data = await res.json()
      if (data.detail) errMessage = data.detail
    } catch {}
    throw new Error(errMessage)
  }
  return res.json()
}

export const api = {
  getPresets: () => request<PresetsResponse>('/api/presets'),
  getGenres: () => request<{ genres: GenrePref[] }>('/api/genres'),
  getPriceTiers: () => request<{ tiers: Array<{ id: string; label: string }> }>('/api/price-tiers'),
  updateGenres: (disabled: string[]) =>
    request<{ message: string; genres: GenrePref[] }>('/api/genres', {
      method: 'PUT',
      body: JSON.stringify({ disabled }),
    }),
  getGroupedEvents: (city?: string) => request<GroupedEventsResponse>(`/api/events/grouped${city ? `?city=${encodeURIComponent(city)}` : ''}`),
  getStats: () => request<any>('/api/events/stats'),
  getRuns: (limit = 20, offset = 0) => request<{ total: number; items: any[] }>(`/api/runs?limit=${limit}&offset=${offset}`),
  getReports: () => request<{ reports: any[] }>('/api/reports'),
  getReportContent: (date: string) => request<{ date: string; content: string }>(`/api/reports/${date}`),
  getConfig: () => request<{ sections: Record<string, any>; sources: Record<string, string> }>('/api/config'),
  revealSecret: (section = 'llm', key = 'key') =>
    request<{ value: string; configured: boolean }>(`/api/config/secret?section=${section}&key=${key}`),
  fetchModels: (payload: { base_url?: string; key?: string; mode?: string; provider?: string }) =>
    request<ModelListResponse>('/api/llm/models', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateConfig: (sections: Record<string, any>, sources?: Record<string, string>) =>
    request<any>('/api/config', {
      method: 'PUT',
      body: JSON.stringify({ sections, sources }),
    }),
  getStatus: () => request<TaskStatus>('/api/radar/status'),
  triggerRun: (payload: { city?: string; dry_run?: boolean; force_push?: boolean; channel?: string }) =>
    request<any>('/api/radar/run', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  triggerPlan: (payload: { city?: string }) =>
    request<any>('/api/radar/plan', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  triggerProbe: (payload: { city?: string }) =>
    request<ProbeResponse>('/api/radar/probe', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  triggerSelfTest: () =>
    request<{ exit_code: number; success: boolean }>('/api/radar/self-test', {
      method: 'POST',
    }),
  clearLogs: () =>
    request<{ ok: boolean }>('/api/radar/logs/clear', {
      method: 'POST',
    }),
  testPush: (payload: { channel: string; title?: string; body?: string; webhook?: string; secret?: string; pushkey?: string }) =>
    request<any>('/api/push/test', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  getPushChannels: () => request<{ channels: string[] }>('/api/push/channels'),
}
