import { NavLink } from 'react-router-dom'
import type { IndexQuote } from '@/lib/api'
import { cn } from '@/lib/cn'

interface IndexItem {
  symbol: string
  name: string
}

function fmtValue(v: number | null | undefined) {
  if (v == null || Number.isNaN(Number(v))) return '--'
  return Number(v).toFixed(2)
}

function fmtPct(v: number | null | undefined) {
  if (v == null || Number.isNaN(Number(v))) return '--'
  return `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`
}

function pctClass(v: number | null | undefined) {
  if (v == null || Number.isNaN(Number(v))) return 'text-muted'
  const n = Number(v)
  if (n === 0) return 'text-foreground'
  return n > 0 ? 'text-bull' : 'text-bear'
}

/**
 * 顶栏指数条 — 从侧栏底部上移，224px 导航里挤不下的四只核心指数在这里横排。
 * 数据与路由契约不变：同一批 /indices?symbol= 链接。
 */
export function TopIndexStrip({ rows, items, compact = false }: { rows: IndexQuote[] | undefined; items: readonly IndexItem[]; compact?: boolean }) {
  if (items.length === 0) return null
  const quoteBySymbol = new Map((rows ?? []).map(q => [q.symbol, q]))
  return (
    <div className="flex items-center gap-0.5 overflow-hidden">
      {items.map(item => {
        const q = quoteBySymbol.get(item.symbol)
        const value = q?.last_price ?? q?.close
        const pct = q?.change_pct
        return (
          <NavLink
            key={item.symbol}
            to={`/indices?symbol=${encodeURIComponent(item.symbol)}`}
            title={`${item.name} ${item.symbol}`}
            className={cn(
              'flex items-baseline gap-1.5 whitespace-nowrap rounded-control transition-colors duration-150 ease-smooth hover:bg-elevated/60',
              compact ? 'px-1 py-0' : 'px-2 py-1',
            )}
          >
            <span className={cn('text-tertiary', compact ? 'text-[10px]' : 'text-[11px]')}>{item.name}</span>
            <span className={cn('font-mono tabular-nums', compact ? 'text-[10px]' : 'text-[11px]', pctClass(pct))}>{fmtValue(value)}</span>
            <span className={cn('font-mono tabular-nums', compact ? 'text-[10px]' : 'text-[11px]', pctClass(pct))}>{fmtPct(pct)}</span>
          </NavLink>
        )
      })}
    </div>
  )
}
