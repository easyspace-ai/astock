import { useEffect, useMemo, useState } from 'react'
import { Download, Loader2, RefreshCw, Sparkles, Square } from 'lucide-react'
import AgentCollaboration from './components/AgentCollaboration'
import { resolveAgentReportText } from './resolveAgentReport'
import DecisionCard from './components/DecisionCard'
import RiskRadar from './components/RiskRadar'
import KeyMetrics from './components/KeyMetrics'
import { ReportAgentSheet } from './components/ReportAgentSheet'
import {
  deriveKeyMetrics,
  deriveRiskItems,
  normalizeTradePrice,
  resolveTradePrices,
  resolveTradingDecision,
} from './reportText'
import {
  collectAgentReportFiles,
  downloadAgentReportsZip,
} from './downloadReports'
import { AGENT_META } from './agentMeta'
import { useAnalysisStore } from './analysisStore'
import { normalizeCnSymbol } from '@/lib/symbols'
import { toast } from '@/components/Toast'
import { cn } from '@/lib/cn'

const CONFIDENCE_WORD: Record<string, number> = { 高: 80, 中: 60, 低: 40 }

function extractConfidence(text?: string): number | undefined {
  if (!text) return undefined
  const block = text.slice(text.lastIndexOf('【最终决策】') >= 0 ? text.lastIndexOf('【最终决策】') : 0)
  const num = block.match(/置信度[:：]\s*(\d+)%/i) ?? block.match(/confidence[:：]\s*(\d+)%/i)
  if (num) {
    const v = parseInt(num[1], 10)
    return v >= 0 && v <= 100 ? v : undefined
  }
  const word = block.match(/置信度[:：]\s*(高|中|低)/)
  if (word?.[1]) return CONFIDENCE_WORD[word[1]]
  return undefined
}

function extractPrice(text: string | undefined, type: 'target' | 'stop'): number | undefined {
  if (!text) return undefined
  const patterns =
    type === 'target'
      ? [/目标价[:：]\s*[¥$]?\s*([\d.]+)/, /目标价格[:：]\s*[¥$]?\s*([\d.]+)/, /target[:：]\s*[¥$]?\s*([\d.]+)/i]
      : [/止损价[:：]\s*[¥$]?\s*([\d.]+)/, /止损价格[:：]\s*[¥$]?\s*([\d.]+)/, /stop[-\s_]?loss[:：]\s*[¥$]?\s*([\d.]+)/i]
  for (const p of patterns) {
    const m = text.match(p)
    if (m) return normalizeTradePrice(parseFloat(m[1]))
  }
  return undefined
}

interface Props {
  symbol: string
  stockName?: string
  /** Optional error from the runner */
  error?: string | null
  onStartAnalysis: (opts?: { force?: boolean }) => Promise<void>
  onStopAnalysis?: () => void
}

/**
 * Multi-agent AI analysis view (dark / tickflow-styled).
 * Driven by analysisStore; start via useMultiAgentAnalysis.
 */
export function AiAnalysisPanel({
  symbol,
  stockName,
  error,
  onStartAnalysis,
  onStopAnalysis,
}: Props) {
  const [selectedAgent, setSelectedAgent] = useState<string | undefined>()

  const {
    report,
    streamingSections,
    agentReports,
    jobConfidence,
    isAnalyzing,
    isConnected,
    riskItems,
    keyMetrics,
    jobTargetPrice,
    jobStopLoss,
    setCurrentSymbol,
    reset,
  } = useAnalysisStore()

  useEffect(() => {
    setSelectedAgent(undefined)
    setCurrentSymbol(symbol)

    // Defense in depth: if store still holds another symbol's result, wipe it
    const state = useAnalysisStore.getState()
    const reportSym = state.report?.symbol ? normalizeCnSymbol(state.report.symbol) : ''
    const sym = normalizeCnSymbol(symbol)
    if (!sym || state.isAnalyzing) return
    if (reportSym && reportSym !== sym) {
      reset()
      setCurrentSymbol(symbol)
    }
  }, [symbol, setCurrentSymbol, reset])

  const handleReanalyze = async () => {
    if (!symbol.trim()) return
    if (isAnalyzing) {
      const ok = window.confirm('当前分析进行中，确定要重新开始吗？')
      if (!ok) return
    }
    await onStartAnalysis({ force: isAnalyzing })
  }

  const reportSymbol = report?.symbol ? normalizeCnSymbol(report.symbol) : null
  const activeSym = normalizeCnSymbol(symbol)
  // During a live run report.symbol may be unset until job.completed — allow that.
  // After completion / history load, never show another stock's payload.
  const reportMatchesSymbol = !activeSym || !reportSymbol || reportSymbol === activeSym

  const matchedReport = reportMatchesSymbol ? report : null
  const finalDecision = matchedReport?.final_trade_decision
  const effectiveDecision = reportMatchesSymbol
    ? resolveTradingDecision({
        decision: matchedReport?.decision,
        direction: matchedReport?.direction,
        finalTradeDecision: matchedReport?.final_trade_decision,
      })
    : undefined
  const confidence = reportMatchesSymbol
    ? (jobConfidence ?? extractConfidence(finalDecision))
    : undefined
  const rawTargetPrice = reportMatchesSymbol
    ? normalizeTradePrice(jobTargetPrice) ??
      normalizeTradePrice(matchedReport?.target_price) ??
      extractPrice(finalDecision, 'target') ??
      extractPrice(matchedReport?.trader_investment_plan, 'target')
    : undefined
  const rawStopLoss = reportMatchesSymbol
    ? normalizeTradePrice(jobStopLoss) ??
      normalizeTradePrice(matchedReport?.stop_loss_price) ??
      extractPrice(finalDecision, 'stop') ??
      extractPrice(matchedReport?.trader_investment_plan, 'stop')
    : undefined
  const { target: targetPrice, stop: stopLoss } = resolveTradePrices(
    effectiveDecision,
    rawTargetPrice,
    rawStopLoss,
  )

  const effectiveRiskItems = useMemo(
    () =>
      reportMatchesSymbol
        ? riskItems.length > 0
          ? riskItems
          : deriveRiskItems(matchedReport)
        : [],
    [reportMatchesSymbol, riskItems, matchedReport],
  )
  const effectiveKeyMetrics = useMemo(
    () =>
      reportMatchesSymbol
        ? keyMetrics.length > 0
          ? keyMetrics
          : deriveKeyMetrics(matchedReport)
        : [],
    [reportMatchesSymbol, keyMetrics, matchedReport],
  )

  const reportFiles = useMemo(
    () => {
      if (!reportMatchesSymbol) return []
      return collectAgentReportFiles((agentName) => {
        const meta = AGENT_META.find((m) => m.name === agentName)
        return resolveAgentReportText(agentName, {
          agentReports,
          streamingSections,
          report: matchedReport as Record<string, unknown> | null,
          section: meta?.section,
        })
      })
    },
    [reportMatchesSymbol, agentReports, streamingSections, matchedReport],
  )

  const hasAnyContent =
    reportMatchesSymbol &&
    (reportFiles.length > 0 ||
      Boolean(
        matchedReport &&
          Object.values(matchedReport).some((v) => typeof v === 'string' && v.trim().length > 0),
      ))

  const handleDownloadAll = () => {
    if (!reportFiles.length) {
      toast('暂无报告可下载', 'error')
      return
    }
    // ZIP: per-agent .md files + combined markdown
    downloadAgentReportsZip(reportFiles, { symbol, stockName })
    toast('已下载全部报告 ZIP', 'success')
  }

  const btnClass =
    'inline-flex items-center gap-1.5 rounded-lg border border-border/50 bg-elevated/40 px-3 py-1.5 text-xs font-medium text-secondary hover:border-border hover:bg-elevated/70 hover:text-foreground transition-colors disabled:opacity-40'

  return (
    <div className="space-y-4 min-w-0">
      {error && (
        <div className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
          {error}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-[11px] text-muted">
          {isAnalyzing && <Loader2 className="h-3.5 w-3.5 animate-spin text-sky-400" />}
          <span>
            {isAnalyzing
              ? isConnected
                ? '多 Agent 分析进行中…'
                : '连接中断，正在后台重连…'
              : hasAnyContent
                ? '分析已完成，可导出报告'
                : '尚未分析，可启动多 Agent 协同研判'}
          </span>
          {isAnalyzing && onStopAnalysis && (
            <button
              type="button"
              onClick={onStopAnalysis}
              className="inline-flex items-center gap-1 rounded border border-rose-400/30 bg-rose-500/10 px-1.5 py-0.5 text-[10px] text-rose-300 hover:bg-rose-500/20"
            >
              <Square className="h-2.5 w-2.5" />
              停止
            </button>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => void handleReanalyze()}
            disabled={!symbol.trim()}
            className={btnClass}
          >
            <RefreshCw className={cn('h-3.5 w-3.5', isAnalyzing && 'animate-spin')} />
            开始分析
          </button>

          {reportFiles.length > 0 && (
            <button
              type="button"
              onClick={handleDownloadAll}
              className="inline-flex items-center gap-1.5 rounded-lg border border-sky-500/40 bg-sky-500/10 px-3 py-1.5 text-xs font-medium text-sky-300 hover:bg-sky-500/20"
            >
              <Download className="h-3.5 w-3.5" />
              一键下载
            </button>
          )}
        </div>
      </div>

      {!hasAnyContent && !isAnalyzing && !error && (
        <div className="rounded-card border border-border/60 bg-surface/40 px-4 py-8 text-center">
          <p className="text-sm text-secondary">启动多 Agent 协同研判，或从分析历史加载报告</p>
          <p className="mt-1 text-[11px] text-muted">技术 / 舆情 / 新闻 / 基本面 / 宏观 / 主力资金 → 多空辩论 → 风控裁决</p>
          <button
            type="button"
            onClick={() => void handleReanalyze()}
            className="mt-4 inline-flex items-center gap-1.5 rounded-lg border border-sky-500/40 bg-sky-500/10 px-3.5 py-1.5 text-xs font-medium text-sky-300 hover:bg-sky-500/20"
          >
            <Sparkles className="h-3.5 w-3.5" />
            开始分析
          </button>
        </div>
      )}

      {(isAnalyzing || hasAnyContent) && (
        <>
          <AgentCollaboration
            onSelectAgent={setSelectedAgent}
            selectedAgent={selectedAgent}
          />

          <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-3">
            <DecisionCard
              symbol={symbol}
              name={stockName}
              report={matchedReport || undefined}
              decision={effectiveDecision}
              direction={matchedReport?.direction}
              confidence={confidence ?? undefined}
              targetPrice={targetPrice}
              stopLoss={stopLoss}
              reasoning={finalDecision}
            />
            <RiskRadar items={effectiveRiskItems} />
            <KeyMetrics items={effectiveKeyMetrics} />
          </div>

          <ReportAgentSheet
            agentName={selectedAgent}
            symbol={symbol}
            stockName={stockName}
            onClose={() => setSelectedAgent(undefined)}
          />
        </>
      )}
    </div>
  )
}
