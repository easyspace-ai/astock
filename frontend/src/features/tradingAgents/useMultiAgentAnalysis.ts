import { useCallback, useEffect, useState } from 'react'
import {
  describeAistockClientError,
  startAnalysisAsync,
  subscribeJobEvents,
  stopAnalysisJob,
  listAnalysisJobs,
  getTradingReport,
  type JobSummary,
} from '@/lib/aistockApi'
import { normalizeCnSymbol } from '@/lib/symbols'
import { normalizeTradePrice } from './reportText'
import { useAnalysisStore } from './analysisStore'
import type {
  AgentReportEvent,
  AgentSnapshotEvent,
  AgentStatusEvent,
  AgentTokenEvent,
  AnalysisReport,
  ReportChunkEvent,
} from './types'

/** Default analyst set matching newstock ChatCopilotPanel. */
export const DEFAULT_ANALYSTS = [
  'market',
  'social',
  'news',
  'fundamentals',
  'macro',
  'smart_money',
]

type JobStatus = 'pending' | 'running' | 'completed' | 'failed'

function asJobStatus(status: string | undefined): JobStatus {
  if (status === 'running' || status === 'completed' || status === 'failed') return status
  return 'pending'
}

/**
 * Shared subscription handle. Only one active SSE stream exists at a time
 * (background tasks are not subscribed), so this lives at module level and is
 * shared across every hook instance — the queue component and the page must be
 * able to detach/switch the same stream.
 */
const sharedConn = {
  abort: null as AbortController | null,
  activeJobId: null as string | null,
  epoch: 0,
  reconnectTimer: null as ReturnType<typeof setTimeout> | null,
  settled: false,
  retry: 0,
  reconcile: (_jobId: string, _epoch: number) => {},
}

export function useMultiAgentAnalysis() {
  const [error, setError] = useState<string | null>(null)

  const MAX_RETRIES = 6
  const BACKOFF_MS = [1000, 2000, 4000, 8000, 15000]

  const clearReconnectTimer = useCallback(() => {
    if (sharedConn.reconnectTimer) {
      clearTimeout(sharedConn.reconnectTimer)
      sharedConn.reconnectTimer = null
    }
  }, [])

  /** Detach the local SSE listener only; never stops the backend job. */
  const detachLocal = useCallback(() => {
    sharedConn.epoch += 1
    clearReconnectTimer()
    sharedConn.settled = false
    sharedConn.retry = 0
    sharedConn.abort?.abort()
    sharedConn.abort = null
  }, [clearReconnectTimer])

  const {
    isAnalyzing,
    reset,
    setIsAnalyzing,
    setIsConnected,
    setCurrentJobId,
    setCurrentSymbol,
    setCurrentHorizon,
    setReport,
    setStructuredData,
    updateAgentStatus,
    updateAgentSnapshot,
    addAgentReport,
    addReportChunk,
    addAgentToken,
    setBackgroundJob,
    removeBackgroundJob,
    snapshotActiveView,
    restoreJobView,
    upsertJobViewMeta,
    removeJobView,
  } = useAnalysisStore()

  const dispatch = useCallback(
    (event: string, data: Record<string, unknown>) => {
      const state = useAnalysisStore.getState()
      const jobId = state.currentJobId
      const newId = data.job_id ? String(data.job_id) : jobId
      const symbol = String(data.symbol ?? state.currentSymbol ?? '')
      const touchBackground = (status: JobStatus, id?: string | null) => {
        const tid = id ?? jobId
        if (tid) {
          setBackgroundJob({ jobId: tid, symbol, status, updatedAt: Date.now() })
          upsertJobViewMeta(tid, { status, symbol, updatedAt: Date.now() })
        }
      }

      switch (event) {
        case 'job.created': {
          const id = data.job_id ? String(data.job_id) : null
          if (id) {
            setCurrentJobId(id)
            upsertJobViewMeta(id, {
              symbol,
              status: 'pending',
              createdAt: Date.now(),
              updatedAt: Date.now(),
            })
          }
          setIsConnected(true)
          break
        }
        case 'job.running':
          setIsAnalyzing(true)
          touchBackground('running', newId)
          break
        case 'agent.horizon_start':
          setCurrentHorizon(String(data.horizon || '') || null)
          break
        case 'agent.horizon_done':
          break
        case 'job.completed': {
          setCurrentHorizon(null)
          setIsAnalyzing(false)
          touchBackground('completed', newId)
          if (typeof data.result === 'object' && data.result && 'symbol' in (data.result as object)) {
            const symbol = String((data.result as Record<string, unknown>).symbol || '')
            if (symbol) setCurrentSymbol(symbol)
          }
          if (typeof data.result === 'object' && data.result) {
            const body = { ...(data.result as Record<string, unknown>) }
            if (data.direction) body.direction = data.direction
            if (data.decision) body.decision = data.decision
            const targetPrice = normalizeTradePrice(data.target_price as number | null | undefined)
            const stopLoss = normalizeTradePrice(data.stop_loss_price as number | null | undefined)
            if (targetPrice != null) body.target_price = targetPrice
            if (stopLoss != null) body.stop_loss_price = stopLoss
            setReport(body as unknown as AnalysisReport)
          }
          setStructuredData({
            riskItems: data.risk_items as never,
            keyMetrics: data.key_metrics as never,
            confidence: data.confidence as number | null,
            targetPrice: normalizeTradePrice(data.target_price as number | null | undefined) ?? null,
            stopLoss: normalizeTradePrice(data.stop_loss_price as number | null | undefined) ?? null,
          })
          snapshotActiveView(newId ?? undefined)
          break
        }
        case 'job.failed':
          setCurrentHorizon(null)
          setIsAnalyzing(false)
          touchBackground('failed', newId)
          snapshotActiveView(newId ?? undefined)
          setError(String(data.error || '分析失败'))
          break
        case 'agent.status':
          updateAgentStatus(data as unknown as AgentStatusEvent)
          break
        case 'agent.token':
          addAgentToken(data as unknown as AgentTokenEvent)
          break
        case 'agent.snapshot':
          updateAgentSnapshot(data as unknown as AgentSnapshotEvent)
          break
        case 'agent.report':
          addAgentReport(data as unknown as AgentReportEvent)
          break
        case 'agent.report.chunk':
          addReportChunk(data as unknown as ReportChunkEvent)
          break
        default:
          break
      }
    },
    [
      addAgentReport,
      addAgentToken,
      addReportChunk,
      setCurrentHorizon,
      setCurrentJobId,
      setCurrentSymbol,
      setIsAnalyzing,
      setIsConnected,
      setReport,
      setStructuredData,
      updateAgentSnapshot,
      updateAgentStatus,
      setBackgroundJob,
      upsertJobViewMeta,
      snapshotActiveView,
    ],
  )

  /** Persisted SQLite report fallback when the sidecar lost the in-memory job. */
  const backfillFromReport = useCallback(
    async (jobId: string): Promise<boolean> => {
      try {
        const detail = await getTradingReport(jobId)
        if (!detail) return false
        const fromResult = (detail.result_data ?? {}) as Record<string, unknown>
        const body: Record<string, unknown> = {
          ...fromResult,
          symbol: detail.symbol || (fromResult.symbol as string) || '',
          decision: detail.decision ?? (fromResult.decision as string | undefined),
          direction: detail.direction ?? (fromResult.direction as string | undefined),
          target_price: detail.target_price ?? fromResult.target_price,
          stop_loss_price: detail.stop_loss_price ?? fromResult.stop_loss_price,
        }
        setReport(body as unknown as AnalysisReport)
        setStructuredData({
          riskItems: (detail.risk_items ?? fromResult.risk_items) as never,
          keyMetrics: (detail.key_metrics ?? fromResult.key_metrics) as never,
          confidence: (detail.confidence ?? fromResult.confidence) as number | null,
          targetPrice:
            normalizeTradePrice(
              (detail.target_price ?? fromResult.target_price) as number | null | undefined,
            ) ?? null,
          stopLoss:
            normalizeTradePrice(
              (detail.stop_loss_price ?? fromResult.stop_loss_price) as number | null | undefined,
            ) ?? null,
        })
        return true
      } catch {
        return false
      }
    },
    [setReport, setStructuredData],
  )

  /** Terminal cleanup after a completed/failed job is settled. */
  const finishTerminal = useCallback(() => {
    sharedConn.settled = true
    sharedConn.retry = 0
    clearReconnectTimer()
    setIsConnected(false)
    setIsAnalyzing(false)
  }, [clearReconnectTimer, setIsAnalyzing, setIsConnected])

  /**
   * Open the SSE subscription for a job. A terminal event marks the session
   * settled; an unexpected EOF / network drop (no terminal event) hands off to
   * `onDropped` instead of flipping the UI to "completed".
   */
  const openSubscription = useCallback(
    (jobId: string, epoch: number, onDropped: () => void) => {
      const controller = new AbortController()
      sharedConn.abort = controller
      sharedConn.settled = false
      void subscribeJobEvents({
        jobId,
        signal: controller.signal,
        onEvent: (ev) => {
          if (controller.signal.aborted || sharedConn.epoch !== epoch) return
          if (ev.event === 'ping') return
          if (
            ev.event === 'job.completed' ||
            ev.event === 'job.failed' ||
            ev.event === 'done' ||
            ev.data === '[DONE]'
          ) {
            sharedConn.settled = true
          }
          if (ev.event === 'done' || ev.data === '[DONE]') {
            setIsConnected(false)
            return
          }
          let data: Record<string, unknown> = {}
          if (ev.data != null && typeof ev.data === 'object' && !Array.isArray(ev.data)) {
            data = ev.data as Record<string, unknown>
          }
          dispatch(ev.event, data)
        },
      })
        .then(() => {
          if (controller.signal.aborted || sharedConn.epoch !== epoch) return
          if (sharedConn.settled) {
            setIsConnected(false)
            setIsAnalyzing(false)
          } else {
            onDropped()
          }
        })
        .catch(() => {
          if (controller.signal.aborted || sharedConn.epoch !== epoch) return
          onDropped()
        })
    },
    [dispatch, setIsAnalyzing, setIsConnected],
  )

  /** Schedule a backoff reconnect; give up after MAX_RETRIES and keep the background summary. */
  const scheduleReconnect = useCallback(
    (jobId: string, epoch: number) => {
      if (sharedConn.epoch !== epoch) return
      sharedConn.retry += 1
      setIsConnected(false)
      if (sharedConn.retry > MAX_RETRIES) {
        setError('连接中断，研判任务可能仍在后台运行；可稍后从分析历史查看结果')
        finishTerminal()
        return
      }
      const delay = BACKOFF_MS[Math.min(sharedConn.retry - 1, BACKOFF_MS.length - 1)]
      clearReconnectTimer()
      sharedConn.reconnectTimer = setTimeout(() => {
        sharedConn.reconnectTimer = null
        if (sharedConn.epoch !== epoch) return
        sharedConn.reconcile(jobId, epoch)
      }, delay)
    },
    [clearReconnectTimer, finishTerminal, setError, setIsConnected],
  )

  /** Reconcile with the sidecar after a drop: re-subscribe live, replay terminal, or restore persisted report. */
  const reconcileJob = useCallback(
    async (jobId: string, epoch: number) => {
      if (sharedConn.epoch !== epoch) return
      let status: string | null = null
      try {
        const jobs = (await listAnalysisJobs()).jobs ?? []
        status = jobs.find((x) => x.job_id === jobId)?.status ?? null
      } catch {
        status = null // lookup network error: keep retrying
      }
      if (sharedConn.epoch !== epoch) return

      const onDropped = () => scheduleReconnect(jobId, epoch)
      if (status === 'running' || status === 'pending' || status === 'completed' || status === 'failed') {
        if (status === 'running' || status === 'pending') {
          sharedConn.retry = 0
          setIsAnalyzing(true)
          setIsConnected(false)
        }
        openSubscription(jobId, epoch, onDropped)
        return
      }

      // In-memory job gone (sidecar restart / evicted): restore from persisted report.
      const restored = await backfillFromReport(jobId)
      if (sharedConn.epoch !== epoch) return
      if (restored) {
        finishTerminal()
        return
      }
      scheduleReconnect(jobId, epoch)
    },
    [backfillFromReport, finishTerminal, openSubscription, scheduleReconnect, setIsAnalyzing, setIsConnected],
  )
  sharedConn.reconcile = reconcileJob

  const startAnalysis = useCallback(
    async (
      symbol: string,
      name?: string,
      selectedAnalysts = DEFAULT_ANALYSTS,
      opts?: { force?: boolean },
    ) => {
      const code = symbol.trim()
      if (!code) return

      const state = useAnalysisStore.getState()
      const prevJobId = state.currentJobId
      // 先把旧任务当前视图落桶，随后 reset 顶层时不会丢掉后台快照。
      if (prevJobId) snapshotActiveView(prevJobId)
      if (state.isAnalyzing) {
        // 并发多任务：断开旧任务的本地订阅（后端继续跑）。
        detachLocal()
        if (opts?.force && prevJobId) {
          // 重跑同一标的：停止旧后端任务。
          void stopAnalysisJob(prevJobId).catch(() => {})
          removeBackgroundJob(prevJobId)
          removeJobView(prevJobId)
        } else if (prevJobId) {
          // 旧任务转入后台摘要，继续在 sidecar 运行。
          upsertJobViewMeta(prevJobId, {
            symbol: state.currentSymbol,
            status: 'running',
            updatedAt: Date.now(),
          })
          setBackgroundJob({
            jobId: prevJobId,
            symbol: state.currentSymbol,
            status: 'running',
            updatedAt: Date.now(),
          })
        }
      }

      setError(null)
      reset()
      setCurrentSymbol(code)
      setIsAnalyzing(true)
      setIsConnected(false)

      const epoch = sharedConn.epoch

      const label = name?.trim() ? `${name.trim()}(${code})` : code
      const prompt = `请对 ${label} 进行多维度综合分析`

      try {
        const res = await startAnalysisAsync({
          messages: [{ role: 'user', content: prompt }],
          selectedAnalysts,
        })
        const jobId = res.job_id
        sharedConn.activeJobId = jobId
        setCurrentJobId(jobId)
        upsertJobViewMeta(jobId, {
          symbol: code,
          name: name?.trim() || undefined,
          status: 'running',
          createdAt: Date.now(),
          updatedAt: Date.now(),
        })
        setBackgroundJob({ jobId, symbol: code, status: 'running', updatedAt: Date.now() })

        openSubscription(jobId, epoch, () => scheduleReconnect(jobId, epoch))
      } catch (err) {
        if (sharedConn.epoch !== epoch) {
          // Superseded / detached by navigation — leave the backend job running.
          return
        }
        const msg = describeAistockClientError(err)
        setError(msg)
        setIsAnalyzing(false)
        setIsConnected(false)
        throw new Error(msg)
      }
    },
    [
      detachLocal,
      reset,
      setCurrentSymbol,
      setIsAnalyzing,
      setIsConnected,
      setCurrentJobId,
      setBackgroundJob,
      removeBackgroundJob,
      snapshotActiveView,
      upsertJobViewMeta,
      removeJobView,
      openSubscription,
      scheduleReconnect,
    ],
  )

  /** Stop an arbitrary queued job by id (active or background). */
  const stopJobById = useCallback(
    async (jobId: string) => {
      if (!jobId) return
      const state = useAnalysisStore.getState()
      const isActive = jobId === state.currentJobId
      if (isActive) detachLocal()
      try {
        await stopAnalysisJob(jobId)
      } catch {
        // Job may already be finished; ignore.
      }
      removeBackgroundJob(jobId)
      upsertJobViewMeta(jobId, { status: 'failed' })
      if (isActive) {
        sharedConn.activeJobId = null
        setIsAnalyzing(false)
        setIsConnected(false)
      }
    },
    [detachLocal, removeBackgroundJob, upsertJobViewMeta, setIsAnalyzing, setIsConnected],
  )

  /** Manually stop the active job: cancels it in the sidecar, then detaches the listener. */
  const stopAnalysis = useCallback(async () => {
    const jobId = useAnalysisStore.getState().currentJobId
    if (jobId) await stopJobById(jobId)
  }, [stopJobById])

  /**
   * Switch the visible view to another queued task. Freezes the current view,
   * restores the target snapshot, and re-subscribes if it is still running.
   */
  const switchToJob = useCallback(
    (jobId: string) => {
      const state = useAnalysisStore.getState()
      if (!jobId || jobId === state.currentJobId) return
      if (state.currentJobId) snapshotActiveView(state.currentJobId)
      detachLocal()
      restoreJobView(jobId)
      const snap = useAnalysisStore.getState().jobViews[jobId]
      const epoch = sharedConn.epoch
      sharedConn.activeJobId = jobId
      if (snap && (snap.status === 'running' || snap.status === 'pending')) {
        setIsAnalyzing(true)
        setIsConnected(false)
        openSubscription(jobId, epoch, () => scheduleReconnect(jobId, epoch))
      } else {
        setIsAnalyzing(false)
        setIsConnected(false)
      }
    },
    [
      detachLocal,
      snapshotActiveView,
      restoreJobView,
      openSubscription,
      scheduleReconnect,
      setIsAnalyzing,
      setIsConnected,
    ],
  )

  /**
   * Detach the local listener and clear the active view WITHOUT stopping the backend job.
   * Used when loading a historical report so its view isn't overwritten by live events;
   * the sidecar job keeps running and its summary stays in backgroundJobs.
   */
  const detachAnalysis = useCallback(() => {
    const state = useAnalysisStore.getState()
    if (state.currentJobId) snapshotActiveView(state.currentJobId)
    detachLocal()
    sharedConn.activeJobId = null
    setError(null)
    reset()
    setIsAnalyzing(false)
    setIsConnected(false)
  }, [detachLocal, reset, snapshotActiveView, setIsAnalyzing, setIsConnected])

  /**
   * Reconcile with the sidecar on mount: list background jobs, refresh summaries,
   * and re-subscribe to the latest still-running one so progress survives route changes.
   */
  const recoverAnalysis = useCallback(async () => {
    detachLocal()
    const epoch = sharedConn.epoch
    let jobs: JobSummary[] = []
    try {
      jobs = (await listAnalysisJobs()).jobs ?? []
    } catch {
      return
    }
    // A newer detach/switch happened while awaiting — discard this recovery.
    if (epoch !== sharedConn.epoch) return

    const state = useAnalysisStore.getState()
    for (const j of jobs) {
      const st = asJobStatus(j.status)
      const sym = j.symbol || state.currentSymbol
      setBackgroundJob({ jobId: j.job_id, symbol: sym, status: st, updatedAt: Date.now() })
      upsertJobViewMeta(j.job_id, { symbol: sym, status: st, updatedAt: Date.now() })
    }

    const persisted = state.currentJobId
    const active =
      jobs.find((j) => j.job_id === persisted && (j.status === 'running' || j.status === 'pending')) ??
      jobs.find((j) => j.status === 'running' || j.status === 'pending')

    if (!active) {
      setIsAnalyzing(false)
      setIsConnected(false)
      if (persisted) setCurrentJobId(null)
      return
    }

    setCurrentJobId(active.job_id)
    setCurrentSymbol(active.symbol || state.currentSymbol)
    setIsAnalyzing(true)
    setIsConnected(false)
    // Re-check the epoch right before opening the stream; a newer detach/switch wins.
    if (epoch !== sharedConn.epoch) return
    sharedConn.activeJobId = active.job_id
    openSubscription(active.job_id, epoch, () => scheduleReconnect(active.job_id, epoch))
  }, [detachLocal, setCurrentJobId, setCurrentSymbol, setIsAnalyzing, setIsConnected, setBackgroundJob, upsertJobViewMeta, openSubscription, scheduleReconnect])

  /**
   * Bind the global analysis store to a newly selected symbol.
   * Detaches the local listener and clears prior run UI so the panel never shows another stock's report.
   * The sidecar job keeps running in the background; its summary stays in backgroundJobs.
   */
  const syncSymbol = useCallback(
    (nextSymbol: string, prevSymbol?: string) => {
      const next = nextSymbol.trim()
      const nextNorm = normalizeCnSymbol(next)
      const prevNorm = normalizeCnSymbol(prevSymbol ?? '')
      const state = useAnalysisStore.getState()
      const reportNorm = state.report?.symbol ? normalizeCnSymbol(state.report.symbol) : ''

      // A background job already bound to the incoming symbol must survive the
      // mount/recovery reconciliation; don't treat its live state as stale residue.
      const activeBgEntry = Object.values(state.backgroundJobs || {}).find(
        (b) =>
          (b.status === 'running' || b.status === 'pending') &&
          normalizeCnSymbol(b.symbol || '') === nextNorm,
      )
      const activeMatchesNext = Boolean(activeBgEntry)

      const switched = Boolean(prevNorm && nextNorm && prevNorm !== nextNorm)
      const staleReport = Boolean(reportNorm && nextNorm && reportNorm !== nextNorm)
      const staleExtras =
        Boolean(nextNorm) &&
        (!reportNorm || reportNorm !== nextNorm) &&
        (Object.keys(state.agentReports).length > 0 ||
          Object.keys(state.streamingSections).length > 0 ||
          state.riskItems.length > 0 ||
          state.keyMetrics.length > 0 ||
          state.isAnalyzing)

      if ((switched || staleReport || staleExtras) && !activeMatchesNext) {
        if (state.currentJobId) snapshotActiveView(state.currentJobId)
        detachLocal()
        sharedConn.activeJobId = null
        setError(null)
        reset()
        setIsAnalyzing(false)
        setIsConnected(false)
      }

      if (next) setCurrentSymbol(next)
    },
    [detachLocal, reset, snapshotActiveView, setCurrentSymbol, setIsAnalyzing, setIsConnected],
  )

  // Unmount detaches the local listener only; the sidecar job keeps running.
  useEffect(() => () => detachLocal(), [detachLocal])

  return {
    startAnalysis,
    stopAnalysis,
    stopJobById,
    switchToJob,
    detachAnalysis,
    syncSymbol,
    recoverAnalysis,
    isAnalyzing,
    error,
  }
}
