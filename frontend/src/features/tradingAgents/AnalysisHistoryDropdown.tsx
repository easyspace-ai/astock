import { useCallback, useEffect, useRef, useState } from 'react'
import { History, Loader2 } from 'lucide-react'
import {
  getTradingReport,
  getTradingReports,
  type AistockReportDetail,
  type AistockReportSummary,
} from '@/lib/aistockApi'
import { api } from '@/lib/api'
import { toast } from '@/components/Toast'
import { cn } from '@/lib/cn'
import { useAnalysisStore } from './analysisStore'
import { normalizeTradePrice } from './reportText'
import { agentReportsFromAnalysisReport } from './resolveAgentReport'
import type { AnalysisReport, KeyMetric, RiskItem } from './types'

function asRiskItems(raw: Array<Record<string, unknown>> | null | undefined): RiskItem[] {
  if (!Array.isArray(raw)) return []
  return raw
    .map((item) => {
      const level = String(item.level ?? 'medium').toLowerCase()
      return {
        name: String(item.name ?? item.title ?? ''),
        level: (level === 'high' || level === 'low' ? level : 'medium') as RiskItem['level'],
        description: item.description != null ? String(item.description) : undefined,
      }
    })
    .filter((item) => item.name.trim())
}

function asKeyMetrics(raw: Array<Record<string, unknown>> | null | undefined): KeyMetric[] {
  if (!Array.isArray(raw)) return []
  return raw
    .map((item) => {
      const status = String(item.status ?? 'neutral').toLowerCase()
      return {
        name: String(item.name ?? item.title ?? ''),
        value: String(item.value ?? '—'),
        status: (status === 'good' || status === 'bad' ? status : 'neutral') as KeyMetric['status'],
      }
    })
    .filter((item) => item.name.trim())
}

function detailToAnalysisReport(detail: AistockReportDetail): AnalysisReport {
  const fromResult = (detail.result_data ?? {}) as Partial<AnalysisReport>
  return {
    ...fromResult,
    symbol: detail.symbol || fromResult.symbol || '',
    trade_date: detail.trade_date || fromResult.trade_date || '',
    decision: detail.decision ?? fromResult.decision ?? undefined,
    direction: detail.direction ?? fromResult.direction ?? undefined,
    target_price: detail.target_price ?? fromResult.target_price ?? undefined,
    stop_loss_price: detail.stop_loss_price ?? fromResult.stop_loss_price ?? undefined,
    market_report: detail.market_report ?? fromResult.market_report ?? undefined,
    sentiment_report: detail.sentiment_report ?? fromResult.sentiment_report ?? undefined,
    news_report: detail.news_report ?? fromResult.news_report ?? undefined,
    fundamentals_report: detail.fundamentals_report ?? fromResult.fundamentals_report ?? undefined,
    macro_report: detail.macro_report ?? fromResult.macro_report ?? undefined,
    smart_money_report: detail.smart_money_report ?? fromResult.smart_money_report ?? undefined,
    game_theory_report: detail.game_theory_report ?? fromResult.game_theory_report ?? undefined,
    investment_plan: detail.investment_plan ?? fromResult.investment_plan ?? undefined,
    trader_investment_plan: detail.trader_investment_plan ?? fromResult.trader_investment_plan ?? undefined,
    final_trade_decision: detail.final_trade_decision ?? fromResult.final_trade_decision ?? undefined,
  }
}

/** Best-effort name from report payload (Go uses instrument_context.company_name). */
function extractStockName(detail: AistockReportDetail, report: AnalysisReport): string {
  const ctx = report.instrument_context
  const fromTyped = (ctx?.company_name || ctx?.security_name || '').trim()
  if (fromTyped) return fromTyped

  const raw = detail.result_data as Record<string, unknown> | null | undefined
  if (raw && typeof raw === 'object') {
    const nested = raw.instrument_context as Record<string, unknown> | undefined
    const candidates = [
      nested?.company_name,
      nested?.security_name,
      nested?.stock_name,
      raw.company_name,
      raw.security_name,
      raw.stock_name,
      raw.stockName,
    ]
    for (const c of candidates) {
      if (typeof c === 'string' && c.trim()) return c.trim()
    }
  }
  return ''
}

/** Resolve display name via TickFlow instruments when report has no company name. */
async function resolveStockName(symbol: string, fromReport: string): Promise<string> {
  if (fromReport) return fromReport
  const sym = symbol.trim()
  if (!sym) return ''
  try {
    const { names } = await api.instrumentNames([sym])
    const n = names?.[sym]?.trim()
    if (n) return n
  } catch {
    // ignore — fall through to search
  }
  try {
    const { results } = await api.instrumentSearch(sym, 5)
    const hit = results?.find((r) => r.symbol === sym || r.code === sym)
    if (hit?.name?.trim()) return hit.name.trim()
  } catch {
    // ignore
  }
  return ''
}

function fmtHistoryTime(iso?: string | null): string {
  if (!iso) return '—'
  try {
    const t = new Date(iso).getTime()
    const diff = Date.now() - t
    if (diff < 60_000) return '刚刚'
    if (diff < 3600_000) return `${Math.floor(diff / 60_000)} 分钟前`
    if (diff < 86400_000) return `${Math.floor(diff / 3600_000)} 小时前`
    if (diff < 7 * 86400_000) return `${Math.floor(diff / 86400_000)} 天前`
    return new Date(iso).toLocaleDateString('zh-CN')
  } catch {
    return iso
  }
}

interface Props {
  /** Switch page selection to the report's symbol (and name if known). Must run syncSymbol before hydrate. */
  onSelectSymbol?: (symbol: string, name: string) => void
  /** Switch to the AI 分析 tab after a report is loaded. */
  onSwitchToAiTab?: () => void
  onStopAnalysis?: () => void
  /** Detach the live listener without stopping the backend job when loading history. */
  onDetachAnalysis?: () => void
  className?: string
}

/**
 * Hover/click dropdown for loading prior multi-agent analysis reports (all symbols).
 */
export function AnalysisHistoryDropdown({
  onSelectSymbol,
  onSwitchToAiTab,
  onDetachAnalysis,
  className,
}: Props) {
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyItems, setHistoryItems] = useState<AistockReportSummary[]>([])
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [loadingReportId, setLoadingReportId] = useState<string | null>(null)
  const historyWrapRef = useRef<HTMLDivElement>(null)
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const { isAnalyzing, reset, setReport, setStructuredData } = useAnalysisStore()

  const clearCloseTimer = useCallback(() => {
    if (closeTimerRef.current != null) {
      clearTimeout(closeTimerRef.current)
      closeTimerRef.current = null
    }
  }, [])

  const scheduleClose = useCallback(() => {
    clearCloseTimer()
    // Delay so pointer can move from the button into the panel without dismissing.
    closeTimerRef.current = setTimeout(() => setHistoryOpen(false), 280)
  }, [clearCloseTimer])

  const fetchHistory = useCallback(async () => {
    setHistoryLoading(true)
    setHistoryError(null)
    try {
      // Omit symbol → list reports for all stocks
      const res = await getTradingReports(undefined, 0, 30)
      setHistoryItems(res.reports)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '加载分析历史失败'
      setHistoryError(msg)
      setHistoryItems([])
    } finally {
      setHistoryLoading(false)
    }
  }, [])

  const openHistory = useCallback(() => {
    clearCloseTimer()
    setHistoryOpen(true)
    void fetchHistory()
  }, [clearCloseTimer, fetchHistory])

  useEffect(() => () => clearCloseTimer(), [clearCloseTimer])

  useEffect(() => {
    if (!historyOpen) return
    const onPointerDown = (e: PointerEvent) => {
      if (!historyWrapRef.current?.contains(e.target as Node)) {
        clearCloseTimer()
        setHistoryOpen(false)
      }
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        clearCloseTimer()
        setHistoryOpen(false)
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [historyOpen, clearCloseTimer])

  const handleLoadHistory = async (item: AistockReportSummary) => {
    if (isAnalyzing) {
      // 后台研判继续运行，只断开本地订阅并切换到历史报告视图。
      onDetachAnalysis?.()
    }
    setLoadingReportId(item.id)
    try {
      // Fetch first so we only switch symbol after we have a valid report.
      const detail = await getTradingReport(item.id)
      const next = detailToAnalysisReport(detail)
      const sym = (next.symbol || item.symbol || '').trim()
      const nm = await resolveStockName(sym, extractStockName(detail, next))

      // Critical order: set symbol (syncSymbol may reset) → reset leftovers → hydrate.
      // onSelectSymbol updates prevSymbolRef so the page's useEffect won't re-reset after hydrate.
      if (sym) onSelectSymbol?.(sym, nm)
      reset()

      setReport(next)
      setStructuredData({
        riskItems: asRiskItems(detail.risk_items),
        keyMetrics: asKeyMetrics(detail.key_metrics),
        confidence: detail.confidence ?? null,
        targetPrice: normalizeTradePrice(detail.target_price) ?? null,
        stopLoss: normalizeTradePrice(detail.stop_loss_price) ?? null,
      })
      // Live SSE fills agentReports per node; history only has section blobs / debate state.
      // Reconstruct so 多头/空头/研究 etc. pass hasAgentContent and open the same sheet.
      const hydratedAgentReports = agentReportsFromAnalysisReport(
        next as unknown as Record<string, unknown>,
      )
      useAnalysisStore.setState((state) => ({
        agents: state.agents.map((a) => ({
          ...a,
          status: 'completed' as const,
          finishedAt: a.finishedAt ?? Date.now(),
        })),
        agentReports: hydratedAgentReports,
        isAnalyzing: false,
        isConnected: false,
      }))
      setHistoryOpen(false)
      onSwitchToAiTab?.()
      toast('已加载历史分析报告', 'success')
    } catch (err) {
      const msg = err instanceof Error ? err.message : '加载历史报告失败'
      toast(msg, 'error')
    } finally {
      setLoadingReportId(null)
    }
  }

  return (
    <div
      ref={historyWrapRef}
      className={cn('relative', className)}
      onMouseEnter={() => {
        clearCloseTimer()
        openHistory()
      }}
      onMouseLeave={scheduleClose}
    >
      <button
        type="button"
        aria-expanded={historyOpen}
        aria-haspopup="listbox"
        onClick={() => (historyOpen ? (clearCloseTimer(), setHistoryOpen(false)) : openHistory())}
        className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border/50 bg-elevated/40 px-3 text-xs font-medium text-secondary hover:border-border hover:bg-elevated/70 hover:text-foreground transition-colors"
      >
        <History className="h-3.5 w-3.5" />
        分析历史
      </button>

      {historyOpen && (
        /* pt-1.5 bridge keeps pointer inside wrap while moving from button → panel (no mt gap). */
        <div className="absolute right-0 top-full z-50 w-80 pt-1.5">
          <div
            role="listbox"
            className="max-h-72 overflow-y-auto rounded-lg border border-border/60 bg-surface shadow-xl"
            onMouseEnter={clearCloseTimer}
          >
            {historyLoading ? (
              <div className="flex items-center justify-center gap-2 px-3 py-6 text-[11px] text-muted">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                加载中…
              </div>
            ) : historyError ? (
              <div className="px-3 py-4 text-[11px] text-danger">{historyError}</div>
            ) : historyItems.length === 0 ? (
              <div className="px-3 py-4 text-center text-[11px] text-muted">暂无分析历史</div>
            ) : (
              <ul className="p-1.5 space-y-0.5">
                {historyItems.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      role="option"
                      disabled={loadingReportId === item.id}
                      onClick={() => void handleLoadHistory(item)}
                      className="w-full rounded-md px-2.5 py-2 text-left hover:bg-elevated/60 transition-colors disabled:opacity-50"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[11px] font-medium text-foreground truncate">
                          <span className="font-mono text-sky-300/90">{item.symbol}</span>
                          {item.trade_date ? (
                            <span className="text-muted font-normal"> · {item.trade_date}</span>
                          ) : null}
                        </span>
                        <span className="text-[10px] text-muted shrink-0">
                          {fmtHistoryTime(item.created_at)}
                        </span>
                      </div>
                      <div className="mt-0.5 flex items-center gap-2 text-[10px] text-muted">
                        {item.decision && (
                          <span className="text-sky-300/80">{item.decision}</span>
                        )}
                        {item.direction && <span>{item.direction}</span>}
                        {item.confidence != null && <span>置信 {item.confidence}%</span>}
                        {loadingReportId === item.id && (
                          <Loader2 className="h-3 w-3 animate-spin text-sky-400" />
                        )}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
