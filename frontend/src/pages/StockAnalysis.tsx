import { useState, useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Sparkles, LineChart, Loader2, AlertTriangle,
} from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState } from '@/components/EmptyState'
import { StockFinancialSearch } from '@/components/financials/StockFinancialSearch'
import { LastStockChip } from '@/components/LastStockChip'
import { AnalysisKChart, type PriceLevel, type LevelType } from '@/components/stock-analysis/AnalysisKChart'
import { AiAnalysisPanel } from '@/features/tradingAgents/AiAnalysisPanel'
import { AnalysisHistoryDropdown } from '@/features/tradingAgents/AnalysisHistoryDropdown'
import { AnalysisTaskQueue } from '@/features/tradingAgents/AnalysisTaskQueue'
import { useMultiAgentAnalysis } from '@/features/tradingAgents/useMultiAgentAnalysis'
import { api } from '@/lib/api'
import { useLastStock } from '@/lib/useLastStock'
import { QK } from '@/lib/queryKeys'
import { toast } from '@/components/Toast'
import { cn } from '@/lib/cn'

type ViewTab = 'basic' | 'ai'

/**
 * 个股分析页 —— 基本数据(日 K + 关键价位) / AI 多 Agent 协同分析。
 * 主路径走 Go SSE；分析历史通过页头右上角「分析历史」下拉查看。
 */
export function StockAnalysis() {
  const [symbol, setSymbol] = useState<string>('')
  const [name, setName] = useState<string>('')
  const [tab, setTab] = useState<ViewTab>('basic')
  const { last: lastStock, remember: rememberStock } = useLastStock('stock-analysis')
  const {
    startAnalysis: startMultiAgent,
    stopAnalysis,
    detachAnalysis,
    syncSymbol,
    recoverAnalysis,
    isAnalyzing,
    error: aiError,
  } = useMultiAgentAnalysis()
  const prevSymbolRef = useRef<string>('')

  // 挂载时与 sidecar 对账：恢复仍在后台运行的研判任务并重新订阅事件。
  useEffect(() => {
    void recoverAnalysis()
  }, [recoverAnalysis])

  // 自动恢复上次选中的股票(切走再回来不丢)。useLastStock 的 last 来自 localStorage, 同步可用。
  useEffect(() => {
    if (!symbol && lastStock) {
      setSymbol(lastStock.symbol)
      setName(lastStock.name)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 切换标的时中断 SSE、清空上一只股票的分析 UI（不自动开跑）
  useEffect(() => {
    if (!symbol) return
    const prev = prevSymbolRef.current
    syncSymbol(symbol, prev)
    prevSymbolRef.current = symbol
  }, [symbol, syncSymbol])

  const onSelect = (sym: string, nm: string) => {
    // Sync store before paint so AiAnalysisPanel never briefly shows the previous stock
    syncSymbol(sym, symbol || prevSymbolRef.current)
    prevSymbolRef.current = sym
    setSymbol(sym)
    setName(nm)
    rememberStock(sym, nm)
  }

  const handleStartAnalysis = async (opts?: { force?: boolean }) => {
    if (!symbol) return
    setTab('ai')
    try {
      await startMultiAgent(symbol, name, undefined, opts)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'AI 分析启动失败'
      toast(msg, 'error')
    }
  }

  return (
    <>
      <PageHeader
        title="个股分析"
        titleExtra={
          <span className="inline-flex items-center rounded-full border border-amber-400/30 bg-amber-400/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-amber-400">
            Beta
          </span>
        }
        subtitle="基本数据 · 关键价位 · 多 Agent AI 协同分析"
        right={
          <div className="flex items-center gap-2">
            <LastStockChip stock={lastStock} onSelect={onSelect} />
            <AnalysisTaskQueue
              onSelectSymbol={onSelect}
              onSwitchToAiTab={() => setTab('ai')}
            />
            <AnalysisHistoryDropdown
              onSelectSymbol={onSelect}
              onSwitchToAiTab={() => setTab('ai')}
              onStopAnalysis={stopAnalysis}
              onDetachAnalysis={detachAnalysis}
            />
          </div>
        }
      />

      <div className="w-full px-8 py-6 space-y-6">
        {/* 搜索栏 */}
        <div className="flex items-center gap-3 flex-wrap">
          <div className="w-72">
            <StockFinancialSearch onSelect={onSelect} />
          </div>
          {symbol && (
            <div className="flex items-center gap-2 text-sm">
              <span className="text-foreground font-medium">{name || symbol}</span>
              <span className="text-[10px] font-mono text-muted">{symbol}</span>
            </div>
          )}
        </div>

        {/* Tabs */}
        <div className="flex items-center gap-1 border-b border-border/40">
          {([
            { id: 'basic' as const, label: '基本数据' },
            { id: 'ai' as const, label: 'AI 分析' },
          ]).map(t => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={cn(
                'px-4 py-2 text-xs font-medium border-b-2 -mb-px transition-colors',
                tab === t.id
                  ? 'border-sky-400 text-sky-300'
                  : 'border-transparent text-muted hover:text-secondary',
              )}
            >
              {t.label}
              {t.id === 'ai' && isAnalyzing && (
                <Loader2 className="inline-block ml-1.5 h-3 w-3 animate-spin" />
              )}
            </button>
          ))}
        </div>

        {tab === 'basic' ? (
          <div className="min-w-0">
            {!symbol ? (
              <EmptyState
                icon={LineChart}
                title="选择一只股票开始分析"
                hint="搜索代码或名称,查看日 K 与关键价位；切换到「AI 分析」可运行多 Agent 协同研判。"
              />
            ) : (
              <StockAnalysisBoard symbol={symbol} />
            )}
          </div>
        ) : (
          <div className="min-w-0">
            {!symbol ? (
              <EmptyState
                icon={Sparkles}
                title="选择一只股票开始 AI 分析"
                hint="搜索代码后切换到「AI 分析」，即可启动多 Agent SSE 流水线。"
              />
            ) : (
              <AiAnalysisPanel
                symbol={symbol}
                stockName={name}
                error={aiError}
                onStartAnalysis={handleStartAnalysis}
                onStopAnalysis={stopAnalysis}
              />
            )}
          </div>
        )}
      </div>
    </>
  )
}

// ===== 分析看板:日 K + 关键价位 =====
function StockAnalysisBoard({ symbol }: { symbol: string }) {
  const kline = useQuery({
    queryKey: ['kline', symbol, ''],
    queryFn: () => api.klineDaily(symbol, 250),
    enabled: !!symbol,
    staleTime: 60_000,
  })

  const levelsQ = useQuery({
    queryKey: QK.stockLevels(symbol),
    queryFn: () => api.stockAnalysisLevels(symbol, 250),
    enabled: !!symbol,
    staleTime: 60_000,
  })

  if (kline.isLoading) {
    return <div className="flex items-center justify-center py-20"><Loader2 className="h-5 w-5 animate-spin text-muted" /></div>
  }

  if (kline.isError) {
    return (
      <EmptyState
        icon={AlertTriangle}
        title="日 K 数据加载失败"
        hint="请检查网络或数据源配置后重试。"
      />
    )
  }

  const rows = kline.data?.rows ?? []
  if (rows.length === 0) {
    return <EmptyState icon={LineChart} title="暂无日 K 数据" hint="该标的尚未同步日 K,请先在数据页或自选页同步。" />
  }

  const levels = (levelsQ.data?.levels ?? {}) as Record<LevelType, PriceLevel[]>

  // 涨跌色:最后一根 K 线收 vs 前一根收(无前日则按开收判断)
  const last = rows[rows.length - 1]
  const prev = rows[rows.length - 2]
  const curClose = levelsQ.data?.close
  const isUp = prev ? (last.close >= prev.close) : (last.close >= last.open)

  return (
    <div className="rounded-card border border-border/60 bg-surface/40 overflow-hidden">
      <div className="px-4 py-3 border-b border-border/40">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <LineChart className="h-4 w-4 text-sky-400 shrink-0" />
            <span className="text-sm font-medium text-foreground">关键价位分析</span>
          </div>
          <div className="flex items-baseline gap-2 shrink-0">
            <span className="text-[10px] text-muted">{rows.length} 个交易日</span>
            <span className="text-[10px] text-muted/60">·</span>
            <span className="text-[10px] text-muted">当前价</span>
            <span className={`text-base font-mono font-bold ${isUp ? 'text-bull' : 'text-bear'}`}>
              {curClose?.toFixed(2) ?? '—'}
            </span>
          </div>
        </div>
      </div>
      <div className="p-3">
        <AnalysisKChart
          rows={rows}
          levels={levels}
          series={levelsQ.data?.series}
          seriesDates={levelsQ.data?.dates}
          defaultLevelTypes={['sr', 'pivot', 'keltner_s']}
          height={480}
        />
      </div>
    </div>
  )
}
