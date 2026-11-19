/** 公告中心 — 双源统一事件流，保留来源差异与 PDF/详情入口。 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download, FileText, RefreshCw, X } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { cn } from '@/lib/cn'
import {
  formatTime,
  infoApi,
  SOURCE_LABEL,
  sourceText,
  type AnnouncementEvent,
  type InfoStats,
} from './infoClient'
import {
  Chip,
  EmptyBox,
  FilterInput,
  Pagination,
  Select,
  SourceBadge,
  StatStrip,
  Toolbar,
} from './controls'

const SOURCE_OPTIONS = [
  { value: '', label: '全部来源' },
  { value: 'both', label: '双源匹配' },
  { value: 'only_cninfo', label: '仅巨潮' },
  { value: 'only_em', label: '仅东财' },
]

const TYPE_OPTIONS = [
  { value: '', label: '全部类型' },
  { value: '定期报告', label: '定期报告' },
  { value: '业绩预告', label: '业绩预告' },
  { value: '分红送转', label: '分红送转' },
  { value: '增减持', label: '增减持' },
  { value: '回购', label: '回购' },
  { value: '重组并购', label: '重组并购' },
  { value: '合同业务', label: '合同业务' },
  { value: '风险事项', label: '风险事项' },
  { value: '监管问询', label: '监管问询' },
]

function AnnouncementDetail({ event, onClose }: { event: AnnouncementEvent; onClose: () => void }) {
  return (
    <aside className="flex w-full shrink-0 flex-col border-l border-border bg-surface lg:w-[420px]">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <span className="text-xs font-medium text-foreground">公告详情</span>
        <button type="button" onClick={onClose} className="rounded-btn p-1 text-muted hover:bg-elevated hover:text-foreground" aria-label="关闭">
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="flex flex-wrap items-center gap-2">
          <SourceBadge sources={event.sources} />
          <span className="rounded border border-border px-1.5 py-0.5 text-[10px] text-muted">{event.type_primary}</span>
          {event.is_material && <span className="rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-300">重要</span>}
        </div>
        <h2 className="mt-3 text-base font-semibold leading-snug text-foreground">{event.title}</h2>
        <div className="mt-2 font-mono text-[11px] text-muted">
          {event.symbol} · {event.name || '—'} · {formatTime(event.disclosed_at || event.ann_date)}
        </div>
        {event.industry_l1 && <div className="mt-1 text-[11px] text-muted">行业：{event.industry_l1}</div>}

        <div className="mt-4 grid grid-cols-2 gap-px overflow-hidden rounded border border-border bg-border text-[11px]">
          {[
            ['业务日期', event.biz_date],
            ['披露时点', event.disclosed_at ? formatTime(event.disclosed_at) : '仅日期'],
            ['时间精度', event.time_precision],
            ['盘口', event.session],
            ['匹配方式', event.match_note || event.match_status],
            ['匹配分', event.match_score?.toFixed(2) ?? '—'],
          ].map(([k, v]) => (
            <div key={k} className="bg-surface px-2.5 py-2">
              <div className="text-muted">{k}</div>
              <div className="mt-0.5 font-mono text-foreground">{v || '—'}</div>
            </div>
          ))}
        </div>

        <div className="mt-5">
          <div className="text-[11px] font-medium text-foreground">原始来源变体</div>
          <div className="mt-2 space-y-1.5">
            {event.variants.map(v => (
              <div key={`${v.source}-${v.id}`} className="rounded-btn border border-border px-2.5 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] text-secondary">{SOURCE_LABEL[v.source] || v.source}</span>
                  <span className="font-mono text-[10px] text-muted">{v.id || '—'}</span>
                </div>
                <div className="mt-1 text-[11px] text-foreground">{v.title}</div>
                {v.url && (
                  <a href={v.url} target="_blank" rel="noreferrer" className="mt-1 inline-flex text-[10px] text-accent hover:underline">
                    打开原始页
                  </a>
                )}
              </div>
            ))}
          </div>
        </div>

        <div className="mt-5 flex flex-wrap gap-2">
          {event.pdf_url && (
            <a href={event.pdf_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 rounded-btn border border-border px-2.5 py-1.5 text-[11px] text-foreground hover:border-accent/40">
              <FileText className="h-3.5 w-3.5" /> PDF
            </a>
          )}
          {event.detail_url && (
            <a href={event.detail_url} target="_blank" rel="noreferrer" className="rounded-btn border border-border px-2.5 py-1.5 text-[11px] text-foreground hover:border-accent/40">
              原公告页
            </a>
          )}
        </div>
      </div>
    </aside>
  )
}

export function AnnouncementsPage() {
  const [date, setDate] = useState('')
  const [source, setSource] = useState('')
  const [type, setType] = useState('')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<AnnouncementEvent | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [syncMessage, setSyncMessage] = useState('')

  const statsQ = useQuery({ queryKey: ['info', 'stats', date], queryFn: () => infoApi.stats(date || undefined), staleTime: 30_000 })
  const listQ = useQuery({
    queryKey: ['info', 'announcements', date, source, type, q, page],
    queryFn: () => infoApi.announcements({ date, source, type, q, page, size: 30 }),
    staleTime: 20_000,
  })
  const detailQ = useQuery({
    queryKey: ['info', 'announcement', selected?.event_id],
    queryFn: () => infoApi.announcement(selected!.event_id),
    enabled: Boolean(selected?.event_id),
    initialData: selected ?? undefined,
  })

  const stats: InfoStats | undefined = statsQ.data
  const items = listQ.data?.items ?? []
  const total = listQ.data?.total ?? 0
  const detail = selected ? (detailQ.data ?? selected) : null

  async function handleSync() {
    const target = date || new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(new Date())
    const startedAt = new Intl.DateTimeFormat('sv-SE', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    }).format(new Date())
    setSyncing(true)
    setSyncMessage('抓取中，历史日期可能需要几分钟，完成后会自动刷新…')

    // 公告抓取可能持续几十秒；请求先发出，页面通过健康状态里的运行日志判断完成，
    // 避免 HTTP 请求本身超时后，后台其实已经抓完却给用户报错。
    // 手动触发请求只负责“发出”；完成状态以运行日志为准，避免客户端 HTTP
    // 先超时、但服务端其实已经抓完的假失败。
    void infoApi.sync('announcements', true, date || undefined).catch(() => undefined)

    const deadline = Date.now() + 300_000
    try {
      while (Date.now() < deadline) {
        await new Promise(resolve => window.setTimeout(resolve, 2_000))
        const health = (await infoApi.health()) as {
          runs?: Array<{ at: string; action: string; date?: string; cninfo?: number; em?: number; errors?: string[]; degraded?: boolean }>
        }
        const run = (health.runs ?? []).find(
          row => row.action === 'fetch_announcements' && row.date === target && row.at >= startedAt,
        )
        if (!run) continue
        if (run.errors?.length) {
          setSyncMessage(`抓取部分失败：${run.errors.join('；')}`)
        } else {
          setSyncMessage(`已抓取 ${target}：巨潮 ${run.cninfo ?? 0} 条 / 东财 ${run.em ?? 0} 条`)
        }
        await infoApi.reload()
        await Promise.all([listQ.refetch(), statsQ.refetch()])
        return
      }
      setSyncMessage('抓取超时，后台可能仍在写入；可稍后点刷新查看')
    } catch (error) {
      setSyncMessage(error instanceof Error ? error.message : '抓取失败')
    } finally {
      setSyncing(false)
    }
  }

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        className="pl-14 lg:pl-5"
        title="公告中心"
        subtitle="巨潮 + 东财双源统一事件流 · 保留原始来源与匹配差异"
        right={
          <div className="flex flex-wrap items-center justify-end gap-2">
            <span className={cn('max-w-[260px] truncate text-[11px]', listQ.isFetching ? 'text-accent' : 'text-muted')} title={syncMessage || undefined}>
              {syncMessage || (listQ.isFetching ? '刷新中' : '已加载')}
            </span>
            <button
              type="button"
              onClick={() => void handleSync()}
              disabled={syncing}
              className="inline-flex items-center gap-1.5 rounded-btn border border-accent/40 bg-accent/10 px-2 py-1.5 text-[11px] text-accent hover:bg-accent/20 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <Download className={cn('h-3.5 w-3.5', syncing && 'animate-pulse')} />
              {syncing ? '抓取中' : date ? '抓取该日' : '抓取今日'}
            </button>
            <button type="button" onClick={() => void listQ.refetch()} className="rounded-btn border border-border p-1.5 text-secondary hover:text-foreground" aria-label="刷新">
              <RefreshCw className={cn('h-3.5 w-3.5', listQ.isFetching && 'animate-spin')} />
            </button>
          </div>
        }
      />
      <div className="px-4 py-2 border-b border-border">
        <StatStrip
          items={[
            { label: date ? '当日公告' : '公告总量', value: stats?.totals.announcements ?? '—', accent: true },
            { label: '双源', value: stats?.announcements.both ?? '—' },
            { label: '仅巨潮', value: stats?.announcements.cninfo_only ?? '—' },
            { label: '仅东财', value: stats?.announcements.em_only ?? '—' },
            { label: '重要', value: stats?.announcements.material ?? '—' },
          ]}
        />
      </div>
      <Toolbar>
        <FilterInput type="date" value={date} onChange={v => { setDate(v); setPage(1); setSyncMessage('') }} />
        <Select ariaLabel="来源筛选" value={source} onChange={v => { setSource(v); setPage(1) }} options={SOURCE_OPTIONS} />
        <Select ariaLabel="类型筛选" value={type} onChange={v => { setType(v); setPage(1) }} options={TYPE_OPTIONS} />
        <FilterInput value={q} onChange={v => { setQ(v); setPage(1) }} placeholder="标题 / 公司 / 代码" className="w-52" />
        <div className="ml-auto flex gap-1.5">
          {(date || source || type || q) && (
            <Chip onClick={() => { setDate(''); setSource(''); setType(''); setQ(''); setPage(1) }}>清空筛选</Chip>
          )}
        </div>
      </Toolbar>

      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 overflow-auto">
          <div>
            <div className="grid grid-cols-[110px_76px_122px_1fr] gap-2 border-b border-border px-4 py-2 text-[10px] text-muted lg:grid-cols-[130px_92px_150px_1fr_96px_90px]">
              <span>披露时间</span>
              <span>来源</span>
              <span>公司</span>
              <span>标题</span>
              <span className="hidden lg:block">类型</span>
              <span className="hidden lg:block">行业</span>
            </div>
            {items.map(item => (
              <button
                type="button"
                key={item.event_id}
                onClick={() => setSelected(item)}
                className={cn(
                  'grid w-full grid-cols-[110px_76px_122px_1fr] items-center gap-2 border-b border-border/70 px-4 py-2 text-left text-[11px] hover:bg-elevated/40 lg:grid-cols-[130px_92px_150px_1fr_96px_90px]',
                  selected?.event_id === item.event_id && 'bg-accent/5',
                )}
              >
                <span className="font-mono text-[10px] text-muted">{formatTime(item.disclosed_at || item.ann_date)}</span>
                <SourceBadge sources={item.sources} />
                <span className="truncate"><span className="font-mono text-[10px] text-muted">{item.symbol}</span> {item.name}</span>
                <span className="truncate text-foreground">{item.title}</span>
                <span className="hidden truncate text-secondary lg:block">{item.type_primary}{item.is_material && <span className="ml-1 text-amber-400">·</span>}</span>
                <span className="hidden truncate text-muted lg:block">{item.industry_l1 || '—'}</span>
              </button>
            ))}
            {!listQ.isLoading && items.length === 0 && <EmptyBox text={date ? `该日期暂无公告，可点击右上角「抓取该日」` : '该筛选下没有公告'} />}
          </div>
        </div>
        {detail && <AnnouncementDetail event={detail} onClose={() => setSelected(null)} />}
      </div>
      <Pagination page={page} size={30} total={total} onPage={setPage} />
      <div className="px-4 py-1 text-[10px] text-muted">来源说明：{sourceText(['cninfo', 'em'])}；点击行查看双源变体与 PDF。</div>
    </div>
  )
}
