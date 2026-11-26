import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowRight, Command, Search } from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { usePermissions } from '@/lib/useAccount'
import { PERM } from '@/lib/permissions'
import { cn } from '@/lib/cn'

/**
 * 全局标的搜索 — 顶栏中部的常驻入口，⌘K / Ctrl+K 唤起。
 * 只做「跳到个股分析」这一个动作：选中标的后带 ?symbol= 深链过去，
 * 个股页读参数恢复上下文，避免每次都要在页内再搜一次。
 */
const ASSET_TYPES = 'stock,index,etf'

export function GlobalSearch() {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()
  // 这个入口唯一动作是跳「个股分析」; 没有该页面权限时不出现, 避免搜出进不去的页面
  const { can } = usePermissions()

  // ⌘K / Ctrl+K 全局唤起；Esc 关闭交给面板自己处理
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen(v => !v)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    if (!open) {
      setQ('')
      setActive(0)
      return
    }
    // 打开后等一帧再聚焦，避免动画期间 focus 被吃掉
    const t = window.setTimeout(() => inputRef.current?.focus(), 30)
    return () => window.clearTimeout(t)
  }, [open])

  const query = useQuery({
    queryKey: QK.instrumentSearch(q, ASSET_TYPES),
    queryFn: () => api.instrumentSearch(q, 12, ASSET_TYPES),
    enabled: open && q.trim().length > 0,
    staleTime: 60_000,
  })

  const results = useMemo(() => query.data?.results ?? [], [query.data])
  const picked = results[Math.min(active, Math.max(results.length - 1, 0))]

  const go = (symbol: string) => {
    setOpen(false)
    navigate(`/stock-analysis?symbol=${encodeURIComponent(symbol)}`)
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault()
      setOpen(false)
      return
    }
    if (!results.length) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive(i => (i + 1) % results.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive(i => (i - 1 + results.length) % results.length)
    } else if (e.key === 'Enter' && picked) {
      e.preventDefault()
      go(picked.symbol)
    }
  }

  if (!can(PERM.stockAnalysis)) return null

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title="搜索标的 (⌘K)"
        className="group flex h-7 w-full max-w-[18rem] items-center gap-2 rounded-control border border-border bg-transparent px-2 text-[11px] text-muted transition-colors duration-150 hover:border-border-strong hover:bg-elevated/30 hover:text-secondary"
      >
        <Search className="h-3.5 w-3.5 shrink-0" />
        <span className="truncate">搜索标的</span>
        <span className="ml-auto hidden shrink-0 items-center gap-0.5 rounded border border-border/60 px-1 py-0.5 font-mono text-[9px] text-muted/80 sm:flex">
          <Command className="h-2.5 w-2.5" />K
        </span>
      </button>

      {open && (
        <div
          className="fixed inset-0 z-[70] flex items-start justify-center bg-black/30 px-4 pt-[12vh] backdrop-blur-[2px]"
          onClick={() => setOpen(false)}
          role="dialog"
          aria-modal="true"
          aria-label="全局标的搜索"
        >
          <div
            className="w-full max-w-[34rem] overflow-hidden rounded-card border border-border bg-floating shadow-2xl"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center gap-2 border-b border-border px-3 py-2.5">
              <Search className="h-4 w-4 shrink-0 text-muted" />
              <input
                ref={inputRef}
                value={q}
                onChange={e => { setQ(e.target.value); setActive(0) }}
                onKeyDown={onKeyDown}
                placeholder="输入代码或名称，如 600519 / 贵州茅台"
                className="min-w-0 flex-1 bg-transparent text-[13px] text-foreground outline-none placeholder:text-muted/70"
              />
              <span className="shrink-0 font-mono text-[9px] text-muted/70">ESC</span>
            </div>

            <div className="max-h-[46vh] overflow-y-auto">
              {q.trim().length === 0 ? (
                <p className="px-3 py-6 text-center text-[11px] text-muted">输入代码或名称开始搜索</p>
              ) : query.isFetching && results.length === 0 ? (
                <p className="px-3 py-6 text-center text-[11px] text-muted">搜索中…</p>
              ) : results.length === 0 ? (
                <p className="px-3 py-6 text-center text-[11px] text-muted">没有匹配的标的</p>
              ) : (
                <ul className="p-1">
                  {results.map((r, i) => (
                    <li key={r.symbol}>
                      <button
                        type="button"
                        onMouseEnter={() => setActive(i)}
                        onClick={() => go(r.symbol)}
                        className={cn(
                          'flex w-full items-center gap-3 rounded-control px-2 py-1.5 text-left',
                          i === active ? 'bg-elevated text-foreground' : 'text-secondary hover:bg-elevated/60',
                        )}
                      >
                        <span className="min-w-0 flex-1 truncate text-[12px]">{r.name || r.symbol}</span>
                        <span className="shrink-0 font-mono text-[10px] text-muted">{r.symbol}</span>
                        {r.asset_type && r.asset_type !== 'stock' && (
                          <span className="shrink-0 rounded border border-border/60 px-1 text-[9px] text-muted">{r.asset_type}</span>
                        )}
                        {i === active && <ArrowRight className="h-3 w-3 shrink-0 text-accent" />}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="flex items-center gap-3 border-t border-border px-3 py-1.5 text-[9px] text-muted">
              <span>↑↓ 选择</span>
              <span>Enter 打开</span>
              <span className="ml-auto">个股分析</span>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
