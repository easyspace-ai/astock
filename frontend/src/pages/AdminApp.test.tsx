// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AdminApp } from './AdminApp'

// 后台壳只验证结构与分区切换; 具体配置面板由各自测试覆盖
vi.mock('./Settings', () => ({ Settings: ({ embedded }: { embedded?: boolean }) => <div data-testid="settings-panel">{String(embedded)}</div> }))
vi.mock('./Data', () => ({ Data: () => <div data-testid="data-panel" /> }))
vi.mock('@/lib/useAccount', () => ({
  useAccountUser: () => ({ data: { email: 'admin@example.com', role: 'admin' }, isLoading: false }),
}))
vi.mock('@/lib/api', () => ({ api: { accountLogout: vi.fn(), authLogout: vi.fn() } }))

let host: HTMLDivElement
let root: Root

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

async function render(section: 'settings' | 'data' = 'settings') {
  await act(async () => root.render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/admin${section === 'settings' ? '' : `/${section}`}`]}>
        <AdminApp section={section} />
      </MemoryRouter>
    </QueryClientProvider>,
  ))
}

it('renders a standalone admin shell without the front-office sidebar', async () => {
  await render()
  expect(host.querySelector('[data-testid="settings-panel"]')).not.toBeNull()
  expect(host.textContent).toContain('管理后台')
  expect(host.textContent).toContain('返回前台')
  // 前台导航不应出现
  expect(host.textContent).not.toContain('自选')
  expect(host.textContent).not.toContain('策略')
  expect(host.querySelector('a[href="/admin"]')).not.toBeNull()
})

it('switches between admin sections', async () => {
  await render('data')
  expect(host.querySelector('[data-testid="data-panel"]')).not.toBeNull()
  expect(host.querySelector('[data-testid="settings-panel"]')).toBeNull()

})
