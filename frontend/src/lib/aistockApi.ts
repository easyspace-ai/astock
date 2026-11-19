/**
 * Client for the Go aistock sidecar (newstock workbench).
 * Paths are under /api/v1 — Vite proxies to localhost:8787 in dev.
 */

export interface AistockApiMessage {
  role: string
  content: string
}

export interface AistockStreamEvent {
  event: string
  data: unknown
  timestamp?: string
}

export interface AistockReportSummary {
  id: string
  symbol: string
  trade_date: string
  decision?: string | null
  direction?: string | null
  confidence?: number | null
  target_price?: number | null
  stop_loss_price?: number | null
  risk_items?: Array<Record<string, unknown>> | null
  key_metrics?: Array<Record<string, unknown>> | null
  status?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface AistockReportDetail extends AistockReportSummary {
  result_data?: Record<string, unknown> | null
  market_report?: string | null
  sentiment_report?: string | null
  news_report?: string | null
  fundamentals_report?: string | null
  macro_report?: string | null
  smart_money_report?: string | null
  game_theory_report?: string | null
  investment_plan?: string | null
  trader_investment_plan?: string | null
  final_trade_decision?: string | null
}

export interface GlobalIndex {
  name: string
  code: string
  price: number
  change: number
  changePct: number
  updateTime: string
  /** Upstream board: common | america | asia | europe | other */
  region?: string
}

export interface TimelinePoint {
  time: string
  price: number
  avgPrice: number
}

export interface PickerStockResult {
  code: string
  name: string
  price: number
  changePercent: number
  change: number
  volume: number
  amount: number
  turnoverRate: number
  volumeRatio: number
  circulatingMarketCap: number
  totalMarketCap: number
  pe: number
  pb: number
  high: number
  low: number
  open: number
  prevClose: number
  market: string
  timeline?: TimelinePoint[]
  timelineAboveAvgRatio?: number
  /** Momentum scanner */
  momentumRatio?: number
  ma60Distance?: number
  avgTurnover5d?: number
  high20d?: number
  low20d?: number
  ma60?: number
  /** Kunpeng scanner */
  netProfit?: number
  safetyScore?: number
  potentialMultiple?: number
}

export interface PickerResponse {
  strategy: 'end_of_day' | 'momentum' | 'kunpeng'
  total: number
  list: PickerStockResult[]
}

export interface EndOfDayPickerRequest {
  marketCapMin?: number
  marketCapMax?: number
  volumeRatioMin?: number
  changePercentMin?: number
  changePercentMax?: number
  turnoverRateMin?: number
  turnoverRateMax?: number
  excludeST?: boolean
  timelineAboveAvgRatio?: number
}

export interface MomentumPickerRequest {
  momentumThreshold?: number
  trendAboveMA60?: boolean
  avgTurnoverMin?: number
  marketCapMin?: number
  marketCapMax?: number
  excludeST?: boolean
  priceMin?: number
  priceMax?: number
}

export interface KunpengPickerRequest {
  marketCapMin?: number
  marketCapMax?: number
  netProfitMin?: number
  peMin?: number
  peMax?: number
  excludeST?: boolean
  excludeNewStock?: boolean
  priceMin?: number
  priceMax?: number
}

interface ApiEnvelope<T> {
  code: number
  message: string
  data?: T
}

const ANALYSIS_PREFIX = '/api/v1/analysis'

/**
 * Map low-level fetch/proxy failures to actionable copy.
 * Avoid implying "Go is down" when the usual cause is idle/proxy timeout during W6.
 */
export function describeAistockClientError(err: unknown): string {
  if (typeof DOMException !== 'undefined' && err instanceof DOMException && err.name === 'AbortError') {
    return '分析已取消'
  }
  const raw = err instanceof Error ? err.message : String(err ?? '请求失败')
  // Already annotated (avoid double-wrapping across fetch + hook layers)
  if (/（请确认 Go aistock|（代理\/读超时|（连接中断：/.test(raw)) return raw

  const lower = raw.toLowerCase()

  if (/econnrefused|enotfound|sidecar unreachable|aistock sidecar unreachable/.test(lower)) {
    return `${raw}（请确认 Go aistock 服务已在 :8787 运行）`
  }
  if (
    /timeout|timed out|504|gateway timeout|aistock sidecar timeout|proxy.*timeout/.test(lower)
  ) {
    return `${raw}（代理/读超时：W6 预取与多 Agent 分析可能仍在运行，请加大 Vite/FastAPI 超时后重试）`
  }
  if (
    /failed to fetch|networkerror|network error|load failed|err_empty_response|err_connection_reset|err_connection_refused|502|econnreset/.test(
      lower,
    )
  ) {
    return `${raw}（连接中断：多为 SSE 长时间无数据被代理/LB 空闲断开，或 aistock 崩溃；生产查 Go aistock 日志是否 panic，以及是否已开启 SSE keepalive；超时项 AISTOCK_PROXY_READ_TIMEOUT / 本地 VITE_AISTOCK_PROXY_TIMEOUT_MS）`
  }
  return raw
}

function getServiceOrigin(): string {
  const envUrl = (import.meta.env.VITE_AISTOCK_API_URL as string | undefined)?.trim()
  if (envUrl) return envUrl.replace(/\/$/, '')
  return ''
}

function getToken(): string {
  const envToken = (import.meta.env.VITE_AISTOCK_API_TOKEN as string | undefined)?.trim()
  if (envToken) return envToken
  try {
    return (localStorage.getItem('aistock-token') || '').trim()
  } catch {
    return ''
  }
}

function buildHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra || {})
  if (!headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const token = getToken()
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`)
  }
  return headers
}

async function readErrorMessage(response: Response): Promise<string> {
  const contentType = response.headers.get('content-type') || ''
  if (contentType.includes('application/json')) {
    try {
      const data = (await response.json()) as { detail?: string; message?: string }
      return data?.detail || data?.message || response.statusText
    } catch {
      return response.statusText
    }
  }
  try {
    const text = await response.text()
    return text || response.statusText
  } catch {
    return response.statusText
  }
}

async function aistockFetch(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, {
      ...init,
      cache: init?.cache ?? 'no-store',
      headers: buildHeaders(init?.headers),
    })
  } catch (err) {
    throw new Error(describeAistockClientError(err))
  }
}

async function aistockRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${getServiceOrigin()}${path}`
  const response = await aistockFetch(url, init)
  if (!response.ok) {
    throw new Error(describeAistockClientError(new Error(await readErrorMessage(response))))
  }
  return response.json() as Promise<T>
}

export async function envelopeRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${getServiceOrigin()}${path}`
  let response: Response
  try {
    response = await fetch(url, {
      ...init,
      cache: init?.cache ?? 'no-store',
      headers: buildHeaders({
        Accept: 'application/json',
        ...(init?.headers || {}),
      }),
    })
  } catch (err) {
    throw new Error(describeAistockClientError(err))
  }
  if (!response.ok) {
    throw new Error(describeAistockClientError(new Error(await readErrorMessage(response))))
  }
  const payload = (await response.json()) as ApiEnvelope<T>
  if (payload.code !== 0) {
    throw new Error(payload.message || '接口返回错误')
  }
  return payload.data as T
}

/** Generic SSE reader shared by the legacy chat stream and per-job event subscription. */
async function streamSse(
  url: string,
  opts: { onEvent: (event: AistockStreamEvent) => void; signal?: AbortSignal; method?: string; body?: unknown },
): Promise<void> {
  let response: Response
  try {
    response = await fetch(url, {
      method: opts.method ?? 'GET',
      signal: opts.signal,
      headers: buildHeaders(opts.body != null ? { 'Content-Type': 'application/json' } : undefined),
      body: opts.body != null ? JSON.stringify(opts.body) : undefined,
    })
  } catch (err) {
    if (opts.signal?.aborted) throw err
    throw new Error(describeAistockClientError(err))
  }

  if (!response.ok) {
    throw new Error(describeAistockClientError(new Error(await readErrorMessage(response))))
  }
  if (!response.body) {
    throw new Error('分析流不可用')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let currentEvent = 'message'
  let currentData: string[] = []

  const emit = () => {
    if (!currentData.length) return
    const raw = currentData.join('\n')
    let parsed: unknown = raw
    if (raw !== '[DONE]') {
      try {
        parsed = JSON.parse(raw)
      } catch {
        parsed = raw
      }
    }
    opts.onEvent({ event: currentEvent, data: parsed })
  }

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (value) {
        buffer += decoder.decode(value, { stream: !done })
      }
      const lines = buffer.split(/\r?\n/)
      buffer = lines.pop() ?? ''

      for (const line of lines) {
        if (!line) {
          emit()
          currentEvent = 'message'
          currentData = []
          continue
        }
        if (line.startsWith('event:')) {
          currentEvent = line.slice(6).trim() || 'message'
          continue
        }
        if (line.startsWith('data:')) {
          currentData.push(line.slice(5).trimStart())
        }
      }
      if (done) break
    }
    emit()
  } catch (err) {
    if (opts.signal?.aborted) throw err
    throw new Error(describeAistockClientError(err))
  } finally {
    reader.releaseLock()
  }
}

export async function streamTradingAnalysis(opts: {
  messages: AistockApiMessage[]
  selectedAnalysts?: string[]
  onEvent: (event: AistockStreamEvent) => void
  signal?: AbortSignal
}): Promise<void> {
  await streamSse(`${getServiceOrigin()}${ANALYSIS_PREFIX}/chat/completions`, {
    onEvent: opts.onEvent,
    signal: opts.signal,
    method: 'POST',
    body: { messages: opts.messages, stream: true, selected_analysts: opts.selectedAnalysts },
  })
}

export interface JobSummary {
  job_id: string
  status: string
  symbol?: string
  trade_date?: string
  error?: string
  created_at: string
  updated_at: string
}

export interface JobStatusResponse {
  job_id: string
  status: string
  symbol?: string
  trade_date?: string
  error?: string
  created_at: string
  updated_at: string
}

/** Async start: enqueues a background job and returns its id; subscribe via /jobs/:id/events. */
export async function startAnalysisAsync(opts: {
  messages: AistockApiMessage[]
  selectedAnalysts?: string[]
}): Promise<{ job_id: string; status: string; created_at?: string }> {
  return aistockRequest(`${ANALYSIS_PREFIX}/analyze/async`, {
    method: 'POST',
    body: JSON.stringify({
      messages: opts.messages,
      stream: true,
      selected_analysts: opts.selectedAnalysts,
    }),
  })
}

export function getJobStatus(jobId: string) {
  return aistockRequest<JobStatusResponse>(`${ANALYSIS_PREFIX}/jobs/${encodeURIComponent(jobId)}`)
}

/** In-memory job result (sidecar restart drops this; fall back to /reports/:id). */
export function getJobResult<T = Record<string, unknown>>(jobId: string) {
  return aistockRequest<{ job_id: string; status: string; result: T | null }>(
    `${ANALYSIS_PREFIX}/jobs/${encodeURIComponent(jobId)}/result`,
  )
}

export function listAnalysisJobs() {
  return aistockRequest<{ jobs: JobSummary[] }>(`${ANALYSIS_PREFIX}/jobs`)
}

export function stopAnalysisJob(jobId: string) {
  return aistockRequest<{ job_id: string; status: string }>(
    `${ANALYSIS_PREFIX}/jobs/${encodeURIComponent(jobId)}/stop`,
    { method: 'POST' },
  )
}

/** Subscribe to a background job's event stream (replays history, then live events). */
export function subscribeJobEvents(opts: {
  jobId: string
  onEvent: (event: AistockStreamEvent) => void
  signal?: AbortSignal
}): Promise<void> {
  return streamSse(`${getServiceOrigin()}${ANALYSIS_PREFIX}/jobs/${encodeURIComponent(opts.jobId)}/events`, {
    onEvent: opts.onEvent,
    signal: opts.signal,
  })
}

export async function getTradingReports(
  symbol?: string,
  skip = 0,
  limit = 20,
): Promise<{ total: number; reports: AistockReportSummary[] }> {
  const params = new URLSearchParams()
  if (symbol) params.append('symbol', symbol)
  params.append('skip', String(skip))
  params.append('limit', String(limit))
  const data = await aistockRequest<{ total: number; reports: AistockReportSummary[] | null }>(
    `${ANALYSIS_PREFIX}/reports?${params.toString()}`,
  )
  return {
    total: data.total ?? 0,
    reports: Array.isArray(data.reports) ? data.reports : [],
  }
}

export async function getTradingReport(reportId: string): Promise<AistockReportDetail> {
  return aistockRequest<AistockReportDetail>(`${ANALYSIS_PREFIX}/reports/${reportId}`)
}

export interface AgentPromptEntry {
  id: string
  name: string
  label: string
  prompt: string
  overridden: boolean
}

export function listAgentPrompts() {
  return aistockRequest<AgentPromptEntry[]>(`${ANALYSIS_PREFIX}/prompts`)
}

export function getAgentPrompt(id: string) {
  return aistockRequest<AgentPromptEntry>(`${ANALYSIS_PREFIX}/prompts/${encodeURIComponent(id)}`)
}

export function updateAgentPrompt(id: string, prompt: string) {
  return aistockRequest<AgentPromptEntry>(`${ANALYSIS_PREFIX}/prompts/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify({ prompt }),
  })
}

/** @deprecated Prefer `@/lib/marketApi` — kept for callers outside the Market module. */
export function getGlobalIndexes() {
  return envelopeRequest<GlobalIndex[]>('/api/v1/market/global-indexes')
}

export function pickEndOfDay(req: EndOfDayPickerRequest) {
  return envelopeRequest<PickerResponse>('/api/v1/picker/end-of-day', {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

export function pickMomentum(req: MomentumPickerRequest) {
  return envelopeRequest<PickerResponse>('/api/v1/picker/momentum', {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

export function pickKunpeng(req: KunpengPickerRequest) {
  return envelopeRequest<PickerResponse>('/api/v1/picker/kunpeng', {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

/** AI strategy chat stream — POST /api/v1/picker/ai/chat/stream (SSE data: lines). */
export async function streamPickerAiChat(opts: {
  messages: AistockApiMessage[]
  onChunk: (content: string) => void
  signal?: AbortSignal
}): Promise<void> {
  let response: Response
  try {
    response = await fetch(`${getServiceOrigin()}/api/v1/picker/ai/chat/stream`, {
      method: 'POST',
      signal: opts.signal,
      headers: buildHeaders(),
      body: JSON.stringify({ messages: opts.messages }),
    })
  } catch (err) {
    if (opts.signal?.aborted) throw err
    throw new Error(describeAistockClientError(err))
  }
  if (!response.ok) {
    throw new Error(describeAistockClientError(new Error(await readErrorMessage(response))))
  }
  if (!response.body) {
    throw new Error('AI 流不可用')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (value) buffer += decoder.decode(value, { stream: !done })
      const lines = buffer.split(/\r?\n/)
      buffer = lines.pop() ?? ''
      for (const line of lines) {
        if (!line.startsWith('data:')) continue
        const data = line.slice(5).trim()
        if (!data || data === '[DONE]') continue
        try {
          const parsed = JSON.parse(data) as { content?: string }
          if (parsed.content) opts.onChunk(parsed.content)
        } catch {
          /* skip */
        }
      }
      if (done) break
    }
  } catch (err) {
    if (opts.signal?.aborted) throw err
    throw new Error(describeAistockClientError(err))
  } finally {
    reader.releaseLock()
  }
}
