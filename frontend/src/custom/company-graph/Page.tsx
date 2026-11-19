/** 公司图谱 — 重点公司清单管理 + 三层详情 (公司/人/消息)。 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleAlert, Network, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState } from '@/components/EmptyState'
import { toast } from '@/components/Toast'
import { cn } from '@/lib/cn'
import { graphApi, type Overview, type PartMap } from './graphClient'
import { CompanyDetail } from './CompanyDetail'

const SYMBOL_RE = /^\d{6}\.(SH|SZ|BJ)$/

function CompanyRow({
  symbol,
  state,
  stored,
  active,
  onSelect,
  onSync,
  onRemove,
  syncing,
}: {
  symbol: string
  state?: Overview['state'][string]
  stored: boolean
  active: boolean
  onSelect: () => void
  onSync: () => void
  onRemove: () => void
  syncing: boolean
}) {
  const parts: PartMap | undefined = state?.parts
  const failed = Object.entries(parts ?? {}).filter(([, v]) => !v.ok)
  return (
    <div
      className={cn(
        'group rounded-card border px-3 py-2 transition-colors',
        active ? 'border-accent/40 bg-accent/5' : 'border-border hover:border-accent/25',
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <button type="button" onClick={onSelect} className="min-w-0 flex-1 text-left">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium text-foreground">{symbol}</span>
            {!stored && <span className="shrink-0 text-[10px] text-muted">未同步</span>}
            {failed.length > 0 && (
              <CircleAlert className="h-3.5 w-3.5 shrink-0 text-warning" aria-label="部分数据块失败" />
            )}
          </div>
          <div className="mt-0.5 truncate text-[10px] text-muted">
            {state?.last_sync ? `最近同步 ${state.last_sync.replace('T', ' ').slice(0, 16)}` : '从未同步'}
            {failed.length > 0 && ` · 失败块: ${failed.map(([k]) => k).join('/')}`}
          </div>
        </button>
        <div className="flex shrink-0 items-center gap-1 opacity-60 transition-opacity group-hover:opacity-100">
          <button
            type="button"
            onClick={onSync}
            disabled={syncing}
            title="同步"
            className="rounded-btn p-1.5 text-secondary hover:bg-elevated hover:text-foreground disabled:opacity-50"
          >
            <RefreshCw className={cn('h-3.5 w-3.5', syncing && 'animate-spin')} />
          </button>
          <button
            type="button"
            onClick={onRemove}
            title="移出清单"
            className="rounded-btn p-1.5 text-secondary hover:bg-elevated hover:text-danger"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </div>
  )
}

export function CompanyGraphPage() {
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<string | null>(null)
  const [input, setInput] = useState('')
  const [inputError, setInputError] = useState<string | null>(null)

  const overviewQ = useQuery({
    queryKey: ['company-graph', 'overview'],
    queryFn: graphApi.overview,
    staleTime: 30_000,
  })

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['company-graph'] })
  }

  const setTrackedMut = useMutation({
    mutationFn: graphApi.setTracked,
    onSuccess: invalidate,
    onError: e => setInputError(String((e as Error).message)),
  })
  const syncMut = useMutation({
    mutationFn: (symbol: string) => graphApi.sync(symbol),
    onSuccess: invalidate,
    onError: e => toast(String((e as Error)?.message || '同步失败'), 'error'),
  })

  const overview: Overview | undefined = overviewQ.data

  const addSymbol = () => {
    const value = input.trim().toUpperCase()
    if (!value) return
    if (!SYMBOL_RE.test(value)) {
      setInputError('代码格式需形如 000019.SZ / 600519.SH / 830001.BJ')
      return
    }
    const tracked = overview?.tracked ?? []
    if (tracked.includes(value)) {
      setInputError('已在清单中')
      return
    }
    setInputError(null)
    setInput('')
    setTrackedMut.mutate([...tracked, value])
  }
  const removeSymbol = (symbol: string) => {
    const tracked = (overview?.tracked ?? []).filter(s => s !== symbol)
    setTrackedMut.mutate(tracked)
    if (selected === symbol) setSelected(tracked[0] ?? null)
  }
  const syncSymbol = (symbol: string) => syncMut.mutate(symbol)

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="公司图谱"
        subtitle="重点公司的档案、股东、高管与消息 — 数据源: 巨潮资讯 / 东方财富 / 同花顺资讯"
      />
      <div className="flex min-h-0 flex-1 gap-4 overflow-hidden p-4">
        {/* 左栏: 清单 */}
        <aside className="flex w-72 shrink-0 flex-col gap-3 overflow-y-auto">
          <div>
            <div className="flex gap-1.5">
              <input
                value={input}
                onChange={e => {
                  setInput(e.target.value)
                  setInputError(null)
                }}
                onKeyDown={e => e.key === 'Enter' && addSymbol()}
                placeholder="输入代码, 如 000019.SZ"
                className="h-8 w-full rounded-btn border border-border bg-surface px-2.5 text-xs text-foreground outline-none placeholder:text-muted focus:border-accent/40"
              />
              <button
                type="button"
                onClick={addSymbol}
                disabled={setTrackedMut.isPending}
                title="加入重点清单"
                className="shrink-0 rounded-btn border border-border p-1.5 text-secondary hover:border-accent/40 hover:text-accent disabled:opacity-50"
              >
                <Plus className="h-4 w-4" />
              </button>
            </div>
            {inputError && <p className="mt-1 text-[10px] text-danger">{inputError}</p>}
          </div>
          {overviewQ.isLoading ? (
            <div className="p-4 text-xs text-muted">加载中…</div>
          ) : overview?.tracked.length ? (
            <div className="space-y-2">
              {overview.tracked.map(symbol => (
                <CompanyRow
                  key={symbol}
                  symbol={symbol}
                  state={overview.state[symbol]}
                  stored={overview.stored.includes(symbol)}
                  active={selected === symbol}
                  onSelect={() => setSelected(symbol)}
                  onSync={() => syncSymbol(symbol)}
                  onRemove={() => removeSymbol(symbol)}
                  syncing={syncMut.isPending && syncMut.variables === symbol}
                />
              ))}
            </div>
          ) : (
            <EmptyState
              icon={Network}
              title="重点公司清单为空"
              hint="输入代码添加 20~30 家重点公司, 逐家同步后即可查看三层图谱"
            />
          )}
        </aside>

        {/* 右栏: 详情 */}
        <main className="min-w-0 flex-1 overflow-y-auto">
          {selected ? (
            <CompanyDetail symbol={selected} />
          ) : (
            <EmptyState
              icon={Network}
              title="选择左侧公司"
              hint="或在上方输入代码添加重点公司"
            />
          )}
        </main>
      </div>
    </div>
  )
}
