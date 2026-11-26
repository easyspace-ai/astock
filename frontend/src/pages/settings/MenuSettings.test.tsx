// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, type MenuNode, type Preferences } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { SettingsMenuSettingsPanel } from './MenuSettings'

// 后台菜单树是页面设置的唯一来源; 编辑器只渲染已注册页面。
vi.mock('@/extensions/registry', () => ({ getFrontendExtensionNavigation: () => [] }))
vi.mock('@/lib/useSharedQueries', () => ({
  usePreferences: () => useQuery({ queryKey: QK.preferences, queryFn: api.preferences }),
}))
vi.mock('@/lib/useAccount', () => ({
  usePermissions: () => ({
    loading: false, user: null, codes: [], can: () => true, canAny: () => true,
    isSuperAdmin: false, canAdmin: true,
  }),
}))

const PREFS = { nav_order: [], nav_hidden: [], nav_layout: null } as unknown as Preferences

const PAGE_MENUS: MenuNode[] = [
  { key: 'page.dashboard', label: '看板', group: 'page', kind: 'permission', description: '', route: '/', order: 5 },
  { key: 'page.watchlist', label: '自选', group: 'page', kind: 'permission', description: '', route: '/watchlist', order: 20 },
  { key: 'page.screener', label: '策略', group: 'page', kind: 'permission', description: '', route: '/screener', order: 30 },
  { key: 'page.indices', label: '指数', group: 'page', kind: 'permission', description: '', route: '/indices', order: 100 },
]

let root: Root
let host: HTMLDivElement
let client: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div'); document.body.append(host)
  root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  vi.spyOn(api, 'preferences').mockResolvedValue(PREFS)
  vi.spyOn(api, 'navigationTree').mockResolvedValue({ menus: PAGE_MENUS })
  vi.spyOn(api, 'analysisMenus').mockResolvedValue({ items: [] })
  vi.spyOn(api, 'saveNavigationLayout').mockResolvedValue({ menus: PAGE_MENUS })
  vi.spyOn(api, 'saveNavHidden').mockImplementation(async nav_hidden => ({ nav_hidden }))
})
afterEach(async () => {
  await act(async () => root.unmount())
  client.clear(); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals()
})
async function mount(menus: MenuNode[] = PAGE_MENUS) {
  // 预置缓存: 避免测试命中 busy(未加载) 分支, 挂载即得到可编辑的布局。
  client.setQueryData(QK.preferences, PREFS)
  client.setQueryData(QK.navigationTree, { menus })
  client.setQueryData(QK.analysisMenus, { items: [] })
  await act(async () => root.render(<MemoryRouter><QueryClientProvider client={client}>
    <SettingsMenuSettingsPanel />
  </QueryClientProvider></MemoryRouter>))
}
async function click(text: string) {
  const button = [...host.querySelectorAll('button')].find(x => x.textContent?.trim() === text)!
  expect(button).toBeTruthy()
  await act(async () => button.click())
}
async function fill(label: string, text: string) {
  const input = host.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)
  expect(input).toBeInstanceOf(HTMLInputElement)
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, text)
    input!.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
async function createGroup() {
  await fill('新分类名称', '交易工作台'); await click('新建分类')
  return host.querySelector<HTMLSelectElement>('select[aria-label="移动自选到"]')!.options[1].value
}
async function moveWatchlist(group: string) {
  const select = host.querySelector<HTMLSelectElement>('select[aria-label="移动自选到"]')!
  await act(async () => { select.value = group; select.dispatchEvent(new Event('change', { bubbles: true })) })
}

it('pins home and atomically saves a new group, child move, and visibility', async () => {
  await mount()
  expect(host.querySelector<HTMLButtonElement>('button[aria-label="拖动看板"]')?.disabled).toBe(true)
  expect(host.querySelector('select[aria-label="移动看板到"]')).toBeNull()
  const group = await createGroup(); await moveWatchlist(group)
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="隐藏指数"]')!.click())
  await click('保存菜单设置')
  expect(api.saveNavigationLayout).toHaveBeenCalledOnce()
  const saved = vi.mocked(api.saveNavigationLayout).mock.calls[0][0]!
  expect(saved.items[0]).toEqual({ type: 'page', id: '/' })
  expect(saved.items.find(x => x.id === group)).toMatchObject({
    label: '交易工作台', children: [{ type: 'page', id: '/watchlist' }],
  })
  expect(saved.items.filter(x => x.id === '/watchlist')).toHaveLength(0)
  // 隐藏是个人偏好, 单独走 nav-hidden, 不混进全局菜单结构。
  expect(vi.mocked(api.saveNavHidden).mock.calls[0][0]).toEqual(['/indices'])
})

it('renders a drop target right after the home entry for placing the second menu', async () => {
  await mount()
  const homeRow = host.querySelector<HTMLElement>('[data-nav-id="/"]')!
  expect(homeRow.nextElementSibling?.getAttribute('data-drop-id')).toBe('nav:home-after')
})

it('creates the category directly below home and focuses it instead of appending it offscreen', async () => {
  await mount()
  const group = await createGroup()
  expect([...host.querySelectorAll<HTMLElement>('[data-nav-id]')].slice(0, 2).map(x => x.dataset.navId))
    .toEqual(['/', group])
  expect(document.activeElement).toBe(host.querySelector('input[aria-label="分类名称：交易工作台"]'))
})

it('dissolves a group without deleting child pages', async () => {
  await mount(); const group = await createGroup(); await moveWatchlist(group)
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="解散交易工作台"]')!.click())
  await click('保存菜单设置')
  const saved = vi.mocked(api.saveNavigationLayout).mock.calls[0][0]!
  expect(saved.items.some(x => x.type === 'group')).toBe(false)
  expect(saved.items.some(x => x.id === '/watchlist')).toBe(true)
})

it('retains the draft after a failed save and permits retry', async () => {
  vi.mocked(api.saveNavigationLayout).mockRejectedValueOnce(new Error('保存失败，请重试'))
  await mount(); const group = await createGroup(); await moveWatchlist(group)
  await click('保存菜单设置')
  expect(host.querySelector('[role=alert]')?.textContent).toContain('保存失败')
  expect(host.querySelector<HTMLInputElement>('input[aria-label="分类名称：交易工作台"]')?.value).toBe('交易工作台')
  await click('保存菜单设置')
  expect(api.saveNavigationLayout).toHaveBeenCalledTimes(2)
})

it('does not render or submit routes missing from the frontend registry', async () => {
  // 扩展页前端产物缺失时, 后端树里仍有该节点, 但前端无法渲染它。
  // 编辑器不画它、也不提交它 —— 后端 save_layout 会保留未提交页面的旧归属。
  const menus: MenuNode[] = [...PAGE_MENUS,
    { key: 'menu.custom.offline', label: '外部研究', group: 'page', kind: 'group', description: '', order: 50 },
    { key: 'page.ext.route.offline', label: '离线页', group: 'page', kind: 'permission', description: '', route: '/offline', parent: 'menu.custom.offline' },
  ]
  await mount(menus)
  expect(host.querySelector('[data-nav-id="/offline"]')).toBeNull()
  expect(host.textContent).toContain('外部研究')
  await createGroup(); await click('保存菜单设置')
  expect(JSON.stringify(vi.mocked(api.saveNavigationLayout).mock.calls[0][0]?.items)).not.toContain('/offline')
})

it('creates groups on HTTP installations without crypto.randomUUID', async () => {
  vi.stubGlobal('crypto', {})
  await mount()
  expect(await createGroup()).toMatch(/^group:[a-zA-Z0-9_-]+$/)
})
