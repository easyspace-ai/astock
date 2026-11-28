// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

vi.mock('@/lib/uiPrefs', () => ({ hydrateUiPrefs: () => Promise.resolve(), setUiPref: vi.fn() }))

let requests: Array<{ messages: Array<Record<string, unknown>> }>
let replies: Array<Array<Record<string, unknown>>>

beforeEach(() => {
  vi.resetModules()
  vi.useFakeTimers()
  localStorage.clear()
  requests = []
  replies = []
  vi.stubGlobal('fetch', vi.fn(async (url, init) => {
    if (url === '/api/custom/assistant/chat') {
      requests.push(JSON.parse(init.body))
      return new Response((replies.shift() ?? []).map(e => JSON.stringify(e)).join('\n'))
    }
    return new Response(JSON.stringify({ sessions: [], activeId: '' }))
  }))
})
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals() })

it.each(['模型推理', ''])('preserves reasoning across tools, reload, and the next question (%s)', async reasoning => {
  replies.push([
    { type: 'delta', content: '查询中' },
    { type: 'assistant_context', reasoning_content: reasoning, reasoning_context: 'profile' },
    { type: 'tool_call', call_id: 'one', name: 'list_strategies', args: {} },
    { type: 'tool_result', call_id: 'one', name: 'list_strategies', ok: true, summary: 'ok', elapsed_ms: 1 },
    { type: 'delta', content: '完成' },
    { type: 'assistant_context', reasoning_content: reasoning + '最终', reasoning_context: 'profile' },
    { type: 'done' },
  ])
  let store = await import('./store')
  store.bindAssistantAccount(1)
  store.sendMessage('查询策略')
  await vi.waitFor(() => expect(store.activeSession()?.messages.some(m => m.role === 'assistant' && m.streaming)).toBe(false))
  const saved = JSON.parse(localStorage.getItem('assistant.sessions.v2:1')!)
  const answers = saved.sessions[0].messages.filter((m: { role: string }) => m.role === 'assistant')
  expect(answers.map((m: { reasoningContent: string }) => m.reasoningContent)).toEqual([reasoning, reasoning + '最终'])
  expect(answers.map((m: { content: string }) => m.content)).toEqual(['查询中', '完成'])

  vi.clearAllTimers()
  vi.resetModules()
  store = await import('./store')
  store.bindAssistantAccount(1)
  replies.push([{ type: 'delta', content: '继续回答' }, { type: 'done' }])
  store.sendMessage('继续解释')
  await vi.waitFor(() => expect(requests).toHaveLength(2))
  expect(requests[1].messages).toEqual([
    { role: 'user', content: '查询策略' },
    { role: 'assistant', content: '查询中', reasoning_content: reasoning, reasoning_context: 'profile' },
    { role: 'assistant', content: '完成', reasoning_content: reasoning + '最终', reasoning_context: 'profile' },
    { role: 'user', content: '继续解释' },
  ])
})

it('keeps plain-model and old-session history compatible', async () => {
  localStorage.setItem('assistant.sessions.v2:1', JSON.stringify({ activeId: 'old', sessions: [{
    id: 'old', title: '旧会话', createdAt: 1, messages: [
      { id: 'u', role: 'user', content: '旧问题', ts: 1 },
      { id: 'a', role: 'assistant', content: '旧回答', ts: 2, streaming: false },
    ],
  }] }))
  replies.push([{ type: 'delta', content: '新回答' }, { type: 'done' }])
  const store = await import('./store')
  store.bindAssistantAccount(1)
  store.sendMessage('继续')
  await vi.waitFor(() => expect(requests).toHaveLength(1))
  expect(requests[0].messages).toEqual([
    { role: 'user', content: '旧问题' }, { role: 'assistant', content: '旧回答' },
    { role: 'user', content: '继续' },
  ])
})

it('never shows or writes another account history in the same browser', async () => {
  replies.push([{ type: 'delta', content: '答' }, { type: 'done' }])
  let store = await import('./store')
  store.bindAssistantAccount(1)
  store.sendMessage('账号一的问题')
  await vi.waitFor(() => expect(store.activeSession()?.messages.some(m => m.role === 'assistant' && m.streaming)).toBe(false))
  expect(localStorage.getItem('assistant.sessions.v2:1')).toBeTruthy()

  // 模拟同浏览器换账号 (新注册): 模块重载 + 绑定另一个 id
  vi.clearAllTimers()
  vi.resetModules()
  store = await import('./store')
  store.bindAssistantAccount(2)
  expect(store.activeSession()).toBeNull()
  expect(store.assistantSnapshot().sessions).toHaveLength(0)
  expect(localStorage.getItem('assistant.sessions.v2:2')).toBeNull()

  // 未绑定账号时不读取任何历史 (旧的全局 key 也不再被使用)
  localStorage.setItem('assistant.sessions.v1', JSON.stringify({ activeId: 'x', sessions: [{ id: 'x', title: 'x', createdAt: 1, messages: [] }] }))
  vi.resetModules()
  store = await import('./store')
  expect(store.assistantSnapshot().sessions).toHaveLength(0)
})
