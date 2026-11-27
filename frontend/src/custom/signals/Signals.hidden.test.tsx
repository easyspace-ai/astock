// @vitest-environment jsdom
// 内置信号「隐藏」: 只改自己的视图清单 — 管理页拿 include_hidden=true 的分组,
// 已隐藏项进「已隐藏 · 仍参与计算」区并可一键恢复, 不出现编辑/删除入口。
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Signals } from '@/custom/signals/Page'

const mocks = vi.hoisted(() => ({
  list: vi.fn(async (_opts?: { quiet?: boolean; includeHidden?: boolean }) => ({ signals: [] as unknown[] })),
  hide: vi.fn(async (_id: string, _hidden: boolean) => ({ ok: true, id: '', hidden: false, hidden_count: 0 })),
}))

vi.mock('@/lib/api', () => ({
  api: {
    customSignalsList: mocks.list,
    customSignalHide: mocks.hide,
    customSignalsOptions: async () => ({
      fields: [{ key: 'close', label: '前复权收盘价' }],
      groups: [],
      maxDays: 60,
      operators: ['>', '<'],
      stringFields: ['symbol'],
      stringOperators: ['=='],
    }),
    customSignalDelete: async () => ({ ok: true }),
    customSignalArchive: async () => ({ ok: true }),
    customSignalSave: async () => ({ ok: true }),
    strategyAiStatus: async () => ({ configured: false }),
  },
}))

let root: Root | null = null
let container: HTMLElement | null = null

beforeEach(() => {
  mocks.list.mockClear()
  mocks.hide.mockClear()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  root?.unmount()
  container?.remove()
})

function render(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  root!.render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/signals']}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}

const builtin = {
  id: 'ma_golden_5_20',
  name: '均线金叉',
  kind: 'both',
  condition: [],
  conditions: [{ left: 'close', op: '>', right: 'field:close', leftDays: 0, rightDays: 1 }],
  column: 'signal_ma_golden_5_20',
  owner: null,
  read_only: true,
}

it('已隐藏的内置信号进独立分组, 可恢复, 且无编辑入口', async () => {
  mocks.list.mockImplementation(async () => ({
    signals: [
      { ...builtin, id: 'visible_sig', name: '可见内置', hidden: false },
      { ...builtin, id: 'hidden_sig', name: '被隐藏的内置', hidden: true },
    ],
  }))

  render(<Signals />)

  await vi.waitFor(() => {
    expect(container!.textContent).toContain('被隐藏的内置')
  }, { timeout: 5_000, interval: 25 })

  // 管理页必须带 include_hidden, 否则拿不到「已隐藏」分组
  expect(mocks.list).toHaveBeenCalled()
  expect((mocks.list.mock.calls[0][0] as { includeHidden?: boolean }).includeHidden).toBe(true)
  expect(container!.textContent).toContain('已隐藏 · 1 · 仍参与计算')
  expect(container!.textContent).toContain('可见内置')

  const restore = [...container!.querySelectorAll('button')].find(b => b.textContent?.includes('恢复'))
  expect(restore).toBeTruthy()
  restore!.click()
  await vi.waitFor(() => {
    expect(mocks.hide).toHaveBeenCalledWith('hidden_sig', false)
  }, { timeout: 5_000, interval: 25 })

  // 内置卡片只有「隐藏」按钮, 不给编辑/删除
  expect(container!.querySelector('button[title="编辑"]')).toBeNull()
})
