/**
 * AI 助手页面 — 左侧对话历史, 右侧对话区。
 *
 * 由原悬浮球 + 右滑抽屉改造为侧边栏菜单内的独立路由页: 历史会话常驻左栏,
 * 右栏沿用抽屉的消息流 / 空态建议 / 输入区编排; 会话与发送逻辑仍在 store。
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useIsAdmin } from '@/lib/useAccount'
import { AnimatePresence, motion } from 'framer-motion'
import {
  ArrowDown,
  Bot,
  Database,
  FlaskConical,
  Gift,
  History,
  Plus,
  Puzzle,
  SendHorizontal,
  Settings2,
  SlidersHorizontal,
  Sparkles,
  Square,
  Star,
  Trash2,
  TrendingUp,
  X,
} from 'lucide-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { cn } from '@/lib/cn'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { usePreferences } from '@/lib/useSharedQueries'
import { DEEPSEEK_FALLBACK_RANGE, discountRangeLabel, useSponsorModelItems } from '@/lib/sponsor-discount'
import {
  fetchAssistantStatus,
  fetchAssistantSuggests,
  type AssistantStatus,
  type QuickSuggest,
} from '../client'
import {
  deleteSession,
  newSession,
  retryLast,
  selectSession,
  sendMessage,
  stopSending,
  useAssistantStore,
} from '../store'
import { AssistantMessageView } from './messages'

const EASE_SMOOTH: [number, number, number, number] = [0.16, 1, 0.3, 1]

const RH_PROMO_URL = 'https://www.runninghub.ai/zh-cn/call-api/llm/models?source=github&inviteCode=edt5wh7c'
const RH_PROMO_DISMISS_KEY = 'assistant.rh_promo.dismissed_at.v1'
const RH_PROMO_SNOOZE_MS = 7 * 24 * 60 * 60 * 1000

type SessionMessages = ReturnType<typeof useAssistantStore>['sessions'][number]['messages']

export function AssistantPage() {
  const { sessions, activeId, sending } = useAssistantStore()
  const active = sessions.find(s => s.id === activeId) ?? null
  const messages = active?.messages ?? []
  const [status, setStatus] = useState<AssistantStatus | null>(null)
  const [suggests, setSuggests] = useState<QuickSuggest[]>([])

  useEffect(() => {
    let cancelled = false
    fetchAssistantStatus().then(s => { if (!cancelled) setStatus(s) }).catch(() => {})
    fetchAssistantSuggests().then(list => { if (!cancelled) setSuggests(list) }).catch(() => {})
    return () => { cancelled = true }
  }, [])

  return (
    <div className="flex h-full min-h-0">
      <HistoryPane sessions={sessions} activeId={activeId} sending={sending} />
      <section className="flex min-w-0 flex-1 flex-col">
        <PageHeader status={status} sending={sending} />
        <MessageList messages={messages} sending={sending} status={status} suggests={suggests} />
        <InputArea sending={sending} blocked={status ? !status.supports_tools : false} />
      </section>
    </div>
  )
}

/** 左栏: 历史会话列表 + 新对话入口。 */
function HistoryPane({
  sessions,
  activeId,
  sending,
}: {
  sessions: ReturnType<typeof useAssistantStore>['sessions']
  activeId: string
  sending: boolean
}) {
  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-border max-sm:w-44">
      <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border px-3">
        <History className="h-3.5 w-3.5 shrink-0 text-muted" />
        <span className="flex-1 truncate text-xs font-medium text-secondary">历史会话</span>
        <button
          type="button"
          title="新对话"
          aria-label="新对话"
          disabled={sending}
          onClick={newSession}
          className="flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-control text-secondary transition-colors duration-150 ease-smooth hover:bg-elevated hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
        >
          <Plus className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto p-1.5">
        {sessions.length === 0 && (
          <div className="px-2 py-4 text-center text-xs text-muted">暂无历史会话</div>
        )}
        {sessions.map(session => (
          <div
            key={session.id}
            className={cn(
              'group flex items-center gap-1 rounded-btn px-2 py-1.5 transition-colors duration-150 ease-smooth',
              session.id === activeId ? 'bg-elevated text-foreground' : 'text-secondary hover:bg-elevated/60 hover:text-foreground',
            )}
          >
            <button
              type="button"
              className="min-w-0 flex-1 cursor-pointer text-left"
              onClick={() => selectSession(session.id)}
              title={session.title}
            >
              <span className="block truncate text-xs">{session.title || '新对话'}</span>
              <span className="block text-[10px] leading-tight text-muted">
                {formatSessionTime(session.createdAt)}
              </span>
            </button>
            <button
              type="button"
              className="hidden cursor-pointer text-muted transition-colors duration-150 ease-smooth hover:text-danger group-hover:block"
              onClick={() => deleteSession(session.id)}
              title="删除会话"
              aria-label="删除会话"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
    </aside>
  )
}

function PageHeader({ status, sending }: { status: AssistantStatus | null; sending: boolean }) {
  const [settingsOpen, setSettingsOpen] = useState(false)

  return (
    <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border px-4">
      <Sparkles className="h-4 w-4 shrink-0 text-accent" />
      <span className="shrink-0 text-sm font-semibold text-foreground">AI 助手</span>
      {status?.model && (
        <span
          className="min-w-0 truncate rounded-btn bg-elevated px-1.5 py-0.5 font-mono text-[10px] text-muted"
          title={`模型: ${status.model}\n供应商: ${status.provider ?? '-'}`}
        >
          {status.model}
        </span>
      )}
      {/* <RhPromoBanner /> */}
      <div className="relative ml-auto shrink-0">
        <IconButton title="助手设置" onClick={() => setSettingsOpen(v => !v)} disabled={sending}>
          <SlidersHorizontal className="h-4 w-4" />
        </IconButton>
        {settingsOpen && <AssistantSettingsMenu onDone={() => setSettingsOpen(false)} />}
      </div>
    </div>
  )
}

/** RunningHub 推广条: 标题行中间单行跑马灯横向滚动(悬停暂停), 可点击跳转邀请链接。
 *  点 X 后一周(7 天)内不再显示 —— 时间戳持久化到 localStorage; 每次进入页面重新检查是否已到期。 */
export function RhPromoBanner() {
  const [dismissed, setDismissed] = useState(() => {
    try {
      const ts = Number(localStorage.getItem(RH_PROMO_DISMISS_KEY))
      return Number.isFinite(ts) && Date.now() - ts < RH_PROMO_SNOOZE_MS
    } catch { return false }
  })
  // 折扣区间来自实时模型定价 (与设置页共用 60s 缓存); 取不到时回退到集中的兜底文案
  const items = useSponsorModelItems()
  const range = (items && discountRangeLabel(items)) ?? DEEPSEEK_FALLBACK_RANGE
  if (dismissed) return null
  const snooze = () => {
    setDismissed(true)
    try { localStorage.setItem(RH_PROMO_DISMISS_KEY, String(Date.now())) } catch { /* 存储不可用时仅本次关闭 */ }
  }
  const copy = (key: string) => (
    <a
      key={key}
      href={RH_PROMO_URL}
      target="_blank"
      rel="noreferrer"
      className="flex shrink-0 items-center gap-1 pr-8 text-[10px] leading-relaxed text-secondary transition-colors hover:text-foreground"
    >
      <span className="font-medium text-amber-600 dark:text-amber-400">Claude、ChatGPT、Gemini</span>
      <span>等国际模型直连稳定不掉线，</span>
      <span className="font-medium text-amber-600 dark:text-amber-400">DeepSeek v4.1 Flash {range}</span>
      <span>，</span>
      <span>邀请链接注册享</span>
      <span className="font-medium text-amber-600 dark:text-amber-400">首充满减</span>
      <span>🎁</span>
    </a>
  )
  return (
    <div className="group hidden min-w-0 flex-1 items-center gap-1.5 rounded-btn border border-amber-400/30 bg-amber-400/15 py-1 pl-2 pr-1 md:flex dark:border-amber-400/25 dark:bg-amber-400/10">
      <Gift className="h-3 w-3 shrink-0 text-amber-600 dark:text-amber-400" />
      <div className="min-w-0 flex-1 overflow-hidden">
        <div className="flex w-max animate-[rh-marquee_26s_linear_infinite] whitespace-nowrap motion-reduce:animate-none group-hover:[animation-play-state:paused]">
          {['a', 'b', 'c', 'd'].map(copy)}
        </div>
      </div>
      <button
        onClick={snooze}
        title="关闭推广 (一周内不再显示)"
        aria-label="关闭推广 (一周内不再显示)"
        className="shrink-0 rounded-btn p-0.5 text-muted transition-colors hover:bg-elevated hover:text-foreground"
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  )
}

/** 助手设置: 动作工具免确认开关。免确认打开后 create_signal_strategy /
 *  create_strategy / run_backtest / add_to_watchlist / sync_data 不再逐次
 *  弹确认卡, 直接执行。偏好存后端 preferences, 与设置页同源。 */
function AssistantSettingsMenu({ onDone }: { onDone: () => void }) {
  const prefs = usePreferences()
  const qc = useQueryClient()
  const enabled = prefs.data?.assistant_auto_approve_actions ?? false
  const save = useMutation({
    mutationFn: (next: boolean) => api.updateAssistantAutoApprove(next),
    onSuccess: () => { qc.invalidateQueries({ queryKey: QK.preferences }) },
  })

  return (
    <div className="absolute right-0 top-9 z-20 w-72 rounded-card border border-border bg-surface p-3 shadow-xl">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-xs font-medium text-foreground">动作工具免确认</div>
          <div className="mt-1 text-[11px] leading-relaxed text-muted">
            打开后创建信号/策略、回测、加自选、数据补全不再逐次弹确认卡, 直接执行。
          </div>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          aria-label="动作工具免确认"
          disabled={save.isPending}
          onClick={() => save.mutate(!enabled)}
          className={cn(
            'relative mt-0.5 inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors duration-200 disabled:opacity-50',
            enabled ? 'bg-accent' : 'bg-border',
          )}
        >
          <span
            className={cn(
              'inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow-sm transition-transform duration-200',
              enabled ? 'translate-x-[18px]' : 'translate-x-0.5',
            )}
          />
        </button>
      </div>
      {enabled && (
        <div className="mt-2 rounded-btn bg-warning/10 px-2 py-1.5 text-[10px] leading-relaxed text-warning">
          已开启: 助手会不经确认直接写入。轮次检查点仍然有效 (防止单轮无限调用)。
        </div>
      )}
      <div className="mt-2 flex items-center justify-between border-t border-border/50 pt-2">
        <span className="text-[10px] text-muted">更多 AI 配置在设置页</span>
        <button
          type="button"
          onClick={onDone}
          className="cursor-pointer rounded-btn px-2 py-0.5 text-[10px] text-muted transition-colors hover:bg-elevated hover:text-foreground"
        >
          关闭
        </button>
      </div>
    </div>
  )
}

/** 会话时间: 今天/昨天 + HH:MM, 年内 MM-DD HH:MM, 跨年带年份 */
function formatSessionTime(ts: number): string {
  const d = new Date(ts)
  const now = new Date()
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (d.toDateString() === now.toDateString()) return `今天 ${hm}`
  const yesterday = new Date(now.getTime() - 86_400_000)
  if (d.toDateString() === yesterday.toDateString()) return `昨天 ${hm}`
  const md = `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  return d.getFullYear() === now.getFullYear() ? `${md} ${hm}` : `${d.getFullYear()}-${md}`
}

function IconButton({
  title,
  onClick,
  disabled,
  children,
}: {
  title: string
  onClick: () => void
  disabled?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      disabled={disabled}
      onClick={onClick}
      className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-btn text-secondary transition-colors duration-150 ease-smooth hover:bg-elevated hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
    >
      {children}
    </button>
  )
}

function MessageList({
  messages,
  sending,
  status,
  suggests,
}: {
  messages: SessionMessages
  sending: boolean
  status: AssistantStatus | null
  suggests: QuickSuggest[]
}) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const pinnedRef = useRef(true)
  const [showJump, setShowJump] = useState(false)

  useLayoutEffect(() => {
    const el = scrollRef.current
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight
  }, [messages])

  const onScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight
    pinnedRef.current = distance < 80
    setShowJump(distance >= 240)
  }

  const jumpToBottom = () => {
    const el = scrollRef.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }

  return (
    <div className="relative min-h-0 flex-1">
      <div ref={scrollRef} onScroll={onScroll} className="h-full space-y-4 overflow-y-auto px-5 py-4">
        {messages.length === 0 ? (
          <EmptyState status={status} suggests={suggests} />
        ) : (
          messages.map(message => (
            <AssistantMessageView key={message.id} message={message} onRetry={sending ? undefined : retryLast} />
          ))
        )}
      </div>
      <AnimatePresence>
        {showJump && (
          <motion.button
            type="button"
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 4 }}
            transition={{ duration: 0.15, ease: EASE_SMOOTH }}
            onClick={jumpToBottom}
            className="absolute bottom-3 left-1/2 flex h-7 w-7 -translate-x-1/2 cursor-pointer items-center justify-center rounded-full border border-border bg-elevated text-secondary shadow-md transition-colors duration-150 ease-smooth hover:text-foreground"
            title="回到底部"
          >
            <ArrowDown className="h-3.5 w-3.5" />
          </motion.button>
        )}
      </AnimatePresence>
    </div>
  )
}

/** 空会话建议分组的图标 (后端 suggests 的 group 字段驱动, 未匹配时兜底)。 */
const SUGGEST_GROUP_ICONS: Record<string, typeof TrendingUp> = {
  行情与大盘: TrendingUp,
  我的与个股: Star,
  策略与信号: FlaskConical,
  数据与扩展: Database,
}

function EmptyState({ status, suggests }: { status: AssistantStatus | null; suggests: QuickSuggest[] }) {
  const navigate = useNavigate()
  const isAdmin = useIsAdmin()

  if (status && !status.configured) {
    return (
      <div className="rounded-card border border-warning/30 bg-warning/5 p-4 text-sm">
        <div className="flex items-center gap-1.5 font-medium text-warning">
          <Settings2 className="h-4 w-4" />
          AI 未配置
        </div>
        <p className="mt-1.5 text-secondary">AI 助手需要先配置 AI 供应商和 API Key 才能对话。</p>
        {isAdmin ? (
          <button
            type="button"
            onClick={() => navigate('/admin?tab=ai')}
            className="mt-2 cursor-pointer rounded-btn bg-accent px-3 py-1.5 text-xs text-white transition-colors duration-150 ease-smooth hover:bg-accent/90"
          >
            去管理后台配置
          </button>
        ) : (
          <p className="mt-2 text-xs text-muted">请联系管理员在管理后台配置 AI 供应商与 API Key。</p>
        )}
      </div>
    )
  }

  if (status && !status.supports_tools) {
    return (
      <div className="rounded-card border border-warning/30 bg-warning/5 p-4 text-sm">
        <div className="flex items-center gap-1.5 font-medium text-warning">
          <Settings2 className="h-4 w-4" />
          当前供应商不支持工具调用
        </div>
        <p className="mt-1.5 text-secondary">
          AI 助手依赖工具调用能力({status.provider} 不支持), 请在设置页切换为 OpenAI 兼容模型。
        </p>
        {isAdmin ? (
          <button
            type="button"
            onClick={() => navigate('/admin?tab=ai')}
            className="mt-2 cursor-pointer rounded-btn bg-accent px-3 py-1.5 text-xs text-white transition-colors duration-150 ease-smooth hover:bg-accent/90"
          >
            去管理后台调整
          </button>
        ) : (
          <p className="mt-2 text-xs text-muted">请联系管理员调整为 OpenAI 兼容模型。</p>
        )}
      </div>
    )
  }

  if (!suggests.length) {
    return (
      <p className="pt-8 text-center text-xs text-muted">
        问我任何关于行情、个股、策略、扩展数据或数据能力的问题。
      </p>
    )
  }

  // 按后端 group 字段分区, 保持原始顺序; 未分组的建议归入「更多」。
  const groups = new Map<string, QuickSuggest[]>()
  for (const suggest of suggests) {
    const key = suggest.group || '更多'
    groups.set(key, [...(groups.get(key) ?? []), suggest])
  }

  return (
    <div className="space-y-4 pb-2 pt-2">
      <div className="rounded-card border border-border bg-gradient-to-b from-accent/10 to-transparent px-4 py-4 text-center">
        <div className="mx-auto flex h-9 w-9 items-center justify-center rounded-full bg-accent/15 text-accent">
          <Bot className="h-4.5 w-4.5" />
        </div>
        <p className="mt-2 text-sm font-medium text-foreground">有什么可以帮你？</p>
        <p className="mt-1 text-xs text-muted">基于本地真实数据回答 · 每次取数都可展开核对</p>
      </div>

      {[...groups.entries()].map(([group, items]) => {
        const GroupIcon = SUGGEST_GROUP_ICONS[group] ?? Sparkles
        return (
          <div key={group} className="space-y-2">
            <div className="flex items-center gap-1.5 px-0.5 text-[11px] font-medium text-muted">
              <GroupIcon className="h-3.5 w-3.5" />
              {group}
            </div>
            <div className="flex flex-wrap gap-2">
              {items.map(suggest => (
                <button
                  key={suggest.id}
                  type="button"
                  onClick={() => sendMessage(suggest.prompt)}
                  className="max-w-full shrink-0 cursor-pointer rounded-card border border-border bg-base/60 px-3 py-2 text-left text-xs text-secondary transition-all duration-150 ease-smooth hover:border-accent/40 hover:bg-elevated hover:text-foreground"
                >
                  {suggest.label}
                </button>
              ))}
            </div>
          </div>
        )
      })}

      <div className="rounded-card border border-dashed border-border bg-base/40 px-3.5 py-3 text-xs">
        <div className="flex items-center gap-1.5 font-medium text-foreground">
          <Puzzle className="h-3.5 w-3.5 text-accent" />
          扩展内容同样可以问
        </div>
        <p className="mt-1 leading-relaxed text-secondary">
          扩展数据表、自定义策略与信号等扩展内容都能问答互动, 比如「我的扩展数据表里有什么」「看看最新一张的数据」。
          AI 助手本身也是一个 custom 扩展模块。
        </p>
      </div>
    </div>
  )
}

function InputArea({ sending, blocked }: { sending: boolean; blocked: boolean }) {
  const [text, setText] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const canSend = !sending && !blocked && text.trim().length > 0

  const submit = () => {
    if (!canSend) return
    sendMessage(text)
    setText('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  const autoGrow = () => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 144)}px`
  }

  return (
    <div className="shrink-0 border-t border-border/60 bg-surface px-4 pb-3 pt-3">
      <div
        className={cn(
          'flex items-end gap-1.5 rounded-card border bg-base/60 p-1.5',
          'transition-[border-color,background-color,box-shadow] duration-200 ease-smooth',
          'focus-within:border-accent/50 focus-within:bg-base focus-within:ring-[3px] focus-within:ring-accent/10',
          blocked ? 'border-warning/30' : 'border-border',
        )}
      >
        <textarea
          ref={textareaRef}
          value={text}
          rows={1}
          disabled={blocked}
          placeholder={blocked ? '请先在设置页配置 AI' : '问点什么…'}
          onChange={e => { setText(e.target.value); autoGrow() }}
          onKeyDown={onKeyDown}
          className="max-h-36 min-h-[40px] flex-1 resize-none rounded-btn bg-transparent px-3 py-2 text-sm text-foreground outline-none placeholder:text-muted/80 disabled:cursor-not-allowed"
        />
        {sending ? (
          <button
            type="button"
            onClick={stopSending}
            title="停止生成"
            aria-label="停止生成"
            className="mr-0.5 mb-0.5 flex h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-full border border-border bg-elevated text-secondary transition-all duration-150 ease-smooth hover:border-danger/40 hover:text-danger active:scale-95"
          >
            <Square className="h-3 w-3 fill-current" />
          </button>
        ) : (
          <button
            type="button"
            onClick={submit}
            disabled={!canSend}
            title="发送"
            aria-label="发送"
            className={cn(
              'mr-0.5 mb-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-all duration-150 ease-smooth active:scale-95',
              canSend
                ? 'cursor-pointer bg-accent text-white shadow-sm hover:bg-accent/90'
                // 空输入时退为幽灵态而非褪色实心块, 视觉重心不被锁死在右下角
                : 'cursor-default bg-elevated/60 text-muted',
            )}
          >
            <SendHorizontal className="h-4 w-4" />
          </button>
        )}
      </div>
      <div className="mt-2 flex items-center justify-between gap-2 px-1">
        <p className="text-[10px] leading-relaxed text-muted">
          回答基于本地数据, 不构成投资建议
        </p>
        <span className="shrink-0 text-[10px] text-muted/70">
          Enter 发送 · ⌘K 打开助手
        </span>
      </div>
    </div>
  )
}
