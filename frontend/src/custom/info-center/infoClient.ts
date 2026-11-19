/** 资讯中心扩展端点与类型；不修改核心 lib/api。 */
import { ApiError } from '@/lib/api'

export interface AnnouncementEvent {
  event_id: string
  symbol: string
  code: string
  name: string
  title: string
  ann_date: string
  ann_time: string | null
  disclosed_at: string | null
  time_precision: string
  biz_date: string
  session: string
  type_primary: string
  type_domain: string | null
  is_material: boolean
  is_correction: boolean
  sources: string[]
  ids: Record<string, string>
  variants: Array<{ source: string; id: string; title: string; url: string; time: string }>
  pdf_url: string
  detail_url: string
  industry_l1: string | null
  match_status: string
  match_score: number
  match_note: string
}

export interface NewsEvent {
  event_id: string
  event_kind: string
  news_kind: string
  symbol: string
  code: string
  name: string
  sector: string | null
  title: string
  summary: string
  content: string
  media: string
  publish_time: string
  publish_date: string
  url: string
  sources: string[]
  source_primary: string
  match_status: string
  match_type: string
  heat: number
}

export interface FlashEvent {
  event_id: string
  title: string
  summary: string
  publish_time: string
  publish_date: string
  url: string
  sec_codes: string[]
  sec_names: string[]
  sector: string | null
  match_type: string
  sources: string[]
}

export interface PageResult<T> {
  total: number
  page: number
  size: number
  items: T[]
}

export interface WatchlistItem {
  code: string
  name: string
  aliases: string[]
}

export interface Watchlist {
  sectors: Record<string, WatchlistItem[]>
}

export interface TimelineResult {
  code: string
  symbol: string
  name: string
  days: number
  since: string
  counts: Record<string, number>
  events: Array<AnnouncementEvent & NewsEvent & FlashEvent & { event_kind: string }>
}

export interface InfoStats {
  totals: { announcements: number; news: number; flash: number }
  announcements: { both: number; cninfo_only: number; em_only: number; material: number }
  news: { news: number; report: number; sources: Record<string, number> }
  flash: { hit: number; total: number }
  sectors: Record<string, number>
  dates: Record<string, { dates: number; date_from: string | null; date_to: string | null }>
}

async function request<T>(
  path: string,
  params?: Record<string, string | number | boolean | undefined>,
  init?: RequestInit,
  timeoutMs = 30_000,
): Promise<T> {
  const url = new URL(`/api/info${path}`, window.location.origin)
  Object.entries(params ?? {}).forEach(([key, value]) => {
    if (value !== undefined && value !== '') url.searchParams.set(key, String(value))
  })
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const res = await fetch(url.toString(), { ...init, signal: controller.signal })
    const payload = await res.json().catch(() => null)
    if (!res.ok) {
      const detail = (payload as { detail?: unknown } | null)?.detail
      throw new ApiError(typeof detail === 'string' ? detail : `资讯请求失败 (${res.status})`, res.status)
    }
    return payload as T
  } catch (error) {
    if (controller.signal.aborted) throw new Error('资讯请求超时，请稍后重试')
    throw error
  } finally {
    window.clearTimeout(timer)
  }
}

export const infoApi = {
  announcements: (params: Record<string, string | number | boolean | undefined>) =>
    request<PageResult<AnnouncementEvent>>('/announcements', params),
  announcement: (id: string) => request<AnnouncementEvent>(`/announcements/${encodeURIComponent(id)}`),
  news: (params: Record<string, string | number | boolean | undefined>) =>
    request<PageResult<NewsEvent>>('/news', params),
  newsDetail: (id: string) => request<NewsEvent>(`/news/${encodeURIComponent(id)}`),
  flash: (params: Record<string, string | number | boolean | undefined>) =>
    request<PageResult<FlashEvent>>('/flash', params),
  timeline: (code: string, days: number, kinds?: string) =>
    request<TimelineResult>('/timeline', { code, days, kinds }),
  stats: (date?: string) => request<InfoStats>('/stats', { date }),
  watchlist: () => request<Watchlist>('/watchlist'),
  health: () => request<Record<string, unknown>>('/health'),
  sync: (kinds: string, live = false, date?: string) =>
    request<Record<string, unknown>>('/sync', { kinds, live, date }, { method: 'POST' }, 360_000),
  reload: () => request<{ ok: boolean }>('/reload', undefined, { method: 'POST' }),
}

export const SOURCE_LABEL: Record<string, string> = {
  cninfo: '巨潮',
  em: '东财',
  ths: '同花顺',
  ths_flash: '同花顺快讯',
  legacy: '历史导入',
}

export const KIND_LABEL: Record<string, string> = {
  announcement: '公告',
  news: '新闻',
  report: '研报',
  flash: '快讯',
}

export function sourceText(sources: string[] | undefined): string {
  const list = sources ?? []
  if (list.includes('cninfo') && list.includes('em')) return '双源'
  return list.map(s => SOURCE_LABEL[s] || s).join(' / ') || '未知'
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  const text = value.replace('T', ' ')
  return text.length > 16 ? text.slice(0, 16) : text
}
