import { TrendingUp, TrendingDown, Target, Shield, Info } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { AnalysisReport } from '../types'
import {
  normalizeTradePrice,
  resolveTradingDecision,
  resolveTradePrices,
  sanitizeReportMarkdown,
  shouldShowTradePrices,
  type TradingUiDecision,
} from '../reportText'

interface DecisionCardProps {
  symbol: string
  name?: string
  decision?: TradingUiDecision
  direction?: string
  confidence?: number
  targetPrice?: number
  targetChange?: number
  stopLoss?: number
  stopLossChange?: number
  reasoning?: string
  riskLevel?: 'low' | 'medium' | 'high'
  report?: AnalysisReport
}

const decisionConfig: Record<string, { label: string; color: string; icon: typeof TrendingUp }> = {
  buy: { label: '买入', color: 'bg-bull/15 text-bull border-bull/30', icon: TrendingUp },
  sell: { label: '卖出', color: 'bg-bear/15 text-bear border-bear/30', icon: TrendingDown },
  hold: { label: '持有', color: 'bg-amber-400/15 text-amber-300 border-amber-400/30', icon: Shield },
  add: { label: '增持', color: 'bg-bull/15 text-bull border-bull/30', icon: TrendingUp },
  reduce: { label: '减持', color: 'bg-orange-400/15 text-orange-300 border-orange-400/30', icon: TrendingDown },
  watch: { label: '观望', color: 'bg-amber-400/15 text-amber-300 border-amber-400/30', icon: Info },
}

function formatTradePrice(price?: number): string {
  if (price == null || !Number.isFinite(price) || price <= 0) return '暂无'
  return `¥${price}`
}

export default function DecisionCard({
  symbol,
  name = symbol,
  decision: propDecision,
  direction,
  confidence,
  targetPrice,
  targetChange,
  stopLoss,
  stopLossChange,
  reasoning,
  riskLevel,
  report,
}: DecisionCardProps) {
  const decision =
    propDecision ??
    resolveTradingDecision({
      decision: report?.decision,
      direction: report?.direction,
      finalTradeDecision: report?.final_trade_decision,
    })
  const config = decision ? (decisionConfig[decision] || decisionConfig.hold) : null
  const DecisionIcon = config?.icon
  const showPriceTargets = shouldShowTradePrices(decision)
  const { target: displayTarget, stop: displayStop } = resolveTradePrices(decision, targetPrice, stopLoss)
  const hasTargetPrice = formatTradePrice(displayTarget) !== '暂无'
  const hasStopLoss = formatTradePrice(displayStop) !== '暂无'
  const invalidBuyPrices =
    (decision === 'buy' || decision === 'add') &&
    normalizeTradePrice(targetPrice) != null &&
    normalizeTradePrice(stopLoss) != null &&
    normalizeTradePrice(targetPrice)! <= normalizeTradePrice(stopLoss)!

  const riskLabels: Record<string, string> = { low: '低', medium: '中等', high: '高' }
  const riskColors: Record<string, string> = {
    low: 'text-bull',
    medium: 'text-amber-300',
    high: 'text-bear',
  }

  return (
    <div className="rounded-card border border-border/60 bg-surface/40 p-4 overflow-hidden">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-sky-500 to-blue-500 flex items-center justify-center shrink-0">
            <TrendingUp className="w-5 h-5 text-white" />
          </div>
          <div className="min-w-0">
            <h3 className="font-semibold text-foreground truncate">{name}</h3>
            <p className="text-sm text-muted font-mono">{symbol}</p>
            {direction && <p className="text-xs text-muted mt-0.5">方向：{direction}</p>}
          </div>
        </div>
        {config && DecisionIcon ? (
          <div className={`px-3 py-1.5 rounded-full border text-sm font-medium flex items-center gap-1.5 shrink-0 ${config.color}`}>
            <DecisionIcon className="w-4 h-4" />
            {config.label}
          </div>
        ) : (
          <div className="px-3 py-1.5 rounded-full border border-border text-sm font-medium text-muted shrink-0">
            等待裁决
          </div>
        )}
      </div>

      {confidence != null && (
        <div className="mb-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm text-muted">置信度</span>
            <span className="text-sm font-medium text-secondary">{confidence}%</span>
          </div>
          <div className="h-2 bg-elevated rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-sky-500 to-blue-400 rounded-full transition-all duration-1000"
              style={{ width: `${confidence}%` }}
            />
          </div>
        </div>
      )}

      {showPriceTargets && (
        <div className="grid grid-cols-2 gap-3 mb-4">
          <div className="p-3 rounded-xl bg-bull/10 border border-bull/20">
            <div className="flex items-center gap-1.5 mb-1">
              <Target className="w-4 h-4 text-bull" />
              <span className="text-xs text-muted">目标价</span>
            </div>
            <p className={`text-xl font-bold ${hasTargetPrice ? 'text-bull' : 'text-muted'}`}>
              {formatTradePrice(displayTarget)}
            </p>
            {targetChange != null && hasTargetPrice && (
              <p className="text-sm text-bull">
                {targetChange >= 0 ? '+' : ''}
                {targetChange.toFixed(1)}%
              </p>
            )}
          </div>
          <div className="p-3 rounded-xl bg-bear/10 border border-bear/20">
            <div className="flex items-center gap-1.5 mb-1">
              <Shield className="w-4 h-4 text-bear" />
              <span className="text-xs text-muted">止损价</span>
            </div>
            <p className={`text-xl font-bold ${hasStopLoss ? 'text-bear' : 'text-muted'}`}>
              {formatTradePrice(displayStop)}
            </p>
            {stopLossChange != null && hasStopLoss && (
              <p className="text-sm text-bear">
                {stopLossChange >= 0 ? '+' : ''}
                {stopLossChange.toFixed(1)}%
              </p>
            )}
          </div>
        </div>
      )}
      {invalidBuyPrices && (
        <p className="mb-4 text-xs text-amber-300">目标价不应低于或等于止损价，已忽略无效价位。</p>
      )}

      {reasoning && (
        <div className="mb-4 rounded-lg bg-elevated/40 p-3 border border-border/40">
          <span className="text-sm text-muted">核心逻辑</span>
          <div className="prose prose-sm prose-invert mt-1 max-w-none max-h-72 overflow-y-auto pr-1 text-secondary">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {sanitizeReportMarkdown(reasoning)}
            </ReactMarkdown>
          </div>
        </div>
      )}

      {riskLevel && (
        <div className="flex justify-between border-t border-border/40 pt-3">
          <span className="text-sm text-muted">风险等级</span>
          <span className={`text-sm font-medium ${riskColors[riskLevel]}`}>{riskLabels[riskLevel]}</span>
        </div>
      )}
    </div>
  )
}
