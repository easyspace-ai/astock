/** 公司图谱扩展专用端点与类型; 不动 lib/api。 */
import { ApiError } from '@/lib/api'

// ===== 后端契约 (app/custom/cninfo_graph/store.py) =====

export interface ExecutiveRole {
  role: string
  name: string
}

export interface CompanyProfile {
  symbol: string
  name: string | null
  short_name: string | null
  isin: string | null
  founded_date: string | null
  listed_date: string | null
  website: string | null
  reg_address: string | null
  office_address: string | null
  email: string | null
  phone: string | null
  industry_l1: string | null
  industry_l2: string | null
  market: string | null
  declare_date: string | null
  business_summary: string | null
  highlights: string | null
  risks: string | null
  executives: ExecutiveRole[]
  board_members: string[]
  fetched_at: string
}

/** 巨潮前五大: shares_mn 单位百万股, holding_pct_percent 单位百分数 */
export interface Top5Holder {
  name: string
  shares_mn: number | null
  holding_pct_percent: number | null
  report_date: string | null
}

export interface Top5Snapshot {
  report_date: string | null
  holders: Top5Holder[]
}

/** 东财十大: shares 单位股, *_pct 单位百分数 */
export interface Top10Holder {
  rank: number | null
  name: string
  shares_type: string | null
  shares: number | null
  pct_percent: number | null
  change_shares: number | null
  change_note: string | null
  change_pct: number | null
  report_date: string | null
}

export interface HolderCountRow {
  end_date: string | null
  holder_total_num: number | null
  avg_free_shares: number | null
  hold_focus: string | null
  avg_hold_amt_yuan: number | null
}

export interface HolderResearch {
  source: string
  report_date: string | null
  top10: Top10Holder[]
  top10_float: Array<Top10Holder & { float_pct_percent: number | null }>
  holder_count_history: HolderCountRow[]
  controller: { name: string; pct_percent: number | null } | null
  fetched_at: string
}

/** 东财高管详情: salary_yuan 单位元/年, hold_num 单位股 */
export interface ExecutiveDetail {
  person_code: string | null
  name: string
  position: string | null
  sex: string | null
  age: string | null
  degree: string | null
  salary_yuan: number | null
  hold_num: number | null
  hold_ratio_percent: number | null
  incumbent_date: string | null
  incumbent_time: string | null
  is_dsh: boolean
  is_jsh: boolean
  is_glc: boolean
  resume: string | null
}

export interface ExecutiveSnapshot {
  source: string
  report_date: string | null
  persons: ExecutiveDetail[]
  fetched_at: string
}

export interface Announcement {
  announcement_id: string | null
  title: string | null
  date: string | null
  pdf_url: string | null
  column_ids: string[]
  types: string[]
}

export interface NewsItem {
  id: string
  title: string
  type: number | null
  author: string | null
  source: string | null
  summary: string | null
  publish_time_ms: number | null
  date: string | null
  url: string | null
  pic_url: string | null
}

export type PartStatus = { ok: boolean; at?: string; error?: string; rows?: number }

export interface PartMap {
  profile?: PartStatus
  shareholders?: PartStatus
  holders_top10?: PartStatus
  executives?: PartStatus
  announcements?: PartStatus
  news?: PartStatus
}

export interface CompanyDetailData {
  symbol: string
  profile: CompanyProfile
  shareholders: Top5Snapshot | null
  holders_top10: HolderResearch | null
  executives: ExecutiveSnapshot | null
  announcements: Announcement[]
  news: NewsItem[]
  state: { org_id?: string; last_sync?: string; parts?: PartMap }
}

export interface Overview {
  tracked: string[]
  stored: string[]
  state: Record<string, { org_id?: string; last_sync?: string; parts?: PartMap }>
}

export interface SyncResult {
  symbol: string
  parts: PartMap
}

// ===== 请求 =====

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), 120_000)
  try {
    const res = await fetch(`/api/graph${path}`, {
      ...init,
      headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
      signal: controller.signal,
    })
    const payload = await res.json().catch(() => null)
    if (!res.ok) {
      const detail = (payload as { detail?: unknown } | null)?.detail
      const message =
        typeof detail === 'string'
          ? detail
          : `公司图谱请求失败 (${res.status})`
      throw new ApiError(message, res.status)
    }
    return payload as T
  } catch (error) {
    if (controller.signal.aborted) throw new Error('公司图谱请求超时, 请稍后重试')
    throw error
  } finally {
    window.clearTimeout(timer)
  }
}

export const graphApi = {
  overview: () => request<Overview>(''),
  setTracked: (symbols: string[]) =>
    request<{ ok: boolean; tracked: string[] }>('', {
      method: 'PUT',
      body: JSON.stringify({ symbols }),
    }),
  detail: (symbol: string) => request<CompanyDetailData>(`/${encodeURIComponent(symbol)}`),
  sync: (symbol: string, withNews = true) =>
    request<SyncResult>(`/${encodeURIComponent(symbol)}/sync?with_news=${withNews}`, {
      method: 'POST',
    }),
}
