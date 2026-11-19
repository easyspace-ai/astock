// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AdminGuard } from '@/router'

// 守卫只依赖会话状态; 这里直接控制三种情况: 未登录 / 普通用户 / 管理员
const sessionState = vi.hoisted(() => ({
  current: { loading: false, authenticated: false, isAdmin: false, user: null as null | { role: string } },
}))

vi.mock('@/lib/useAccount', () => ({
  useSession: () => sessionState.current,
  useAccountUser: () => ({ data: null, isLoading: false }),
}))
vi.mock('@/lib/useSharedQueries', () => ({ useSettings: () => ({ data: { onboarding_completed: true }, isLoading: false }) }))
vi.mock('@/extensions/registry', () => ({
  finalizeFrontendExtensions: () => {},
  getFrontendExtensionLoadErrors: () => [],
  getFrontendExtensionRoutes: () => [],
}))

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

async function render() {
  await act(async () => root.render(
    <MemoryRouter initialEntries={['/admin?tab=ai']}>
      <Routes>
        <Route path="/admin" element={<AdminGuard><div data-testid="admin-body" /></AdminGuard>} />
        <Route path="/login" element={<div data-testid="login-page" />} />
        <Route path="/" element={<div data-testid="home" />} />
      </Routes>
    </MemoryRouter>,
  ))
}

it('未登录时把 /admin 送去登录页, 不渲染后台内容', async () => {
  sessionState.current = { loading: false, authenticated: false, isAdmin: false, user: null }
  await render()
  expect(host.querySelector('[data-testid="admin-body"]')).toBeNull()
  expect(host.querySelector('[data-testid="login-page"]')).not.toBeNull()
})

it('已登录但不是管理员时退回看板', async () => {
  sessionState.current = { loading: false, authenticated: true, isAdmin: false, user: { role: 'free' } }
  await render()
  expect(host.querySelector('[data-testid="admin-body"]')).toBeNull()
  expect(host.querySelector('[data-testid="home"]')).not.toBeNull()
})

it('管理员登录后才渲染后台内容', async () => {
  sessionState.current = { loading: false, authenticated: true, isAdmin: true, user: { role: 'admin' } }
  await render()
  expect(host.querySelector('[data-testid="admin-body"]')).not.toBeNull()
  expect(host.querySelector('[data-testid="login-page"]')).toBeNull()
})

it('会话状态加载中不提前放行', async () => {
  sessionState.current = { loading: true, authenticated: false, isAdmin: false, user: null }
  await render()
  expect(host.querySelector('[data-testid="admin-body"]')).toBeNull()
  expect(host.querySelector('[data-testid="login-page"]')).toBeNull()
})
