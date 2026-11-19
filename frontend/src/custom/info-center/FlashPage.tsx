/** 快讯 — 全市场快讯，按关注池打标；30 秒自动刷新。 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Radio, RefreshCw } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { cn } from '@/lib/cn'
import { formatTime, infoApi, type FlashEvent } from './infoClient'
import { Chip, EmptyBox, FilterInput, Pagination, StatStrip, Toolbar } from './controls'

export function FlashPage() {
  const [q, setQ] = useState('')
  const [date, setDate] = useState('')
  const [hit, setHit] = useState(false)
  const [page, setPage] = useState(1)

  const listQ = useQuery({
    queryKey: ['info', 'flash', q, date, hit, page],
    queryFn: () => infoApi.flash({ q, date, hit: hit ? 1 : undefined, page, size: 50 }),
    refetchInterval: 30_000,
    staleTime: 15_000,
  })
  const items = listQ.data?.items ?? []
  const total = listQ.data?.total ?? 0
  const last = items[0]?.publish_time

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        className="pl-14 lg:pl-5"
        title="快讯"
        subtitle="同花顺 7x24 A股快讯 · 关注池命中标记 · 30 秒自动刷新"
        right={
          <div className="flex items-center gap-2 text-[11px] text-muted">
            <Radio className={cn('h-3.5 w-3.5', listQ.isFetching ? 'text-accent' : 'text-muted')} />
            <span>最后更新 {formatTime(last) || '—'}</span>
            <button type="button" onClick={() => void listQ.refetch()} className="rounded-btn border border-border p-1.5 text-secondary hover:text-foreground" aria-label="刷新">
              <RefreshCw className={cn('h-3.5 w-3.5', listQ.isFetching && 'animate-spin')} />
            </button>
          </div>
        }
      />
      <div className="border-b border-border px-4 py-2">
        <StatStrip
          items={[
            { label: '快讯总量', value: total || '—', accent: true },
            { label: '当前筛选', value: items.length },
            { label: '关注池命中', value: items.filter(i => i.sec_codes?.length).length },
          ]}
        />
      </div>
      <Toolbar>
        <FilterInput type="date" value={date} onChange={v => { setDate(v); setPage(1) }} />
        <FilterInput value={q} onChange={v => { setQ(v); setPage(1) }} placeholder="搜索标题 / 摘要" className="w-64" />
        <Chip active={hit} onClick={() => { setHit(!hit); setPage(1) }}>仅关注池命中</Chip>
        {(q || date || hit) && <Chip onClick={() => { setQ(''); setDate(''); setHit(false); setPage(1) }}>清空</Chip>}
      </Toolbar>
      <div className="min-h-0 flex-1 overflow-auto px-4 py-2">
        {items.map(item => (
          <FlashRow key={item.event_id} item={item} />
        ))}
        {!listQ.isLoading && items.length === 0 && <EmptyBox text="暂无快讯" />}
      </div>
      <Pagination page={page} size={50} total={total} onPage={setPage} />
    </div>
  )
}

function FlashRow({ item }: { item: FlashEvent }) {
  return (
    <div className="flex gap-3 border-b border-border/60 py-2.5">
      <div className="w-[112px] shrink-0 font-mono text-[10px] text-muted">{formatTime(item.publish_time)}</div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[12px] font-medium text-foreground">{item.title || item.summary.slice(0, 40)}</span>
          {(item.sec_names ?? []).map(name => (
            <span key={name} className="rounded border border-accent/30 bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">{name}</span>
          ))}
          {item.sector && <span className="rounded border border-border px-1.5 py-0.5 text-[10px] text-muted">{item.sector}</span>}
          {(item.sec_codes ?? []).length === 0 && <span className="text-[10px] text-muted">全市场</span>}
        </div>
        {item.summary && <p className="mt-1 line-clamp-2 text-[11px] leading-5 text-secondary">{item.summary}</p>}
        {item.url && (
          <a href={item.url} target="_blank" rel="noreferrer" className="mt-1 inline-block text-[10px] text-accent hover:underline">原文</a>
        )}
      </div>
    </div>
  )
}
