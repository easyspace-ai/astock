/** 公司图谱详情 — 公司层/人层/消息层三段展示, 导航页与个股 tab 复用。 */
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Building2,
  ChevronDown,
  ExternalLink,
  FileText,
  Landmark,
  Newspaper,
  RefreshCw,
  Users,
} from 'lucide-react'
import { toast } from '@/components/Toast'
import { cn } from '@/lib/cn'
import { fmtBigNum, priceColorClass } from '@/lib/format'
import {
  graphApi,
  type Announcement,
  type CompanyDetailData,
  type ExecutiveDetail,
  type NewsItem,
  type Top10Holder,
} from './graphClient'

const PART_LABELS: Record<string, string> = {
  profile: '档案',
  shareholders: '前五大',
  holders_top10: '十大',
  executives: '高管',
  announcements: '公告',
  news: '新闻',
}

function PartBadges({ state }: { state: CompanyDetailData['state'] }) {
  const parts = state.parts
  if (!parts) return null
  return (
    <span className="flex flex-wrap gap-1">
      {Object.entries(parts).map(([name, st]) => (
        <span
          key={name}
          title={st.error ?? `${PART_LABELS[name] ?? name} ${st.rows != null ? `(${st.rows})` : ''}`}
          className={cn(
            'rounded-btn px-1.5 py-0.5 text-[10px]',
            st.ok ? 'bg-success/10 text-success' : 'bg-danger/10 text-danger',
          )}
        >
          {PART_LABELS[name] ?? name}
        </span>
      ))}
    </span>
  )
}

function SectionTitle({ icon, title, extra }: { icon: React.ReactNode; title: string; extra?: React.ReactNode }) {
  return (
    <div className="mb-2 flex items-center justify-between">
      <h3 className="flex items-center gap-1.5 text-sm font-medium text-foreground">
        {icon}
        {title}
      </h3>
      {extra}
    </div>
  )
}

/** 持股变动标记: 数值 → 红涨绿跌; "不变"等文本 → 灰字。 */
function ChangeMark({ holder }: { holder: Top10Holder }) {
  if (holder.change_shares != null) {
    return (
      <span className={priceColorClass(holder.change_shares)}>
        {holder.change_shares > 0 ? '增持 ' : '减持 '}
        {fmtBigNum(Math.abs(holder.change_shares))}
      </span>
    )
  }
  if (holder.change_note) {
    return <span className="text-muted">{holder.change_note}</span>
  }
  return <span className="text-muted">—</span>
}

function Placeholder({ text }: { text: string }) {
  return (
    <div className="grid h-24 place-items-center rounded-card border border-dashed border-border text-xs text-muted">
      {text}
    </div>
  )
}

function ProfileSection({ detail }: { detail: CompanyDetailData }) {
  const p = detail.profile
  const rows: Array<[string, string | null]> = [
    ['上市日期', p.listed_date],
    ['成立日期', p.founded_date],
    ['行业', [p.industry_l1, p.industry_l2].filter(Boolean).join(' / ') || null],
    ['市场', p.market],
    ['官网', p.website],
    ['ISIN', p.isin],
  ]
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <div className="rounded-card border border-border bg-elevated p-3">
        <SectionTitle icon={<Building2 className="h-4 w-4 text-accent" />} title="公司档案" />
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
          {rows
            .filter(([, v]) => v)
            .map(([k, v]) => (
              <div key={k} className="col-span-2 grid grid-cols-[auto_1fr] gap-x-3">
                <dt className="text-muted">{k}</dt>
                <dd className="text-foreground">{v}</dd>
              </div>
            ))}
        </dl>
      </div>
      <div className="rounded-card border border-border bg-elevated p-3">
        <SectionTitle
          icon={<Landmark className="h-4 w-4 text-accent" />}
          title="业务概况"
          extra={p.declare_date ? <span className="text-[10px] text-muted">披露 {p.declare_date}</span> : null}
        />
        <div className="space-y-2 text-xs leading-relaxed">
          {p.business_summary && <p className="whitespace-pre-line text-foreground">{p.business_summary}</p>}
          {p.highlights && (
            <p className="whitespace-pre-line">
              <span className="text-success">亮点：</span>
              <span className="text-secondary">{p.highlights}</span>
            </p>
          )}
          {p.risks && (
            <p className="whitespace-pre-line">
              <span className="text-danger">风险：</span>
              <span className="text-secondary">{p.risks}</span>
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

function ExecutiveCard({ person }: { person: ExecutiveDetail }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-card border border-border bg-elevated p-2.5">
      <button type="button" onClick={() => setOpen(v => !v)} className="w-full text-left">
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-sm font-medium text-foreground">{person.name}</span>
          <span className="text-[10px] text-muted">{person.incumbent_time ?? ''}</span>
        </div>
        <div className="mt-0.5 text-xs text-secondary">{person.position ?? ''}</div>
        <div className="mt-1 flex flex-wrap gap-2 text-[10px] text-muted">
          {person.salary_yuan != null && <span>年薪 {fmtBigNum(person.salary_yuan)}元</span>}
          {person.age && <span>{person.age}岁</span>}
          {person.degree && <span>{person.degree}</span>}
          {person.hold_num != null && person.hold_num > 0 && <span>持股 {fmtBigNum(person.hold_num)}股</span>}
        </div>
      </button>
      {open && person.resume && (
        <p className="mt-2 border-t border-border pt-2 text-[11px] leading-relaxed whitespace-pre-line text-secondary">
          {person.resume}
        </p>
      )}
    </div>
  )
}

function PeopleSection({ detail }: { detail: CompanyDetailData }) {
  const research = detail.holders_top10
  const top5 = detail.shareholders
  const holderCount = research?.holder_count_history?.[0]
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <div>
        <SectionTitle
          icon={<Users className="h-4 w-4 text-accent" />}
          title="董监高"
          extra={detail.executives?.report_date ? <span className="text-[10px] text-muted">报告期 {detail.executives.report_date}</span> : null}
        />
        {detail.executives?.persons?.length ? (
          <div className="grid gap-2 sm:grid-cols-2">
            {detail.executives.persons.map(p => (
              <ExecutiveCard key={p.person_code ?? p.name} person={p} />
            ))}
          </div>
        ) : (
          <Placeholder text="暂无高管详情 · 同步成功后展示" />
        )}
      </div>
      <div>
        <SectionTitle
          icon={<Landmark className="h-4 w-4 text-accent" />}
          title="十大股东"
          extra={research?.report_date ? <span className="text-[10px] text-muted">报告期 {research.report_date}</span> : null}
        />
        {research?.top10?.length ? (
          <div className="overflow-hidden rounded-card border border-border">
            <table className="w-full text-xs">
              <thead className="bg-elevated text-muted">
                <tr>
                  <th className="px-2 py-1.5 text-left font-normal">#</th>
                  <th className="px-2 py-1.5 text-left font-normal">股东</th>
                  <th className="px-2 py-1.5 text-right font-normal">持股</th>
                  <th className="px-2 py-1.5 text-right font-normal">比例</th>
                  <th className="px-2 py-1.5 text-right font-normal">变动</th>
                </tr>
              </thead>
              <tbody>
                {research.top10.map(h => (
                  <tr key={h.rank ?? h.name} className="border-t border-border">
                    <td className="px-2 py-1.5 text-muted">{h.rank}</td>
                    <td className="px-2 py-1.5 text-foreground">
                      {h.name}
                      {h.shares_type && h.shares_type !== 'A股' && (
                        <span className="ml-1 text-[10px] text-muted">{h.shares_type}</span>
                      )}
                    </td>
                    <td className="px-2 py-1.5 text-right text-secondary">{fmtBigNum(h.shares)}</td>
                    <td className="px-2 py-1.5 text-right text-foreground">
                      {h.pct_percent != null ? `${h.pct_percent.toFixed(2)}%` : '—'}
                    </td>
                    <td className="px-2 py-1.5 text-right text-[10px]">
                      <ChangeMark holder={h} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Placeholder text="暂无十大股东 · 同步成功后展示" />
        )}
        {/* 前五大对照 / 户数 / 实控人 */}
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[10px] text-muted">
          {top5?.report_date && <span>巨潮前五大 · {top5.report_date}</span>}
          {research?.controller?.name && (
            <span className="rounded-btn bg-elevated px-1.5 py-0.5">实控人 {research.controller.name}</span>
          )}
          {holderCount?.holder_total_num != null && (
            <span className="rounded-btn bg-elevated px-1.5 py-0.5">
              股东 {fmtBigNum(holderCount.holder_total_num)} 户
              {holderCount.hold_focus ? ` · ${holderCount.hold_focus}` : ''}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

interface TimelineEntry {
  key: string
  date: string
  time: string | null
  title: string
  kind: '公告' | '新闻'
  url: string | null
  source: string | null
  summary: string | null
}

function buildTimeline(announcements: Announcement[], news: NewsItem[]): TimelineEntry[] {
  const entries: TimelineEntry[] = []
  for (const a of announcements) {
    if (!a.date) continue
    entries.push({
      key: `ann:${a.announcement_id ?? a.title ?? a.date}`,
      date: a.date,
      time: null,
      title: a.title ?? '(无标题公告)',
      kind: '公告',
      url: a.pdf_url,
      source: null,
      summary: null,
    })
  }
  for (const n of news) {
    if (!n.date) continue
    entries.push({
      key: `news:${n.id}`,
      date: n.date.slice(0, 10),
      time: n.date.slice(11, 16),
      title: n.title,
      kind: '新闻',
      url: n.url,
      source: n.source,
      summary: n.summary,
    })
  }
  entries.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0))
  return entries
}

function TimelineSection({ detail }: { detail: CompanyDetailData }) {
  const timeline = useMemo(
    () => buildTimeline(detail.announcements, detail.news),
    [detail.announcements, detail.news],
  )
  const [expanded, setExpanded] = useState<string | null>(null)
  const groups = useMemo(() => {
    const map = new Map<string, TimelineEntry[]>()
    for (const e of timeline) {
      const list = map.get(e.date) ?? []
      list.push(e)
      map.set(e.date, list)
    }
    return [...map.entries()]
  }, [timeline])

  return (
    <div>
      <SectionTitle
        icon={<Newspaper className="h-4 w-4 text-accent" />}
        title="消息时间线"
        extra={<span className="text-[10px] text-muted">{timeline.length} 条 · 公告 {detail.announcements.length} / 新闻 {detail.news.length}</span>}
      />
      {groups.length === 0 ? (
        <Placeholder text="暂无消息 · 同步成功后展示公告与新闻" />
      ) : (
        <div className="space-y-3">
          {groups.map(([date, entries]) => (
            <div key={date}>
              <div className="mb-1 text-[10px] text-muted">{date}</div>
              <div className="space-y-1.5">
                {entries.map(e => (
                  <div key={e.key} className="rounded-card border border-border bg-elevated px-2.5 py-1.5">
                    <div className="flex items-start gap-2">
                      {e.kind === '公告' ? (
                        <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" />
                      ) : (
                        <Newspaper className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
                      )}
                      <div className="min-w-0 flex-1">
                        <div className="flex items-baseline gap-2">
                          {e.time && <span className="shrink-0 text-[10px] text-muted">{e.time}</span>}
                          {e.source && (
                            <span className="shrink-0 rounded-btn bg-elevated px-1 py-0.5 text-[10px] text-muted">
                              {e.source}
                            </span>
                          )}
                          {e.url ? (
                            <a
                              href={e.url}
                              target="_blank"
                              rel="noreferrer"
                              className="line-clamp-1 text-xs text-foreground hover:text-accent"
                            >
                              {e.title}
                              <ExternalLink className="ml-0.5 inline h-3 w-3 text-muted" />
                            </a>
                          ) : (
                            <span className="line-clamp-1 text-xs text-foreground">{e.title}</span>
                          )}
                        </div>
                        {e.summary && (
                          <button
                            type="button"
                            onClick={() => setExpanded(v => (v === e.key ? null : e.key))}
                            className="mt-0.5 flex items-center gap-1 text-[10px] text-muted hover:text-secondary"
                          >
                            {expanded === e.key ? '收起摘要' : '摘要'}
                            <ChevronDown className={cn('h-3 w-3 transition-transform', expanded === e.key && 'rotate-180')} />
                          </button>
                        )}
                        {expanded === e.key && e.summary && (
                          <p className="mt-1 border-t border-border pt-1 text-[11px] leading-relaxed text-secondary">
                            {e.summary}
                          </p>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function CompanyDetail({ symbol, compact = false }: { symbol: string; compact?: boolean }) {
  const queryClient = useQueryClient()
  const detailQ = useQuery({
    queryKey: ['company-graph', 'detail', symbol],
    queryFn: () => graphApi.detail(symbol),
    staleTime: 60_000,
    retry: false,
  })
  const syncMut = useMutation({
    mutationFn: () => graphApi.sync(symbol),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['company-graph', 'detail', symbol] })
      void queryClient.invalidateQueries({ queryKey: ['company-graph', 'overview'] })
    },
    onError: e => toast(String((e as Error)?.message || '同步失败'), 'error'),
  })

  if (detailQ.isLoading) {
    return <div className="p-4 text-xs text-muted">加载中…</div>
  }
  if (detailQ.error) {
    const notSynced = (detailQ.error as { status?: number }).status === 404
    return (
      <div className="grid h-48 place-items-center">
        <div className="text-center">
          <div className="text-sm text-foreground">{notSynced ? `${symbol} 尚未同步` : '加载失败'}</div>
          <p className="mt-1 max-w-md text-xs text-muted">
            {notSynced ? '点击同步, 从巨潮/东财/扶摇拉取公司数据' : String(detailQ.error.message ?? detailQ.error)}
          </p>
          <button
            type="button"
            onClick={() => syncMut.mutate()}
            disabled={syncMut.isPending}
            className="mt-3 inline-flex items-center gap-1.5 rounded-btn bg-accent px-3 py-1.5 text-xs text-white hover:bg-accent/90 disabled:opacity-50"
          >
            <RefreshCw className={cn('h-3.5 w-3.5', syncMut.isPending && 'animate-spin')} />
            {syncMut.isPending ? '同步中…' : '同步公司数据'}
          </button>
        </div>
      </div>
    )
  }
  const detail = detailQ.data
  if (!detail) return null
  const shortName = detail.profile.short_name ?? detail.profile.name ?? symbol

  return (
    <div className={cn('space-y-4', compact && 'space-y-3')}>
      {/* 头部: 名称 + 状态徽标 + 同步 */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-base font-semibold text-foreground">{shortName}</span>
          <span className="text-xs text-muted">{symbol}</span>
          <PartBadges state={detail.state} />
        </div>
        <button
          type="button"
          onClick={() => syncMut.mutate()}
          disabled={syncMut.isPending}
          className="inline-flex items-center gap-1 rounded-btn border border-border px-2 py-1 text-xs text-secondary hover:text-foreground disabled:opacity-50"
        >
          <RefreshCw className={cn('h-3.5 w-3.5', syncMut.isPending && 'animate-spin')} />
          同步
        </button>
      </div>

      <ProfileSection detail={detail} />
      <PeopleSection detail={detail} />
      <TimelineSection detail={detail} />
    </div>
  )
}
