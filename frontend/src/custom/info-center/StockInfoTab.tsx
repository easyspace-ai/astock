/** 个股“资讯”Tab：公告 / 新闻 / 研报 / 快讯四类合并时间线。 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { cn } from '@/lib/cn'
import { formatTime, infoApi, sourceText } from './infoClient'
import { Chip, EmptyBox, KindBadge } from './controls'

const TABS = [
  { value: 'all', label: '全部' },
  { value: 'announcement', label: '公告' },
  { value: 'news', label: '新闻' },
  { value: 'report', label: '研报' },
  { value: 'flash', label: '快讯' },
]

export function StockInfoTab({ symbol, name }: { symbol: string; name?: string | null }) {
  const [tab, setTab] = useState('all')
  const [days, setDays] = useState(30)
  const kinds = tab === 'all' ? undefined : tab
  const q = useQuery({
    queryKey: ['info', 'timeline', symbol, days, kinds],
    queryFn: () => infoApi.timeline(symbol, days, kinds),
    staleTime: 30_000,
  })
  const data = q.data
  const events = data?.events ?? []

  return (
    <div className="flex max-h-[60vh] min-h-[260px] flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2">
        {TABS.map(t => (
          <Chip key={t.value} active={tab === t.value} onClick={() => setTab(t.value)}>{t.label}</Chip>
        ))}
        <span className="ml-auto text-[10px] text-muted">{name || data?.name || symbol}</span>
        {[7, 30, 90, 400].map(d => (
          <Chip key={d} active={days === d} onClick={() => setDays(d)}>{d}天</Chip>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-2">
        {events.map(event => (
          <div key={`${event.event_kind}-${event.event_id}`} className="flex gap-3 border-b border-border/60 py-2">
            <div className="w-[104px] shrink-0">
              <div className="font-mono text-[10px] text-muted">{formatTime(event.disclosed_at || event.publish_time || event.ann_date)}</div>
              <div className="mt-1"><KindBadge kind={event.event_kind} /></div>
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[12px] text-foreground">{event.title}</div>
              <div className="mt-1 flex flex-wrap gap-x-2 text-[10px] text-muted">
                {event.sources?.length > 0 && <span>{sourceText(event.sources)}</span>}
                {'type_primary' in event && event.type_primary && <span>{event.type_primary}</span>}
                {'sector' in event && event.sector && <span>{event.sector}</span>}
                {'summary' in event && event.summary && <span className="line-clamp-1 max-w-full">{event.summary}</span>}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2 text-[10px]">
              {'pdf_url' in event && event.pdf_url && <a href={event.pdf_url} target="_blank" rel="noreferrer" className="text-accent hover:underline">PDF</a>}
              {'url' in event && event.url && <a href={event.url} target="_blank" rel="noreferrer" className="text-accent hover:underline">原文</a>}
            </div>
          </div>
        ))}
        {!q.isLoading && events.length === 0 && <EmptyBox text="该窗口没有资讯" />}
      </div>
      <div className={cn('border-t border-border px-4 py-1.5 text-[10px]', q.isError ? 'text-danger' : 'text-muted')}>
        {q.isError ? '加载失败，请稍后重试' : `共 ${events.length} 条 · 业务日期 ${data?.since ?? '—'} 起`}
      </div>
    </div>
  )
}
