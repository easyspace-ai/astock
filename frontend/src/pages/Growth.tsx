/**
 * 三年五倍 — 扫描历史滚动窗口内「谷底最低价 → 之后最高价」涨幅 ≥5 倍的 A 股。
 *
 * 口径：前复权日 K；窗口 ≤756 个交易日；命中标的按倍数/涨幅/峰值日排序分页，
 * 右侧 K 线图复现谷底→峰值区间用于人工验证。数据来自后端已落盘结果 CSV，
 * 「刷新」在后台重新全市场扫描（运行中不可重复触发）。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { TrendingUp, RefreshCw, Search, Rocket } from 'lucide-react'
import { api, type StockGrowthRow } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { toast } from '@/components/Toast'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState } from '@/components/EmptyState'
import { GrowthChart } from '@/components/growth/GrowthChart'
import { toOHLC } from '@/components/StockDailyKChart'
import { cn } from '@/lib/cn'

type SortKey = 'multiple' | 'gain_pct' | 'peak_date'

const PAGE_SIZE = 50
const YEAR_OPTIONS = [3, 5, 10]

function fmtPrice(v: number | null | undefined): string {
  return v == null || !Number.isFinite(Number(v)) ? '—' : Number(v).toFixed(2)
}

export function Growth() {
  const queryClient = useQueryClient()
  const [years, setYears] = useState(10)
  const [keyword, setKeyword] = useState('')
  const [sort, setSort] = useState<SortKey>('multiple')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<StockGrowthRow | null>(null)

  const statusQuery = useQuery({
    queryKey: QK.growthStatus(),
    queryFn: api.stockGrowthStatus,
    refetchInterval: (q) => (q.state.data?.running ? 2000 : false),
  })

  const listQuery = useQuery({
    queryKey: QK.growthList(years, keyword, sort, page, PAGE_SIZE),
    queryFn: () => api.stockGrowthList({ years, keyword, sort, page, pageSize: PAGE_SIZE }),
  })

  const rows = listQuery.data?.rows ?? []
  const total = listQuery.data?.total ?? 0
  const status = statusQuery.data
  const running = !!status?.running
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  // 翻页 / 筛选变化后回到首页；关键词输入防抖由受控值驱动，回车或失焦生效。
  useEffect(() => {
    setPage(1)
  }, [years, sort])

  // 当前页若没有选中行（首屏或翻页后），默认选中第一行供右侧验证。
  useEffect(() => {
    if (!selected && rows.length > 0) setSelected(rows[0])
    if (selected && !rows.some(r => r.symbol === selected.symbol && r.peak_date === selected.peak_date)) {
      setSelected(rows[0] ?? null)
    }
  }, [rows, selected])

  // 扫描由运行转为完成后，重新拉取一次最终榜单。
  const prevRunning = useRef(false)
  useEffect(() => {
    if (prevRunning.current && !running) {
      void queryClient.invalidateQueries({ queryKey: ['stock-growth'] })
    }
    prevRunning.current = running
  }, [running, queryClient])

  const refreshMutation = useMutation({
    mutationFn: () => api.stockGrowthRefresh(years),
    onSuccess: () => {
      toast('已开始后台扫描，完成后榜单自动刷新', 'success')
      void queryClient.invalidateQueries({ queryKey: QK.growthStatus() })
    },
    onError: (e: Error) => toast(e.message || '触发扫描失败'),
  })

  const klineQuery = useQuery({
    queryKey: selected ? QK.growthKline(selected.symbol, selected.trough_date, selected.peak_date) : ['stock-growth', 'kline'],
    queryFn: () => api.stockGrowthKline(selected!.symbol, selected!.trough_date, selected!.peak_date),
    enabled: !!selected,
  })

  const ohlc = useMemo(() => toOHLC(klineQuery.data?.rows ?? []), [klineQuery.data?.rows])

  const headerCell = (key: SortKey, label: string) => (
    <th
      key={key}
      onClick={() => setSort(key)}
      className={cn('cursor-pointer select-none px-3 py-2.5 font-medium transition-colors hover:text-foreground',
        sort === key ? 'text-accent' : 'text-secondary')}
    >
      {label}{sort === key ? ' ▾' : ''}
    </th>
  )

  return (
    <div className="flex min-h-full flex-col bg-page xl:h-full">
      <PageHeader
        title="三年五倍"
        subtitle="滚动 756 个交易日内，谷底最低价到之后最高价涨幅 ≥5 倍的 A 股（前复权口径）"
        className="shrink-0 flex-wrap gap-x-4 gap-y-2 bg-page/95 px-3 lg:flex-nowrap lg:px-5"
        right={(
          <div className="flex w-full min-w-0 flex-wrap items-center gap-1.5 sm:gap-2 lg:w-auto lg:flex-nowrap">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted" />
              <input
                value={keyword}
                onChange={e => { setKeyword(e.target.value); setPage(1) }}
                placeholder="代码 / 名称"
                className="h-8 w-32 rounded-btn border border-border bg-surface pl-7 pr-2 text-xs text-foreground placeholder:text-muted focus:border-accent/50 focus:outline-none sm:w-36"
              />
            </div>
            <select
              value={years}
              onChange={e => setYears(Number(e.target.value))}
              className="h-8 rounded-btn border border-border bg-surface px-2 text-xs text-secondary focus:border-accent/50 focus:outline-none"
              aria-label="回看年份"
            >
              {YEAR_OPTIONS.map(y => <option key={y} value={y}>近 {y} 年</option>)}
            </select>
            <button
              type="button"
              onClick={() => refreshMutation.mutate()}
              disabled={running || refreshMutation.isPending}
              className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-btn border border-border bg-surface px-2.5 text-[11px] font-medium text-secondary transition-colors hover:border-accent/40 hover:text-accent disabled:cursor-not-allowed disabled:opacity-45 sm:text-xs"
            >
              <RefreshCw className={cn('h-3.5 w-3.5', running && 'animate-spin')} />
              <span>{running ? '扫描中…' : '重新扫描'}</span>
            </button>
          </div>
        )}
      />

      <main className="min-h-0 flex-1 px-3 pb-3 pt-3 lg:px-4 lg:pb-4">
        {running && status?.message && (
          <div className="mb-2 flex items-center gap-2 rounded-btn border border-accent/20 bg-accent/5 px-3 py-1.5 text-xs text-secondary">
            <RefreshCw className="h-3.5 w-3.5 animate-spin text-accent" />
            <span>{status.message}{typeof status.pct === 'number' && status.pct > 0 ? `（${status.pct}%）` : ''}</span>
          </div>
        )}

        {listQuery.isLoading && !listQuery.data ? (
          <EmptyState icon={TrendingUp} title="正在加载榜单…" />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={Rocket}
            title={status?.last_count === 0 ? '本次扫描暂无达标标的' : '暂无扫描结果'}
            hint="点击右上角「重新扫描」，按所选年份回看全市场历史行情；结果落盘后可在此分页检索。"
          />
        ) : (
          <div className="flex h-full min-h-0 flex-col gap-3 lg:flex-row">
            {/* 榜单 */}
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-btn border border-border bg-surface">
              <div className="min-h-0 flex-1 overflow-auto">
                <table className="w-full min-w-[720px] text-sm">
                  <thead className="sticky top-0 bg-elevated">
                    <tr className="text-left text-xs">
                      <th className="px-3 py-2.5 font-medium text-secondary">代码 / 名称</th>
                      <th className="px-3 py-2.5 font-medium text-secondary">谷底日</th>
                      <th className="px-3 py-2.5 font-medium text-secondary">峰值日</th>
                      {headerCell('multiple', '倍数')}
                      {headerCell('gain_pct', '涨幅')}
                      <th className="px-3 py-2.5 font-medium text-secondary">谷底价</th>
                      <th className="px-3 py-2.5 font-medium text-secondary">峰值价</th>
                      <th className="px-3 py-2.5 font-medium text-secondary">区间交易日</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(row => {
                      const active = selected?.symbol === row.symbol && selected?.peak_date === row.peak_date
                      return (
                        <tr
                          key={`${row.symbol}-${row.peak_date}`}
                          onClick={() => setSelected(row)}
                          className={cn('cursor-pointer border-t border-border transition-colors',
                            active ? 'bg-accent/10' : 'hover:bg-elevated/60')}
                        >
                          <td className="px-3 py-2.5">
                            <div className="font-medium text-foreground">{row.symbol}</div>
                            <div className="text-xs text-muted">{row.name || '—'}</div>
                          </td>
                          <td className="px-3 py-2.5 text-secondary">{row.trough_date}</td>
                          <td className="px-3 py-2.5 text-secondary">{row.peak_date}</td>
                          <td className="px-3 py-2.5 font-semibold text-accent">{row.multiple.toFixed(2)}×</td>
                          <td className="px-3 py-2.5 text-foreground">+{row.gain_pct.toFixed(1)}%</td>
                          <td className="px-3 py-2.5 text-secondary">{fmtPrice(row.trough_price)}</td>
                          <td className="px-3 py-2.5 text-secondary">{fmtPrice(row.peak_price)}</td>
                          <td className="px-3 py-2.5 text-secondary">{row.bars}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-4 py-2 text-xs text-muted">
                <span>共 {total} 只 · 第 {page}/{totalPages} 页</span>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setPage(p => Math.max(1, p - 1))}
                    disabled={page <= 1}
                    className="rounded-btn border border-border bg-surface px-2.5 py-1 text-xs text-secondary transition-colors hover:border-accent/40 hover:text-accent disabled:cursor-not-allowed disabled:opacity-45"
                  >
                    上一页
                  </button>
                  <button
                    type="button"
                    onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                    disabled={page >= totalPages}
                    className="rounded-btn border border-border bg-surface px-2.5 py-1 text-xs text-secondary transition-colors hover:border-accent/40 hover:text-accent disabled:cursor-not-allowed disabled:opacity-45"
                  >
                    下一页
                  </button>
                </div>
              </div>
            </div>

            {/* 验证 K 线 */}
            <div className="flex min-h-[320px] w-full flex-col overflow-hidden rounded-btn border border-border bg-surface lg:w-[46%] xl:w-[50%]">
              {selected ? (
                <>
                  <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-b border-border px-4 py-2.5">
                    <div>
                      <div className="text-sm font-medium text-foreground">{selected.name || selected.symbol}</div>
                      <div className="text-xs text-muted">{selected.symbol}</div>
                    </div>
                    <div className="flex items-center gap-3 text-xs text-secondary">
                      <span>谷底 {selected.trough_date} · {fmtPrice(selected.trough_price)}</span>
                      <span className="text-muted">→</span>
                      <span>峰值 {selected.peak_date} · {fmtPrice(selected.peak_price)}</span>
                      <span className="font-semibold text-accent">{selected.multiple.toFixed(2)}×</span>
                    </div>
                  </div>
                  <div className="min-h-0 flex-1 p-2">
                    {klineQuery.isLoading ? (
                      <div className="grid h-full place-items-center text-xs text-muted">正在加载 K 线…</div>
                    ) : ohlc.length === 0 ? (
                      <EmptyState icon={TrendingUp} title="该区间暂无 K 线数据" />
                    ) : (
                      <GrowthChart
                        data={ohlc}
                        height={420}
                        trough={selected ? { date: selected.trough_date, price: selected.trough_price } : null}
                        peak={selected ? { date: selected.peak_date, price: selected.peak_price } : null}
                      />
                    )}
                  </div>
                </>
              ) : (
                <EmptyState icon={TrendingUp} title="从左侧榜单选择一只标的查看验证 K 线" />
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  )
}
