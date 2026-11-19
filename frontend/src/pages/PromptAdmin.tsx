import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Save, MessageSquareText } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { toast } from '@/components/Toast'
import { cn } from '@/lib/cn'
import {
  describeAistockClientError,
  listAgentPrompts,
  updateAgentPrompt,
  type AgentPromptEntry,
} from '@/lib/aistockApi'

const PROMPTS_QK = ['aistock', 'agent-prompts'] as const

export function PromptAdmin() {
  const qc = useQueryClient()
  const { data, isLoading, error } = useQuery({
    queryKey: PROMPTS_QK,
    queryFn: listAgentPrompts,
  })

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [dirty, setDirty] = useState(false)

  const agents = data ?? []
  const selected = useMemo(
    () => agents.find((a) => a.id === selectedId) ?? agents[0] ?? null,
    [agents, selectedId],
  )

  useEffect(() => {
    if (!selected) return
    if (!selectedId) setSelectedId(selected.id)
    if (!dirty) setDraft(selected.prompt)
  }, [selected, selectedId, dirty])

  const saveMut = useMutation({
    mutationFn: async () => {
      if (!selected) throw new Error('未选择 Agent')
      return updateAgentPrompt(selected.id, draft)
    },
    onSuccess: (entry) => {
      qc.setQueryData<AgentPromptEntry[]>(PROMPTS_QK, (prev) =>
        (prev ?? []).map((a) => (a.id === entry.id ? entry : a)),
      )
      setDraft(entry.prompt)
      setDirty(false)
      toast('提示词已保存', 'success')
    },
    onError: (err) => {
      toast(describeAistockClientError(err), 'error')
    },
  })

  const selectAgent = (id: string) => {
    if (dirty && id !== selected?.id) {
      const ok = window.confirm('当前提示词尚未保存，切换后将丢失修改。确定切换？')
      if (!ok) return
    }
    setSelectedId(id)
    setDirty(false)
    const next = agents.find((a) => a.id === id)
    if (next) setDraft(next.prompt)
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        title="提示词管理"
        subtitle="编辑多 Agent 分析的系统提示词，保存后立即生效"
        right={
          <button
            type="button"
            disabled={!selected || !dirty || saveMut.isPending}
            onClick={() => saveMut.mutate()}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-btn px-3 py-1.5 text-sm font-medium transition-colors',
              dirty && selected
                ? 'bg-accent text-white hover:bg-accent/90'
                : 'bg-elevated text-muted cursor-not-allowed',
            )}
          >
            {saveMut.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Save className="h-3.5 w-3.5" />
            )}
            保存
          </button>
        }
      />

      <div className="flex min-h-0 flex-1 gap-3 p-4">
        <aside className="flex w-56 shrink-0 flex-col overflow-hidden rounded-card border border-border bg-surface">
          <div className="border-b border-border px-3 py-2 text-[11px] font-medium uppercase tracking-wide text-muted">
            Agent 列表
          </div>
          <div className="flex-1 overflow-y-auto p-1.5">
            {isLoading && (
              <div className="flex items-center justify-center py-10 text-muted">
                <Loader2 className="h-4 w-4 animate-spin" />
              </div>
            )}
            {error && (
              <div className="px-2 py-4 text-xs text-bear">
                {describeAistockClientError(error)}
              </div>
            )}
            {agents.map((agent) => {
              const active = agent.id === selected?.id
              return (
                <button
                  key={agent.id}
                  type="button"
                  onClick={() => selectAgent(agent.id)}
                  className={cn(
                    'mb-0.5 flex w-full items-center gap-2 rounded-btn px-2.5 py-2 text-left text-sm transition-colors',
                    active
                      ? 'bg-elevated text-foreground font-medium'
                      : 'text-foreground/80 hover:bg-elevated/60 hover:text-foreground',
                  )}
                >
                  <MessageSquareText className="h-3.5 w-3.5 shrink-0 text-muted" />
                  <span className="min-w-0 flex-1 truncate">{agent.label}</span>
                  {agent.overridden && (
                    <span className="shrink-0 rounded bg-accent/15 px-1 py-px text-[9px] font-semibold text-accent">
                      已改
                    </span>
                  )}
                </button>
              )
            })}
          </div>
        </aside>

        <section className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-card border border-border bg-surface">
          {selected ? (
            <>
              <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-2.5">
                <div className="min-w-0">
                  <div className="text-sm font-medium text-foreground">{selected.label}</div>
                  <div className="truncate text-[11px] text-muted">
                    {selected.name}
                    <span className="mx-1.5 text-border">·</span>
                    <span className="font-mono">{selected.id}</span>
                  </div>
                </div>
                {dirty && (
                  <span className="shrink-0 text-[11px] text-warning">未保存</span>
                )}
              </div>
              <textarea
                value={draft}
                onChange={(e) => {
                  setDraft(e.target.value)
                  setDirty(e.target.value !== selected.prompt)
                }}
                spellCheck={false}
                className="min-h-0 flex-1 resize-none bg-base/40 px-4 py-3 font-mono text-[12px] leading-relaxed text-foreground outline-none placeholder:text-muted"
                placeholder="系统提示词…"
              />
            </>
          ) : (
            !isLoading && (
              <div className="flex flex-1 items-center justify-center text-sm text-muted">
                暂无 Agent 提示词
              </div>
            )
          )}
        </section>
      </div>
    </div>
  )
}
