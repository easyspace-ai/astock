/** 资讯中心共用的小型控件；保持 TSP 现有视觉语汇。 */
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { SOURCE_LABEL } from './infoClient'

export function Toolbar({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2">{children}</div>
}

export function FilterInput({
  value,
  onChange,
  placeholder,
  type = 'text',
  className,
}: {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  type?: string
  className?: string
}) {
  return (
    <input
      type={type}
      value={value}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      className={cn(
        'h-8 rounded-btn border border-border bg-surface px-2.5 text-xs text-foreground outline-none placeholder:text-muted focus:border-accent/50',
        className,
      )}
    />
  )
}

export function Select({
  value,
  onChange,
  options,
  ariaLabel,
}: {
  value: string
  onChange: (value: string) => void
  options: Array<{ value: string; label: string }>
  ariaLabel: string
}) {
  return (
    <select
      aria-label={ariaLabel}
      value={value}
      onChange={e => onChange(e.target.value)}
      className="h-8 rounded-btn border border-border bg-surface px-2 text-xs text-foreground outline-none focus:border-accent/50"
    >
      {options.map(o => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  )
}

export function Chip({
  active,
  onClick,
  children,
}: {
  active?: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'h-7 rounded-btn border px-2.5 text-[11px] transition-colors',
        active
          ? 'border-accent/50 bg-accent/10 text-accent'
          : 'border-border bg-surface text-secondary hover:border-accent/30 hover:text-foreground',
      )}
    >
      {children}
    </button>
  )
}

export function SourceBadge({ sources }: { sources: string[] }) {
  const both = sources.includes('cninfo') && sources.includes('em')
  const label = both ? '巨潮 + 东财' : sources.map(s => SOURCE_LABEL[s] || s).join(' / ') || '未知'
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center rounded border px-1.5 py-0.5 text-[10px]',
        both
          ? 'border-accent/30 bg-accent/10 text-accent'
          : sources.includes('cninfo')
            ? 'border-sky-500/30 bg-sky-500/10 text-sky-300'
            : sources.includes('em')
              ? 'border-amber-500/30 bg-amber-500/10 text-amber-300'
              : 'border-border bg-elevated text-muted',
      )}
    >
      {label}
    </span>
  )
}

export function KindBadge({ kind }: { kind: string }) {
  const map: Record<string, string> = {
    announcement: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
    news: 'border-sky-500/30 bg-sky-500/10 text-sky-300',
    report: 'border-violet-500/30 bg-violet-500/10 text-violet-300',
    flash: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
  }
  const label: Record<string, string> = { announcement: '公告', news: '新闻', report: '研报', flash: '快讯' }
  return (
    <span className={cn('inline-flex shrink-0 items-center rounded border px-1.5 py-0.5 text-[10px]', map[kind] || 'border-border bg-elevated text-muted')}>
      {label[kind] || kind}
    </span>
  )
}

export function StatStrip({ items }: { items: Array<{ label: string; value: string | number; accent?: boolean }> }) {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-[11px] text-muted">
      {items.map(item => (
        <span key={item.label} className="inline-flex items-baseline gap-1">
          <span>{item.label}</span>
          <span className={cn('font-mono text-sm tabular-nums', item.accent ? 'text-accent' : 'text-foreground')}>{item.value}</span>
        </span>
      ))}
    </div>
  )
}

export function EmptyBox({ text }: { text: string }) {
  return <div className="flex h-40 items-center justify-center text-xs text-muted">{text}</div>
}

export function Pagination({
  page,
  size,
  total,
  onPage,
}: {
  page: number
  size: number
  total: number
  onPage: (page: number) => void
}) {
  const pages = Math.max(1, Math.ceil(total / size))
  return (
    <div className="flex items-center justify-between border-t border-border px-4 py-2 text-[11px] text-muted">
      <span>共 {total} 条 · 第 {page}/{pages} 页</span>
      <div className="flex gap-1.5">
        <button type="button" disabled={page <= 1} onClick={() => onPage(page - 1)} className="rounded-btn border border-border px-2 py-1 disabled:opacity-40">上一页</button>
        <button type="button" disabled={page >= pages} onClick={() => onPage(page + 1)} className="rounded-btn border border-border px-2 py-1 disabled:opacity-40">下一页</button>
      </div>
    </div>
  )
}
