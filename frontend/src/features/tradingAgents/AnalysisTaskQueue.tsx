import { useCallback, useEffect, useRef, useState } from 'react'
import { ListTodo, Loader2, Square } from 'lucide-react'
import { useAnalysisStore, type JobViewSnapshot } from './analysisStore'
import { useMultiAgentAnalysis } from './useMultiAgentAnalysis'
import { cn } from '@/lib/cn'

type QueueStatus = JobViewSnapshot['status']

function fmtAge(ts?: number): string {
  if (!ts) return '—'
  const diff = Date.now() - ts
  if (diff < 60_000) return '刚刚'
  if (diff < 3600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86400_000) return `${Math.floor(diff / 3600_000)} 小时前`
  return new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

/** Reuse the collaboration panel's progress: non-skipped agents participate; completed count toward done. */
function progress(snap: JobViewSnapshot): number {
  const agents = snap.agents ?? []
  const participating = agents.filter((a) => a.status !== 'skipped').length
  const done = agents.filter((a) => a.status === 'completed').length
  if (participating === 0) return snap.status === 'completed' ? 100 : 0
  return Math.round((done / participating) * 100)
}

const STATUS_META: Record<QueueStatus, { label: string; className: string }> = {
  pending: { label: '排队中', className: 'text-muted border-border/50 bg-elevated/40' },
  running: { label: '分析中', className: 'text-sky-300 border-sky-400/30 bg-sky-400/10' },
  completed: { label: '已完成', className: 'text-emerald-300 border-emerald-400/30 bg-emerald-400/10' },
  failed: { label: '已停止', className: 'text-red-300 border-red-400/30 bg-red-400/10' },
}

const STATUS_ORDER: Record<QueueStatus, number> = { running: 0, pending: 1, completed: 2, failed: 3 }

interface Props {
  /** Sync the page's selected symbol/name after switching to a queued task. */
  onSelectSymbol?: (symbol: string, name: string) => void
  /** Switch to the AI 分析 tab after picking a task. */
  onSwitchToAiTab?: () => void
  className?: string
}

/**
 * Multi-task analysis queue: lists in-flight and recently finished in-memory jobs.
 * Clicking a task restores its frozen snapshot and re-subscribes to SSE replay;
 * background tasks keep running in the sidecar. Finished reports remain in 分析历史.
 */
export function AnalysisTaskQueue({ onSelectSymbol, onSwitchToAiTab, className }: Props) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const activeJobId = useAnalysisStore((s) => s.activeJobId)
  const jobViews = useAnalysisStore((s) => s.jobViews)
  const { switchToJob, stopJobById } = useMultiAgentAnalysis()

  const clearCloseTimer = useCallback(() => {
    if (closeTimerRef.current != null) {
      clearTimeout(closeTimerRef.current)
      closeTimerRef.current = null
    }
  }, [])

  const scheduleClose = useCallback(() => {
    clearCloseTimer()
    closeTimerRef.current = setTimeout(() => setOpen(false), 280)
  }, [clearCloseTimer])

  useEffect(() => () => clearCloseTimer(), [clearCloseTimer])

  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: PointerEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) {
        clearCloseTimer()
        setOpen(false)
      }
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        clearCloseTimer()
        setOpen(false)
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open, clearCloseTimer])

  const items = Object.values(jobViews)
    .filter((s) => s && (s.symbol || s.agents.length > 0))
    .sort((a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || b.updatedAt - a.updatedAt)

  const activeCount = items.filter((s) => s.status === 'running' || s.status === 'pending').length

  const handleSwitch = (snap: JobViewSnapshot) => {
    // Sync the page selection first: syncSymbol snapshots the current view and
    // clears the top-level panel. Only then restore the target snapshot, so the
    // clearing pass doesn't wipe the view we just restored.
    if (snap.symbol) onSelectSymbol?.(snap.symbol, snap.name ?? '')
    switchToJob(snap.jobId)
    onSwitchToAiTab?.()
    clearCloseTimer()
    setOpen(false)
  }

  const handleStop = (e: React.MouseEvent, jobId: string) => {
    e.stopPropagation()
    void stopJobById(jobId)
  }

  return (
    <div
      ref={wrapRef}
      className={cn('relative', className)}
      onMouseEnter={() => {
        clearCloseTimer()
        setOpen(true)
      }}
      onMouseLeave={scheduleClose}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => (open ? (clearCloseTimer(), setOpen(false)) : (clearCloseTimer(), setOpen(true)))}
        className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border/50 bg-elevated/40 px-3 text-xs font-medium text-secondary hover:border-border hover:bg-elevated/70 hover:text-foreground transition-colors"
      >
        <ListTodo className="h-3.5 w-3.5" />
        任务队列
        {activeCount > 0 && (
          <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-sky-500/20 px-1 text-[10px] font-semibold text-sky-300">
            {activeCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-full z-50 w-80 pt-1.5">
          <div
            role="listbox"
            className="max-h-72 overflow-y-auto rounded-lg border border-border/60 bg-surface shadow-xl"
            onMouseEnter={clearCloseTimer}
          >
            {items.length === 0 ? (
              <div className="px-3 py-4 text-center text-[11px] text-muted">暂无分析任务</div>
            ) : (
              <ul className="p-1.5 space-y-0.5">
                {items.map((snap) => {
                  const meta = STATUS_META[snap.status]
                  const isActive = snap.jobId === activeJobId
                  const pct = progress(snap)
                  const busy = snap.status === 'running' || snap.status === 'pending'
                  const title = snap.name?.trim() || snap.symbol
                  return (
                    <li key={snap.jobId}>
                      <div
                        role="option"
                        aria-selected={isActive}
                        onClick={() => handleSwitch(snap)}
                        className={cn(
                          'group w-full rounded-md px-2.5 py-2 text-left transition-colors cursor-pointer',
                          isActive ? 'bg-sky-500/10' : 'hover:bg-elevated/60',
                        )}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-[11px] font-medium text-foreground truncate">
                            <span className="text-foreground">{title}</span>
                            {snap.name?.trim() && (
                              <span className="font-mono text-sky-300/90 ml-1.5">{snap.symbol}</span>
                            )}
                          </span>
                          <span className="flex items-center gap-1.5 shrink-0">
                            {busy && <Loader2 className="h-3 w-3 animate-spin text-sky-400" />}
                            <span className={cn('rounded-full border px-1.5 py-0.5 text-[10px]', meta.className)}>
                              {meta.label}
                            </span>
                          </span>
                        </div>
                        <div className="mt-1 flex items-center gap-2 text-[10px] text-muted">
                          <span className="tabular-nums">{busy ? `${pct}%` : snap.status === 'completed' ? '已完成' : '已停止'}</span>
                          <span>·</span>
                          <span>{fmtAge(snap.updatedAt)}</span>
                          {isActive && <span className="text-sky-300/80">当前</span>}
                          {busy && (
                            <button
                              type="button"
                              title="停止任务"
                              onClick={(e) => handleStop(e, snap.jobId)}
                              className="ml-auto inline-flex h-5 w-5 items-center justify-center rounded text-muted hover:text-red-300 hover:bg-red-400/10 transition-colors"
                            >
                              <Square className="h-3 w-3" />
                            </button>
                          )}
                        </div>
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
