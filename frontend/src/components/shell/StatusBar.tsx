import { Loader2 } from 'lucide-react'
import type { IndexQuote } from '@/lib/api'
import { cn } from '@/lib/cn'
import { TopIndexStrip } from './IndexStrip'

interface IndexItem {
  symbol: string
  name: string
}

interface StatusBarProps {
  rows: IndexQuote[] | undefined
  items: readonly IndexItem[]
  /** 实时行情状态: active=盘中运行, paused=管道暂停, off=未开启/不可用 */
  realtime: 'active' | 'paused' | 'off'
  realtimeLabel: string
  sourceName?: string | null
  syncing?: boolean
}

/**
 * 底部状态栏 — 交易终端的常驻状态行。
 * 左侧：四只核心指数（原顶栏指数条下沉，给顶栏减负、顺手消除三处重复）；
 * 右侧：实时行情状态 + 数据源 + 同步态。高度 28px，只读，不承载操作。
 */
export function StatusBar({ rows, items, realtime, realtimeLabel, sourceName, syncing }: StatusBarProps) {
  const dot = realtime === 'active' ? 'bg-bear' : realtime === 'paused' ? 'bg-warning' : 'bg-muted/70'
  return (
    <footer className="flex h-7 shrink-0 items-center gap-3 px-2.5 text-[10px] text-tertiary">
      <div className="min-w-0 flex-1 overflow-hidden">
        <TopIndexStrip rows={rows} items={items} compact />
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <span className="flex items-center gap-1.5 whitespace-nowrap" title={sourceName ? `数据源：${sourceName}` : undefined}>
          <span className={cn('h-1.5 w-1.5 rounded-full', dot)} />
          实时行情 {realtimeLabel}
          {sourceName && <span className="text-muted">· {sourceName}</span>}
        </span>
        {syncing && (
          <span className="flex items-center gap-1 whitespace-nowrap text-accent">
            <Loader2 className="h-3 w-3 animate-spin" />
            数据同步中
          </span>
        )}
      </div>
    </footer>
  )
}
