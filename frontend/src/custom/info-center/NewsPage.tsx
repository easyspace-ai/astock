/** 新闻中心 — 关注池新闻、研报与来源筛选。 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ExternalLink, RefreshCw, X } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { cn } from '@/lib/cn'
import { formatTime, infoApi, sourceText, type NewsEvent } from './infoClient'
import {
  Chip,
  EmptyBox,
  FilterInput,
  KindBadge,
  Pagination,
  Select,
  SourceBadge,
  StatStrip,
  Toolbar,
} from './controls'

const KIND_OPTIONS = [
  { value: '', label: '全部资讯' },
  { value: 'news', label: '新闻' },
  { value: 'report', label: '研报' },
]

const SOURCE_OPTIONS = [
  { value: '', label: '全部来源' },
  { value: 'ths', label: '同花顺' },
  { value: 'em', label: '东方财富' },
]

function NewsDetail({ item, onClose }: { item: NewsEvent; onClose: () => void }) {
  return (
    <aside className="flex w-full shrink-0 flex-col border-l border-border bg-surface lg:w-[420px]">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <span className="text-xs font-medium text-foreground">资讯详情</span>
        <button type="button" onClick={onClose} className="rounded-btn p-1 text-muted hover:bg-elevated hover:text-foreground" aria-label="关闭">
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="flex flex-wrap items-center gap-2">
          <KindBadge kind={item.news_kind} />
          <SourceBadge sources={item.sources} />
          {item.sector && <span className="rounded border border-border px-1.5 py-0.5 text-[10px] text-muted">{item.sector}</span>}
        </div>
        <h2 className="mt-3 text-base font-semibold leading-snug text-foreground">{item.title}</h2>
        <div className="mt-2 font-mono text-[11px] text-muted">
          {item.symbol} · {item.name || '—'} · {formatTime(item.publish_time)}
        </div>
        <div className="mt-1 text-[11px] text-muted">媒体：{item.media || '—'}</div>
        <p className="mt-4 whitespace-pre-wrap text-[12px] leading-6 text-secondary">{item.summary || item.content || '暂无摘要'}</p>
        {item.url && (
          <a href={item.url} target="_blank" rel="noreferrer" className="mt-4 inline-flex items-center gap-1.5 rounded-btn border border-border px-2.5 py-1.5 text-[11px] text-foreground hover:border-accent/40">
            <ExternalLink className="h-3.5 w-3.5" /> 原文
          </a>
        )}
      </div>
    </aside>
  )
}

export function NewsPage() {
  const [sector, setSector] = useState('')
  const [kind, setKind] = useState('')
  const [source, setSource] = useState('')
  const [q, setQ] = useState('')
  const [date, setDate] = useState('')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<NewsEvent | null>(null)

  const statsQ = useQuery({ queryKey: ['info', 'stats', 'news'], queryFn: () => infoApi.stats(), staleTime: 30_000 })
  const listQ = useQuery({
    queryKey: ['info', 'news', sector, kind, source, q, date, page],
    queryFn: () => infoApi.news({ sector, kind, source, q, date, page, size: 30 }),
    staleTime: 20_000,
  })
  const stats = statsQ.data
  const items = listQ.data?.items ?? []
  const total = listQ.data?.total ?? 0

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        className="pl-14 lg:pl-5"
        title="新闻中心"
        subtitle="关注池新闻 / 研报 · 同花顺主源 + 东方财富补充"
        right={
          <button type="button" onClick={() => void listQ.refetch()} className="rounded-btn border border-border p-1.5 text-secondary hover:text-foreground" aria-label="刷新">
            <RefreshCw className={cn('h-3.5 w-3.5', listQ.isFetching && 'animate-spin')} />
          </button>
        }
      />
      <div className="border-b border-border px-4 py-2">
        <StatStrip
          items={[
            { label: '资讯总量', value: stats?.totals.news ?? '—', accent: true },
            { label: '新闻', value: stats?.news.news ?? '—' },
            { label: '研报', value: stats?.news.report ?? '—' },
            { label: '覆盖板块', value: Object.keys(stats?.sectors ?? {}).length || '—' },
          ]}
        />
      </div>
      <Toolbar>
        <FilterInput type="date" value={date} onChange={v => { setDate(v); setPage(1) }} />
        <FilterInput value={q} onChange={v => { setQ(v); setPage(1) }} placeholder="标题 / 公司 / 关键词" className="w-56" />
        <Select ariaLabel="资讯类型" value={kind} onChange={v => { setKind(v); setPage(1) }} options={KIND_OPTIONS} />
        <Select ariaLabel="来源" value={source} onChange={v => { setSource(v); setPage(1) }} options={SOURCE_OPTIONS} />
        <div className="flex flex-wrap gap-1.5">
          {Object.keys(stats?.sectors ?? {}).map(name => (
            <Chip key={name} active={sector === name} onClick={() => { setSector(sector === name ? '' : name); setPage(1) }}>{name}</Chip>
          ))}
        </div>
        {(sector || kind || source || q || date) && (
          <Chip onClick={() => { setSector(''); setKind(''); setSource(''); setQ(''); setDate(''); setPage(1) }}>清空</Chip>
        )}
      </Toolbar>

      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 overflow-auto">
          {items.map(item => (
            <button
              type="button"
              key={item.event_id}
              onClick={() => setSelected(item)}
              className={cn(
                'flex w-full items-start gap-3 border-b border-border/70 px-4 py-2.5 text-left hover:bg-elevated/40',
                selected?.event_id === item.event_id && 'bg-accent/5',
              )}
            >
              <div className="w-[124px] shrink-0">
                <div className="font-mono text-[10px] text-muted">{formatTime(item.publish_time)}</div>
                <div className="mt-1"><SourceBadge sources={item.sources} /></div>
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <KindBadge kind={item.news_kind} />
                  <span className="truncate text-[12px] font-medium text-foreground">{item.title}</span>
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted">
                  <span className="font-mono">{item.symbol}</span>
                  <span>{item.name}</span>
                  {item.sector && <span>{item.sector}</span>}
                  <span>{item.media || '—'}</span>
                  <span>匹配：{item.match_type}</span>
                  {item.match_status === 'both' && <span className="text-accent">跨源合并</span>}
                </div>
                {item.summary && <p className="mt-1 line-clamp-2 text-[11px] leading-5 text-secondary">{item.summary}</p>}
              </div>
            </button>
          ))}
          {!listQ.isLoading && items.length === 0 && <EmptyBox text="该筛选下没有资讯" />}
        </div>
        {selected && <NewsDetail item={selected} onClose={() => setSelected(null)} />}
      </div>
      <Pagination page={page} size={30} total={total} onPage={setPage} />
      <div className="px-4 py-1 text-[10px] text-muted">来源说明：{sourceText(['ths', 'em'])}；同源内已按标题相似度与时间窗口去重。</div>
    </div>
  )
}
