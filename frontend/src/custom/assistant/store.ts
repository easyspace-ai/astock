/**
 * AI 助手前端状态 — 会话/消息/发送编排, useSyncExternalStore 单例 store。
 *
 * 会话历史双写: localStorage 即时缓存 (启动秒开/离线兜底) + 服务端同步
 * (/api/custom/assistant/sessions, 防抖 1.5s fire-and-forget) — 换设备可见。
 * 每轮请求把可见的 user/assistant 消息回传, 由后端负责截断。工具往返
 * (footprint)只用于本地展示, 不进入请求历史。
 */
import { useSyncExternalStore } from 'react'
import {
  assistantChatStream,
  decideAssistantAction,
  type AssistantChart,
  type AssistantContext,
  type AssistantEvent,
  type ChatHistoryMessage,
} from './client'
import { mergeSessions } from './sessionsMerge'

/**
 * running=执行中; awaiting=动作工具等待用户确认; approved/denied=用户已点
 * 确认/拒绝(短暂中间态, 后端 tool_result 随后到达并覆盖为 ok/error)。
 */
export type ToolCallStatus = 'running' | 'awaiting' | 'approved' | 'denied' | 'ok' | 'error'

export interface ToolCallConfirm {
  label: string
  risk: string
}

export interface ToolCallRecord {
  callId: string
  name: string
  args: Record<string, unknown>
  status: ToolCallStatus
  summary?: string
  elapsedMs?: number
  charts?: AssistantChart[]
  confirm?: ToolCallConfirm
}

export type ChatMessage =
  | { id: string; role: 'user'; content: string; ts: number }
  | { id: string; role: 'assistant'; content: string; ts: number; streaming: boolean; reasoningContent?: string; reasoningContext?: string }
  | { id: string; role: 'footprint'; calls: ToolCallRecord[]; ts: number }
  | { id: string; role: 'error'; kind: string; message: string; hint?: string; ts: number }
  | { id: string; role: 'notice'; content: string; ts: number }

export interface AssistantSession {
  id: string
  title: string
  createdAt: number
  messages: ChatMessage[]
}

export interface AssistantState {
  sessions: AssistantSession[]
  activeId: string
  sending: boolean
}

/** localStorage 分区前缀 (后接账号 id): 同一浏览器可能先后登录多个账号,
 *  不分区会把上一个账号的历史呈现给下一个登录的人 (新注册账号看到别人的会话),
 *  并把这份历史回写到新账号名下。 */
const STORAGE_PREFIX = 'assistant.sessions.v2'
const SESSIONS_URL = '/api/custom/assistant/sessions'
const SERVER_SYNC_DEBOUNCE_MS = 1500
const MAX_SESSIONS = 20
const MAX_SESSION_MESSAGES = 200
const MAX_INPUT_CHARS = 4000

let state: AssistantState = {
  sessions: [],
  activeId: '',
  sending: false,
}

const listeners = new Set<() => void>()
let abortController: AbortController | null = null
let pageContext: AssistantContext = {}
let serverSyncTimer: ReturnType<typeof setTimeout> | null = null
/** 当前绑定的账号 id; null = 未绑定 (账号信息未到/未登录), 此时不读写任何持久层。 */
let accountKey: string | null = null

function storageKey(): string | null {
  return accountKey ? `${STORAGE_PREFIX}:${accountKey}` : null
}

/** 绑定当前账号 (由常驻宿主在 /api/accounts/me 到达后调用)。账号一变立刻清空
 *  内存态、按该账号的分区重载并同步服务端 — 账号之间互不留痕。重复绑定同一
 *  账号是空操作 (多个挂载点都可以调用)。 */
export function bindAssistantAccount(userId: number | string | null | undefined) {
  const next = userId === null || userId === undefined || userId === '' ? null : String(userId)
  if (next === accountKey) return
  if (state.sending) {
    abortController?.abort()
    abortController = null
  }
  accountKey = next
  state = { sessions: [], activeId: '', sending: false }
  if (next) loadSessions()
  emit()
  if (next) void syncFromServer()
}

function emit() {
  for (const listener of listeners) listener()
}

function setState(partial: Partial<AssistantState>, persist = true) {
  state = { ...state, ...partial }
  if (persist) persistSessions()
  emit()
}

function trimmedSessions() {
  return state.sessions
    .slice(0, MAX_SESSIONS)
    .map(session => ({ ...session, messages: session.messages.slice(-MAX_SESSION_MESSAGES) }))
}

function persistSessions() {
  const key = storageKey()
  // 未绑定账号时只保留内存态: 既不落 localStorage 也不推服务端, 防止"未知身份"
  // 的数据被写进别人 (或下一个登录的人) 的存储分区。
  if (!key) return
  try {
    localStorage.setItem(key, JSON.stringify({ sessions: trimmedSessions(), activeId: state.activeId }))
  } catch { /* 存储满/隐私模式时静默降级为内存态 */ }
  scheduleServerSync()
}

/** 服务端同步 (防抖 fire-and-forget): 消息边界触发, 流式 delta 不落盘不触发;
 *  失败静默 — 旧后端无此端点/离线时退化为纯本地行为。 */
function scheduleServerSync() {
  const key = storageKey()
  if (!key) return
  if (serverSyncTimer) clearTimeout(serverSyncTimer)
  serverSyncTimer = setTimeout(() => {
    serverSyncTimer = null
    if (storageKey() !== key) return // 排队期间已换账号: 丢弃, 不写入新账号
    void fetch(SESSIONS_URL, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessions: trimmedSessions(), activeId: state.activeId }),
    }).catch(() => { /* 静默降级 */ })
  }, SERVER_SYNC_DEBOUNCE_MS)
}

/** 启动同步: 本地先渲染 (秒开), 再拉服务端合并 (换设备可见对方历史)。
 *  拉取期间用户已开始交互 (会话被改动) 则放弃合并 — 本地更新会经防抖写回服务端。 */
async function syncFromServer() {
  const key = storageKey()
  if (!key) return
  const sessionsBefore = state.sessions
  const sendingBefore = state.sending
  try {
    const resp = await fetch(SESSIONS_URL)
    if (storageKey() !== key) return // 期间换了账号: 结果作废
    if (!resp.ok) return // 旧后端(404)/网关错误 → 保持本地
    const data = await resp.json() as { sessions?: unknown; activeId?: unknown }
    if (!Array.isArray(data.sessions)) return
    if (state.sending || state.sessions !== sessionsBefore || sendingBefore) return
    const remote = (data.sessions as AssistantSession[])
      .filter(s => s && typeof s.id === 'string' && Array.isArray(s.messages))
    if (!remote.length) return
    const merged = mergeSessions(state.sessions, state.activeId, remote, typeof data.activeId === 'string' ? data.activeId : '', MAX_SESSIONS)
    const unchanged = merged.sessions.length === state.sessions.length
      && merged.sessions.every((s, i) => s === state.sessions[i])
      && merged.activeId === state.activeId
    if (unchanged) return
    setState({ sessions: merged.sessions, activeId: merged.activeId })
  } catch { /* 网络不可达: 保持本地 */ }
}

function loadSessions() {
  const key = storageKey()
  if (!key) return
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return
    const parsed = JSON.parse(raw) as { sessions?: AssistantSession[]; activeId?: string }
    if (!Array.isArray(parsed.sessions)) return
    // 旧数据兼容: 只校验结构, 不校验具体消息形状(未知消息渲染为空)。
    const sessions = parsed.sessions.filter(s => s && typeof s.id === 'string' && Array.isArray(s.messages))
    const activeId = parsed.activeId && sessions.some(s => s.id === parsed.activeId)
      ? parsed.activeId
      : sessions[0]?.id ?? ''
    state = { ...state, sessions, activeId }
  } catch { /* 损坏数据视为无历史 */ }
}

function uid(): string {
  return Math.random().toString(36).slice(2, 10) + Date.now().toString(36)
}

export function activeSession(): AssistantSession | null {
  return state.sessions.find(s => s.id === state.activeId) ?? null
}

function ensureSession(): AssistantSession {
  let session = activeSession()
  if (!session) {
    session = { id: uid(), title: '新对话', createdAt: Date.now(), messages: [] }
    setState({ sessions: [session, ...state.sessions], activeId: session.id })
  }
  return session
}

function patchActiveSession(
  patch: (session: AssistantSession) => AssistantSession,
  { persist = true }: { persist?: boolean } = {},
) {
  setState({
    sessions: state.sessions.map(s => (s.id === state.activeId ? patch(s) : s)),
  }, persist)
}

/** 定向修补指定会话 (不存在则忽略): 生成中途切换/删除会话后, 终态回落必须
 * 落在发起生成的那一个会话里, 而不是追加到当前 activeId 指向的新会话。 */
function patchSessionById(
  sessionId: string,
  patch: (session: AssistantSession) => AssistantSession,
) {
  if (!state.sessions.some(s => s.id === sessionId)) return
  setState({
    sessions: state.sessions.map(s => (s.id === sessionId ? patch(s) : s)),
  })
}

function appendMessage(message: ChatMessage) {
  patchActiveSession(session => ({ ...session, messages: [...session.messages, message] }))
}

function activeMessages(): ChatMessage[] {
  return activeSession()?.messages ?? []
}

/** 发给后端的历史: 仅 user/assistant 且有内容(足迹/错误/提示不入上下文)。 */
function isTextMessage(m: ChatMessage): m is Extract<ChatMessage, { role: 'user' | 'assistant' }> {
  return (m.role === 'user' || m.role === 'assistant') && m.content.trim().length > 0
}

function buildHistory(messages: ChatMessage[]): ChatHistoryMessage[] {
  return messages
    .filter(isTextMessage)
    .map(m => {
      const message: ChatHistoryMessage = { role: m.role, content: m.content }
      if (m.role === 'assistant' && m.reasoningContent !== undefined) {
        message.reasoning_content = m.reasoningContent
        message.reasoning_context = m.reasoningContext
      }
      return message
    })
}

/** 一轮生成中的滚动游标: 当前正在写入的足迹卡与正文段。
 *
 * 工具调用发生在正文中间时, 就地把已生成的正文段"封口", 新开一张足迹卡 +
 * 新正文段 —— 这样渲染出来的顺序与事件到达顺序一致 (工具卡显示在它被调用的
 * 那个位置), 而不是整回合一张累积卡固定堆在正文上方。
 */
interface TurnCursor {
  footprintId: string
  assistantId: string
  /** 发起生成的会话: append 型流事件 (error/notice) 必须钉在它上面, 防止
   *  中途切换会话后错投到 activeId 指向的新会话。 */
  sessionId: string
}

/** 一轮生成的完整编排: 建占位 → 流事件落库 → 终态收敛。 */
async function runGeneration() {
  const history = buildHistory(activeMessages())
  // 生成的终态 (中断提示/流式收敛) 必须落在发起会话: 中途切换或删除会话时
  // activeId 已变, 若按 activeId 追加会把"已中断"写进无关的新会话。
  const generationSessionId = state.activeId
  const cursor: TurnCursor = { footprintId: uid(), assistantId: uid(), sessionId: generationSessionId }
  appendMessage({ id: cursor.footprintId, role: 'footprint', calls: [], ts: Date.now() })
  appendMessage({ id: cursor.assistantId, role: 'assistant', content: '', ts: Date.now(), streaming: true })
  setState({ sending: true })

  abortController = new AbortController()
  try {
    for await (const event of assistantChatStream(
      { messages: history, context: pageContext },
      abortController.signal,
    )) {
      applyEvent(event, cursor)
    }
  } catch (error) {
    const aborted = abortController.signal.aborted
    const message: ChatMessage = aborted
      ? { id: uid(), role: 'notice', content: '已中断, 可点击重试继续。', ts: Date.now() }
      : { id: uid(), role: 'error', kind: 'network', message: error instanceof Error ? error.message : String(error), ts: Date.now() }
    patchSessionById(generationSessionId, session => ({
      ...session,
      messages: [...session.messages, message],
    }))
  } finally {
    abortController = null
    patchSessionById(generationSessionId, session => ({
      ...session,
      messages: session.messages.map(m => (m.role === 'assistant' && m.streaming ? { ...m, streaming: false } : m)),
    }))
    setState({ sending: false })
  }
}

/**
 * 正文段里已有内容时, 在调用工具前把它封口并新开一张足迹卡 + 正文段。
 * 正文为空 (整轮还没输出文字, 例如连续调用工具) 时沿用当前足迹卡, 不产生空段。
 */
function rotateTurn(cursor: TurnCursor) {
  const session = activeSession()
  const current = session?.messages.find(
    m => m.role === 'assistant' && m.id === cursor.assistantId,
  )
  const hasText = Boolean(current && current.role === 'assistant' && current.content.trim())
  if (!hasText) return

  const footprintId = uid()
  const assistantId = uid()
  patchActiveSession(s => ({
    ...s,
    messages: [
      ...s.messages.map(m => (m.id === cursor.assistantId && m.role === 'assistant'
        ? { ...m, streaming: false }
        : m)),
      { id: footprintId, role: 'footprint' as const, calls: [], ts: Date.now() },
      { id: assistantId, role: 'assistant' as const, content: '', ts: Date.now(), streaming: true },
    ],
  }))
  cursor.footprintId = footprintId
  cursor.assistantId = assistantId
}

function applyEvent(event: AssistantEvent, cursor: TurnCursor) {
  switch (event.type) {
    case 'assistant_context':
      // Provider state belongs to the completed text segment, before a tool
      // rotates it. It is persisted for follow-ups and never rendered as text.
      patchSessionById(cursor.sessionId, session => ({
        ...session,
        messages: session.messages.map(m => m.id === cursor.assistantId && m.role === 'assistant'
          ? { ...m, reasoningContent: event.reasoning_content, reasoningContext: event.reasoning_context }
          : m),
      }))
      break
    case 'tool_call':
      // 工具调用就地把之前的正文封口, 卡片插在它被调用的位置
      rotateTurn(cursor)
      patchActiveSession(session => ({
        ...session,
        messages: session.messages.map(m => m.id === cursor.footprintId && m.role === 'footprint'
          ? {
              ...m,
              calls: [...m.calls, {
                callId: event.call_id,
                name: event.name,
                args: event.args ?? {},
                status: 'running' as ToolCallStatus,
              }],
            }
          : m),
      }))
      break
    case 'tool_result':
      patchActiveSession(session => ({
        ...session,
        messages: session.messages.map(m => m.id === cursor.footprintId && m.role === 'footprint'
          ? {
              ...m,
              calls: m.calls.map(c => c.callId === event.call_id
                ? {
                    ...c,
                    status: event.ok ? 'ok' as ToolCallStatus : 'error' as ToolCallStatus,
                    summary: event.summary,
                    elapsedMs: event.elapsed_ms,
                    charts: event.charts,
                  }
                : c),
            }
          : m),
      }))
      break
    case 'action_confirm':
      patchActiveSession(session => ({
        ...session,
        messages: session.messages.map(m => m.id === cursor.footprintId && m.role === 'footprint'
          ? {
              ...m,
              calls: m.calls.map(c => c.callId === event.call_id
                ? {
                    ...c,
                    status: 'awaiting' as ToolCallStatus,
                    confirm: { label: event.label, risk: event.risk },
                  }
                : c),
            }
          : m),
      }))
      break
    case 'rounds_confirm':
      // 轮次检查点: 以合成足迹记录承载「继续/停止」卡, 决策走同一端点。
      patchActiveSession(session => ({
        ...session,
        messages: session.messages.map(m => m.id === cursor.footprintId && m.role === 'footprint'
          ? {
              ...m,
              calls: [...m.calls, {
                callId: event.call_id,
                name: '__rounds',
                args: { reached: event.reached },
                status: 'awaiting' as ToolCallStatus,
                confirm: {
                  label: `已连续 ${event.reached} 轮工具调用仍未完成`,
                  risk: '继续将再运行一个周期(至多再 100 轮), 消耗更多时间与 token; 停止则保留已生成内容。',
                },
              }],
            }
          : m),
      }))
      break
    case 'delta':
      // 逐字流式高频到达: 只更新内存态, 由生成结束时的 setState({sending:false}) 统一落盘。
      patchActiveSession(session => ({
        ...session,
        messages: session.messages.map(m => (m.id === cursor.assistantId && m.role === 'assistant'
          ? { ...m, content: m.content + event.content }
          : m)),
      }), { persist: false })
      break
    case 'error':
      // append 型事件钉在发起会话 (map 型分支落错会话时是 no-op, 安全)
      patchSessionById(cursor.sessionId, session => ({
        ...session,
        messages: [...session.messages, { id: uid(), role: 'error', kind: event.kind, message: event.message, hint: event.hint, ts: Date.now() } as ChatMessage],
      }))
      break
    case 'notice':
      patchSessionById(cursor.sessionId, session => ({
        ...session,
        messages: [...session.messages, { id: uid(), role: 'notice', content: event.message, ts: Date.now() } as ChatMessage],
      }))
      break
    case 'done':
      // 流结束: 仍处于 awaiting 的确认(后端超时未执行)收敛为已取消, 卡片不再悬挂。
      patchActiveSession(session => ({
        ...session,
        messages: session.messages.map(m => m.role === 'footprint'
          ? { ...m, calls: m.calls.map(c => (c.status === 'awaiting' ? { ...c, status: 'denied' as ToolCallStatus } : c)) }
          : m),
      }))
      break
    default:
      break
  }
}

// ===== 对外动作 =====

export function sendMessage(textRaw: string) {
  const text = textRaw.trim().slice(0, MAX_INPUT_CHARS)
  if (!text || state.sending) return
  const session = ensureSession()
  if (!session.messages.length) {
    patchActiveSession(s => ({ ...s, title: text.slice(0, 16) }))
  }
  appendMessage({ id: uid(), role: 'user', content: text, ts: Date.now() })
  void runGeneration()
}

/** 重试: 丢弃最后一个 user 消息之后的失败产物, 用同一上下文重新生成。 */
export function retryLast() {
  if (state.sending) return
  const messages = activeMessages()
  const lastUserIdx = findLastIndex(messages, m => m.role === 'user')
  if (lastUserIdx < 0) return
  patchActiveSession(session => ({ ...session, messages: session.messages.slice(0, lastUserIdx + 1) }))
  void runGeneration()
}

export function stopSending() {
  abortController?.abort()
}

export function newSession() {
  if (state.sending) stopSending()
  const session: AssistantSession = { id: uid(), title: '新对话', createdAt: Date.now(), messages: [] }
  setState({ sessions: [session, ...state.sessions].slice(0, MAX_SESSIONS), activeId: session.id })
}

export function selectSession(id: string) {
  if (id === state.activeId) return
  if (state.sending) stopSending()
  setState({ activeId: id }, false)
}

export function deleteSession(id: string) {
  if (state.sending && id === state.activeId) stopSending()
  const sessions = state.sessions.filter(s => s.id !== id)
  const activeId = id === state.activeId ? sessions[0]?.id ?? '' : state.activeId
  setState({ sessions, activeId })
}

/** 确认卡按钮: POST 决策并立刻反馈; 后端随后以 tool_result 终态覆盖该记录。 */
export async function decideAction(callId: string, approve: boolean) {
  patchConfirmStatus(callId, approve ? 'approved' : 'denied')
  try {
    await decideAssistantAction(callId, approve)
  } catch {
    // 会话已断/确认过期: 本地收敛为拒绝态, 由 tool_result(或界面)呈现结果
    patchConfirmStatus(callId, 'denied')
  }
}

function patchConfirmStatus(callId: string, status: ToolCallStatus) {
  patchActiveSession(session => ({
    ...session,
    messages: session.messages.map(m => m.role === 'footprint'
      ? { ...m, calls: m.calls.map(c => (c.callId === callId ? { ...c, status } : c)) }
      : m),
  }))
}

/** 页面上下文由插槽组件上报(仅影响后续轮次), 用户可见于足迹中由后端回显。 */
export function setPageContext(context: AssistantContext) {
  pageContext = context
}

function findLastIndex<T>(items: T[], predicate: (item: T) => boolean): number {
  for (let i = items.length - 1; i >= 0; i -= 1) {
    if (predicate(items[i])) return i
  }
  return -1
}

// ===== React 绑定 =====

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useAssistantStore(): AssistantState {
  return useSyncExternalStore(subscribe, () => state, () => state)
}

/** 只读快照: 非 React 场景 (测试/埋点) 读取当前状态, 不订阅。 */
export function assistantSnapshot(): AssistantState {
  return state
}

/**
 * 会话状态不再在模块加载时自动读取: 必须先由界面调用 bindAssistantAccount(账号 id),
 * 拿到身份后才按账号分区加载本地缓存并同步服务端 —— 否则新登录/新注册账号会看到
 * 浏览器里上一个账号的历史。
 */
