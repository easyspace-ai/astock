import { useEffect, useMemo, useRef, useState, Suspense } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAccountUser, userHasPermission } from '@/lib/useAccount'
import { PERM } from '@/lib/permissions'
import { motion } from 'framer-motion'
import { useQuoteStream, useQuoteStreamStatus } from '@/lib/useQuoteStream'
import { ToastContainer, toast } from '@/components/Toast'
import { AlertToastContainer } from '@/components/AlertToast'
import { AiAnalysisHost } from '@/components/financials/AiAnalysisHost'
import { AiReportBubble } from '@/components/financials/AiReportBubble'
import { DailyDataHealthNotice } from '@/components/data/DailyDataHealthNotice'
import { StockAnalysisHost } from '@/components/stock-analysis/StockAnalysisHost'
import { StockPreviewHost } from '@/components/StockPreviewHost'
import { StockAnalysisBubble } from '@/components/stock-analysis/StockAnalysisBubble'
import {
  useSettings,
  usePreferences,
  useQuoteStatus,
} from '@/lib/useSharedQueries'
import {
  useToggleRealtimeQuotes,
} from '@/lib/useSharedMutations'
import { QK, MINUTE_DATA_QUERY_PREFIXES } from '@/lib/queryKeys'
import { setUiPref } from '@/lib/uiPrefs'
import { useStrategyLibrarySync } from '@/lib/strategyLibrary'
import { setKlineBatchLimits } from '@/lib/klineBatchLimits'
import { seedHiddenForNewUser } from '@/lib/navOrder'
import { navPageIds, resolveNavLayout, visibleNavLayout, type NavGroup, type NavNode } from '@/lib/navLayout'
import { getNavigationEntries } from '@/lib/navigation'
import { TopBar } from './shell/TopBar'
import { ContentPanel } from './shell/ContentPanel'
import { StatusBar } from './shell/StatusBar'
import { BrandThemePicker } from './shell/BrandThemePicker'
import { GlobalSearch } from './shell/GlobalSearch'
import { NavPanel } from './shell/NavPanel'
import { HeaderRealtime } from './shell/HeaderRealtime'
import { HeaderAccount } from './shell/HeaderAccount'
import { ThemeToggle } from './shell/ThemeToggle'
import {
  Loader2,
  X,
  WifiOff,
  Menu,
  PanelLeftOpen,
} from 'lucide-react'
import { Logo } from './Logo'
import { api } from '@/lib/api'
import { cn } from '@/lib/cn'
import { useIsDesktop } from '@/lib/useMediaQuery'
import { useDialogBackdrop } from '@/lib/useDialogBackdrop'
import { computeGroupPcts } from '@/lib/watchlistGroupStats'
import { findDataSource } from '@/lib/dataSources'
import { setCurrentTotal as setAlertTotal } from '@/lib/monitorBadge'
import { ExtensionSlot } from '@/extensions/ExtensionSlot'

// 品牌色 — 跟随品牌主题包（--accent-solid 在暗/亮与各品牌包里都有定义）
const BRAND = 'hsl(var(--accent-solid))'

// 核心四只指数 — 与后端 index_const.py 单一权威对齐 (前端展示层固定清单)
export const CORE_INDEXES = [
  { symbol: '000001.SH', name: '上证指数' },
  { symbol: '399001.SZ', name: '深证成指' },
  { symbol: '399006.SZ', name: '创业板指' },
  { symbol: '000680.SH', name: '科创综指' },
] as const

// 侧边栏桌面三态: expanded(14rem) / rail(3.5rem 图标条) / hidden(0 + 左缘悬浮按钮)。
// 移动端 (<768px) 不参与三态 — aside 以抽屉呈现 (见 Layout 内 drawerOpen)。
type NavState = 'expanded' | 'rail' | 'hidden'

export function Layout() {
  useStrategyLibrarySync()
  // ===== 共享 hooks (替代内联 useQuery) =====
  const { data: settingsState } = useSettings()
  const { data: prefs } = usePreferences()
  // 角色: 按 admin.access 权限码判断; 账号数据未就绪时先按普通用户渲染
  const account = useAccountUser()
  const isAdmin = account.isLoading ? false : userHasPermission(account.data, PERM.adminAccess)
  // 批量端点单请求上限 = 前端分块大小 (与后端校验同源, 见 lib/klineBatchLimits)。
  // 在渲染期写入而非 effect: 分块助手在子组件 queryFn 内调用, 而 queryFn 早于
  // 父组件 effect 触发 —— 用 effect 会让首轮请求仍按默认值分块。幂等, 无状态更新。
  setKlineBatchLimits({
    daily: prefs?.daily_batch_symbols,
    minute: prefs?.minute_batch_symbols,
  })
  // 数据源列表 (用于实时行情状态显示当前数据源名称)
  const { data: dataSources } = useQuery({
    queryKey: QK.dataSources,
    queryFn: api.dataSources,
    staleTime: 60_000,
  })
  // poll=true: 全局唯一开启条件轮询 (非交易时段 60s 兜底, 交易时段靠 SSE)
  const { data: quoteStatus } = useQuoteStatus({ poll: true })
  const { data: analysisMenus } = useQuery({
    queryKey: QK.analysisMenus,
    queryFn: api.analysisMenus,
  })

  // 自选分组 — 仅当用户开启「显示在侧边栏」时拉取
  const groupsInNav = prefs?.watchlist_groups_in_nav ?? false
  const location = useLocation()
  const { data: watchlistGroupsData } = useQuery({
    queryKey: QK.watchlistGroups,
    queryFn: api.watchlistGroups,
    enabled: groupsInNav,
    staleTime: 60_000,
  })
  const watchlistGroups = watchlistGroupsData?.groups ?? []
  // 自选二级菜单展开状态 — 默认当前在自选页时展开
  const [watchlistNavExpanded, setWatchlistNavExpanded] = useState(location.pathname === '/watchlist')

  const allNav = getNavigationEntries(analysisMenus?.items ?? [])
  // 页面级权限：无权限的页面不进导航（必须在套用 nav_layout 用户偏好之前过滤，
  // 否则旧布局里的页面 id 会绕过权限重新出现）。
  const canPage = (perm: string) => !account.isLoading && userHasPermission(account.data, perm)
  const permittedNav = allNav.filter(item => !item.perm || canPage(item.perm))
  const navById = new Map(allNav.map(item => [item.id, item]))
  const layout = resolveNavLayout(prefs?.nav_layout, permittedNav, prefs?.nav_order)
  const visibleLayout = visibleNavLayout(layout, permittedNav, prefs?.nav_hidden ?? [])
  // 后台功能与前台分离: 前台永远不展示配置/数据管理入口
  const ADMIN_ONLY_NAV = new Set(['/settings', '/admin', '/data', '/branding'])
  const frontItems: NavNode[] = []
  for (const node of visibleLayout.items) {
    if (node.type === 'page') {
      if (!ADMIN_ONLY_NAV.has(node.id)) frontItems.push(node)
      continue
    }
    const children = node.children.filter(child => !ADMIN_ONLY_NAV.has(child.id))
    // 原本就是空分类的保留(菜单管理里要能看见并提示), 只因后台页面被清空的分类才移除
    if (children.length > 0 || node.children.length === 0) frontItems.push({ ...node, children })
  }
  visibleLayout.items = frontItems
  const [groupOpen, setGroupOpen] = useState<Record<string, boolean>>(() => {
    try {
      const value = JSON.parse(localStorage.getItem('tf-nav-groups') || '{}')
      return value && typeof value === 'object' && !Array.isArray(value)
        ? Object.fromEntries(Object.entries(value).filter((item): item is [string, boolean] => typeof item[1] === 'boolean')) : {}
    } catch { return {} }
  })
  const groupEdited = useRef(false)
  const groupRestored = useRef(false)
  const [railGroupId, setRailGroupId] = useState<string | null>(null)
  const activeGroupId = visibleLayout.items.find(node => node.type === 'group' && node.children.some(page =>
    location.pathname === page.id || (page.id !== '/' && location.pathname.startsWith(`${page.id}/`))))?.id
  useEffect(() => {
    setRailGroupId(null)
    if (activeGroupId) setGroupOpen(previous => ({ ...previous, [activeGroupId]: true }))
  }, [location.pathname, location.search, activeGroupId])
  const savedGroupOpen = prefs?.ui_prefs?.nav_groups
  useEffect(() => {
    if (groupRestored.current || groupEdited.current || typeof savedGroupOpen !== 'string') return
    groupRestored.current = true
    try {
      const value = JSON.parse(savedGroupOpen)
      if (value && typeof value === 'object' && !Array.isArray(value)) {
        const restored = Object.fromEntries(Object.entries(value).filter((item): item is [string, boolean] => typeof item[1] === 'boolean'))
        setGroupOpen(previous => ({ ...restored, ...previous }))
      }
    } catch { /* 非法展开偏好不影响页面入口 */ }
  }, [savedGroupOpen])
  const isGroupOpen = (group: NavGroup) => groupOpen[group.id] ?? group.id === activeGroupId
  const toggleGroup = (group: NavGroup) => {
    groupEdited.current = true
    const next = { ...groupOpen, [group.id]: !isGroupOpen(group) }
    setGroupOpen(next)
    setUiPref('tf-nav-groups', JSON.stringify(next))
  }

  // 侧边栏三态 — expanded(14rem) / rail(3.5rem 图标条) / hidden(0, 左缘悬浮按钮唤出)。
  // 仅桌面 (≥768px) 参与三态; 移动端 aside 以抽屉呈现, 由 drawerOpen 控制, 恒渲染完整形态。
  // 持久化到 localStorage; 迁移旧两态键 tf-nav-collapsed (收起 → 图标条)。
  const [navState, setNavState] = useState<NavState>(() => {
    try {
      // 两态导航: expanded(14rem) / rail(3.5rem 图标条)。历史三态版本的 'hidden' 归一为展开
      const v = localStorage.getItem('tf-nav-state')
      if (v === 'expanded' || v === 'rail') return v
      return 'rail' // 交易终端默认图标条: 941px 内容宽下看板已修好(去掉组件毛玻璃 + 指标卡 3 行高)
    } catch { return 'rail' }
  })
  const [drawerOpen, setDrawerOpen] = useState(false)
  const isDesktop = useIsDesktop()
  // 桌面 hidden 态的左缘悬浮按钮: hover 1s 以 overlay 预览 (不挤压主区), 点击固定展开 (push)
  const [overlayPreview, setOverlayPreview] = useState(false)
  const overlayTimer = useRef<number | undefined>(undefined)
  const setNavStatePersist = (s: NavState) => {
    setNavState(s)
    try { setUiPref('tf-nav-state', s) } catch {}
  }
  // 图标条形态仅桌面 rail 态成立 (移动端抽屉与 overlay 预览恒为完整形态)
  const railMode = isDesktop && navState === 'rail'
  // 路由跳转/切回桌面时关抽屉; ESC 同样关闭
  useEffect(() => { setDrawerOpen(false) }, [location.pathname, location.search, isDesktop])
  useEffect(() => {
    if (!drawerOpen) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setDrawerOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [drawerOpen])

  // 分组等权平均涨跌幅 — 复用 watchlist/enriched 查询缓存(与自选页同 key,
  // 盘中随 SSE 刷新)。可见性门控: 子菜单实际可见(桌面展开 + 二级菜单展开, 或
  // 移动端抽屉打开) 时才拉取, 收起/隐藏/抽屉关闭下不为隐藏 UI 发请求。
  const sidebarFullyVisible = isDesktop ? navState === 'expanded' : drawerOpen
  const watchlistParent = visibleLayout.items.find(node => node.type === 'group' && navPageIds(node).includes('/watchlist'))
  const watchlistVisible = visibleLayout.items.some(node => navPageIds(node).includes('/watchlist'))
  const watchlistParentOpen = !watchlistParent || (watchlistParent.type === 'group' &&
    (railMode ? railGroupId === watchlistParent.id : isGroupOpen(watchlistParent)))
  // 图标模式仅在包含自选的分类弹层中显示分组统计。
  const navGroupPctVisible = groupsInNav && watchlistVisible && watchlistParentOpen
    && (sidebarFullyVisible || (railMode && railGroupId === watchlistParent?.id)) && watchlistNavExpanded
  const { data: navWatchlist } = useQuery({
    queryKey: QK.watchlist,
    queryFn: api.watchlistList,
    enabled: navGroupPctVisible,
    staleTime: 60_000,
  })
  const { data: navEnriched } = useQuery({
    // 无参 → key 归一为 ['watchlist-enriched', ''], 与自选页 (无 ext 列时) 共享缓存,
    // 消除侧栏与自选页各拉一份整表的双份内存
    queryKey: QK.watchlistEnriched(),
    queryFn: () => api.watchlistEnriched(),
    enabled: navGroupPctVisible,
    staleTime: 60_000,
  })
  const navGroupPcts = useMemo(
    () => computeGroupPcts(
      navWatchlist?.symbols ?? [],
      new Map((navEnriched?.rows ?? []).map((r: any) => [r.symbol as string, r])),
    ),
    [navWatchlist, navEnriched],
  )

  // 数据同步状态轮询: 有活跃 job 时「数据」菜单项显示转圈。
  // 标签页隐藏时 TanStack 默认暂停轮询 — 转圈提示只在页面可见时有意义, 不再强制后台轮询
  const { data: pipelineJobs } = useQuery({
    queryKey: QK.pipelineJobs,
    queryFn: () => api.pipelineJobs(1),
    refetchInterval: (query) => (query.state.data?.active_id ? 2000 : 15000),
  })
  const isDataSyncing = !!pipelineJobs?.active_id

  // 数据同步完成的"瞬时反馈": isDataSyncing 从 true→false 时显示绿色对勾,
  // 闪烁约 3 秒后自动消失。
  const [dataSyncJustDone, setDataSyncJustDone] = useState(false)
  const prevSyncingRef = useRef(false)
  useEffect(() => {
    // 仅在"刚结束"(true→false)且非首次挂载时触发
    if (prevSyncingRef.current && !isDataSyncing) {
      setDataSyncJustDone(true)
      const t = setTimeout(() => setDataSyncJustDone(false), 3000)
      prevSyncingRef.current = isDataSyncing
      return () => clearTimeout(t)
    }
    prevSyncingRef.current = isDataSyncing
  }, [isDataSyncing])

  const qc = useQueryClient()
  const latestDataJob = pipelineJobs?.jobs?.[0]
  const refreshedMinuteJob = useRef<string | null>(null)
  useEffect(() => {
    if (!latestDataJob || !['succeeded', 'failed'].includes(latestDataJob.status)) return
    if (latestDataJob.result?.minute_rows == null && !['sync_minute', 'extend_minute'].includes(latestDataJob.stage)) return
    if (refreshedMinuteJob.current === latestDataJob.id) return
    refreshedMinuteJob.current = latestDataJob.id
    // 复用常驻的任务轮询, 离开数据页后补拉结束也会刷新; 取消/失败可能已写入部分数据。
    // 清掉列表增量游标，让下一次查询也能读回上次末根之前补入的早盘。
    qc.resetQueries({ queryKey: QK.minuteBatch(), exact: true })
    qc.invalidateQueries({ predicate: q => q.queryKey[0] !== QK.minuteBatch()[0] && MINUTE_DATA_QUERY_PREFIXES.has(String(q.queryKey[0])) })
    qc.invalidateQueries({ queryKey: QK.dataStatus })
  }, [latestDataJob?.id, latestDataJob?.status, qc])
  const realtimeEnabled = prefs?.realtime_quotes_enabled ?? false
  // 开启实时行情时若存在排队中的挖掘任务 → 确认弹窗 (实时落盘会让排队任务开跑即失败)
  const [miningQueuedWarning, setMiningQueuedWarning] = useState<number | null>(null)
  const miningWarnBackdrop = useDialogBackdrop(() => setMiningQueuedWarning(null))
  // 两态切换 (仅桌面): 展开 <-> 图标条
  const toggleNavCollapsed = () => {
    setNavStatePersist(navState === 'expanded' ? 'rail' : 'expanded')
  }
  // 指数条: 固定核心四只 (产品契约, 不再可配置), 常驻显示
  const sidebarIndexes = CORE_INDEXES
  const { data: sidebarIndexQuotes } = useQuery({
    queryKey: [...QK.indexQuotes, 'sidebar', 'core'] as const,
    queryFn: () => api.indexQuotes(sidebarIndexes.map(p => p.symbol)),
    enabled: sidebarIndexes.length > 0,
    placeholderData: (prev) => prev,
  })

  // SSE: 行情更新时自动刷新相关 queries + 告警通知
  useQuoteStream(realtimeEnabled, prefs?.sse_refresh_pages)
  // 实时 SSE 连接状态 — 断开时底部显示提示, 提示可能漏策略告警
  const streamStatus = useQuoteStreamStatus()

  const toggleQuote = useToggleRealtimeQuotes()
  const isRunning = quoteStatus?.running ?? false
  const isTrading = quoteStatus?.is_trading_hours ?? false
  // 管道/数据修正运行期间实时行情被临时暂停 — 此时禁止开启
  const isPaused = quoteStatus?.paused ?? false
  // 实时模式以 quote_status 为准 (数据源无关): none=不可用 / watchlist=自选实时 / full_market=全市场
  const quoteMode = quoteStatus?.mode ?? 'none'
  const realtimeUnavailable = quoteMode === 'none'
  const isWatchlistMode = quoteMode === 'watchlist'
  const realtimeModeLabel = isWatchlistMode ? '自选' : '全市场'
  // 当前实时行情数据源名称 (插件/自定义源显示源名, tickflow 不显示)
  const realtimeProvider = prefs?.realtime_data_provider
  const realtimeProviderName = realtimeProvider && realtimeProvider !== 'tickflow'
    ? (findDataSource(dataSources, realtimeProvider)?.display_name || realtimeProvider)
    : null
  const realtimeToggleDisabled = toggleQuote.isPending || isPaused
  const realtimeActive = realtimeEnabled && isRunning && isTrading
  const realtimeStatusLabel = toggleQuote.isPending
    ? '正在更新'
    : isPaused
      ? '同步期间暂停'
      : realtimeActive
        ? '运行中'
        : realtimeEnabled
          ? (isTrading ? '正在连接' : '等待交易时段')
          : '已关闭'
  const realtimeStatusClass = realtimeActive
    ? 'text-accent'
    : realtimeEnabled || isPaused
      ? 'text-warning/80'
      : 'text-muted'
  const realtimeIndicatorClass = realtimeActive
    ? 'bg-accent animate-pulse'
    : realtimeEnabled || isPaused
      ? 'bg-warning/70'
      : 'bg-muted'
  const realtimeToggleTitle = isPaused
    ? '数据同步运行中，实时行情已临时暂停'
    : toggleQuote.isPending
      ? '正在更新实时行情设置'
      : realtimeEnabled
        ? '关闭实时行情'
        : '开启实时行情'

  // 轮询触发记录总数 → 更新监控中心徽标 (每 15 秒; 后台标签页由 SSE 事件驱动, 不轮询)。
  // 非交易时段无新告警, 降为 2 分钟兜底
  const alertsTotalQuery = useQuery({
    queryKey: ['alerts-total'],
    queryFn: () => api.alertsList({ days: 7, limit: 1 }),
    refetchInterval: () => (isTrading ? 15000 : 120000),
    select: (data) => data.total,
  })
  // 只在拿到真实总数时同步徽标 (避免 data=undefined 时传 0 重置 lastSeen)
  const alertsTotal = alertsTotalQuery.data
  useEffect(() => {
    if (alertsTotal != null) setAlertTotal(alertsTotal)
  }, [alertsTotal])

  // 新装用户一次性种子: 扩展域页面默认隐藏, 菜单设置里可逐个开启。
  // 老用户 (升级前已完成 onboarding) 只写标记不动偏好, 升级前后侧栏一致。
  // 种子标记存 localStorage (每设备一次, 保存成功后才写); 隐藏偏好本体持久化在后端 nav_hidden。
  const extSeedRef = useRef(false)
  useEffect(() => {
    if (!prefs || !settingsState || extSeedRef.current) return
    let seeded = false
    try { seeded = localStorage.getItem('tf-ext-pages-seed') === '1' } catch { return }
    if (seeded) { extSeedRef.current = true; return }
    const next = seedHiddenForNewUser(settingsState.onboarding_completed, prefs.nav_hidden)
    if (!next) {
      extSeedRef.current = true
      try { localStorage.setItem('tf-ext-pages-seed', '1') } catch {}
      return
    }
    extSeedRef.current = true
    api.saveNavHidden(next)
      .then(() => { try { localStorage.setItem('tf-ext-pages-seed', '1') } catch {} })
      .then(() => qc.invalidateQueries({ queryKey: QK.preferences }))
      .catch(() => { /* 保存失败不写标记, 下次启动重试; 结果集幂等, 重复触发无害 */ })
  }, [prefs, settingsState, qc])

  const doEnableRealtime = async () => {
    await toggleQuote.mutateAsync(true)
    // 仅在交易时段立即获取一次行情
    if (isTrading) {
      api.intradayRefresh().catch(() => {})
    }
  }

  const handleToggle = async (enabled: boolean) => {
    // 开启时重新校验实时权限 (以 quote_status 的数据源无关判定为准)
    if (!enabled) {
      await toggleQuote.mutateAsync(false)
      return
    }
    const fresh = await qc.fetchQuery({
      queryKey: QK.quoteStatus,
      queryFn: api.quoteStatus,
      // 刚切换到 fuyao 等源时, 全局 staleTime 内仍可能缓存旧源的权限。
      staleTime: 0,
    })
    if (!fresh.realtime_allowed) {
      toast('当前数据源无实时行情能力, 请先配置数据源', 'error')
      return
    }
    // 有排队中的挖掘任务时确认: 实时落盘会让排队任务开跑即失败
    // (data generation changed); 运行中的任务会自动跟随新数据, 不受影响。
    try {
      const runs = await qc.fetchQuery({
        queryKey: QK.miningRuns,
        queryFn: api.miningRuns,
        staleTime: 5_000,
      })
      const queued = (runs?.items ?? []).filter(r => r.status === 'queued').length
      if (queued > 0) {
        setMiningQueuedWarning(queued)
        return
      }
    } catch {
      // 挖掘运行历史查询失败不阻塞开关实时行情
    }
    await doEnableRealtime()
  }

  // 顶栏导航开关：移动端开抽屉；桌面在 展开/图标条 之间切换
  const handleTopNavToggle = () => {
    if (!isDesktop) { setDrawerOpen(true); return }
    if (navState === 'hidden') { setNavStatePersist('expanded'); return }
    toggleNavCollapsed()
  }

  return (
    <div className="flex h-full flex-col overflow-hidden bg-base text-foreground">
      <TopBar
        isDesktop={isDesktop}
        navRail={railMode}
        navTitle={isDesktop ? (navState === 'rail' ? '展开菜单' : '收起菜单') : '打开菜单'}
        onToggleNav={handleTopNavToggle}
        brand={(
          <span className="flex shrink-0 items-center gap-2 pl-0.5 pr-1">
            <Logo size={18} className="shrink-0" style={{ color: BRAND }} />
            <span className="hidden whitespace-nowrap text-[11px] font-semibold tracking-[0.02em] text-foreground/80 md:inline">
              财富引擎
            </span>
          </span>
        )}
        center={(
          <div className="hidden w-full max-w-[20rem] justify-center md:flex">
            <GlobalSearch />
          </div>
        )}
        actions={(
          <>
            {/* v1 插槽 layout.header.quick 从侧栏上移到顶栏，扩展注册 key 不变 */}
            <div className="flex shrink-0 items-stretch gap-1">
              <ExtensionSlot name="layout.header.quick" context={{}} compact />
            </div>
            {isDataSyncing && (
              <NavLink
                to="/data"
                title="数据同步中"
                className="flex h-7 w-7 items-center justify-center rounded-control text-accent transition-colors duration-150 hover:bg-elevated/70"
              >
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              </NavLink>
            )}
            {/* L2 顶栏动作插槽：扩展注册图标按钮，核心动作固定在右侧 */}
            <div className="flex shrink-0 items-stretch gap-1">
              <ExtensionSlot name="topbar.actions" context={{}} compact />
            </div>
            <HeaderRealtime
              isAdmin={isAdmin}
              pending={toggleQuote.isPending}
              disabled={realtimeToggleDisabled}
              title={realtimeToggleTitle}
              indicatorClass={realtimeIndicatorClass}
              statusClass={realtimeStatusClass}
              statusLabel={realtimeStatusLabel}
              unavailable={realtimeUnavailable}
              providerName={realtimeProviderName}
              modeLabel={realtimeModeLabel}
              isPaused={isPaused}
              onToggle={() => handleToggle(!realtimeEnabled)}
            />
            <BrandThemePicker />
            <ThemeToggle />
            <HeaderAccount isAdmin={isAdmin} />
          </>
        )}
      />
      <div
        className="grid min-h-0 flex-1 gap-1.5 p-1.5 pt-0 transition-[grid-template-columns] duration-200 ease-smooth"
        style={{ gridTemplateColumns: isDesktop && !overlayPreview ? (navState === 'expanded' ? '14rem 1fr' : navState === 'rail' ? '3.5rem 1fr' : '0 1fr') : '1fr' }}
      >
      {/* 移动端抽屉遮罩 */}
      {!isDesktop && drawerOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50"
          onClick={() => setDrawerOpen(false)}
          aria-hidden="true"
        />
      )}
      {/* 移动端汉堡按钮 / 桌面 hidden 态左缘悬浮按钮 (hover 1s overlay 预览, 点击固定展开) */}
      {(!isDesktop || navState === 'hidden') && !overlayPreview && (
        <button
          onClick={() => {
            window.clearTimeout(overlayTimer.current)
            setOverlayPreview(false)
            if (isDesktop) setNavStatePersist('expanded')
            else setDrawerOpen(true)
          }}
          onMouseEnter={() => {
            if (!isDesktop) return
            window.clearTimeout(overlayTimer.current)
            overlayTimer.current = window.setTimeout(() => setOverlayPreview(true), 1000)
          }}
          onMouseLeave={() => window.clearTimeout(overlayTimer.current)}
          className={cn(
            'fixed z-30 rounded-btn border border-border bg-surface/90 text-muted shadow-lg backdrop-blur-sm',
            'hover:text-foreground hover:bg-elevated transition-colors duration-150 ease-smooth',
            isDesktop ? 'left-1.5 top-1/2 -translate-y-1/2 p-2' : 'left-3 top-3 p-2',
          )}
          title={isDesktop ? '展开菜单' : '打开菜单'}
        >
          {isDesktop
            ? <PanelLeftOpen className="h-4 w-4 shrink-0" />
            : <Menu className="h-4 w-4 shrink-0" />}
        </button>
      )}
      {/* 抽屉位移用内联 transform, 不走 translate-x-* 工具类:
          同一元素上 fixed/transition 与位移类并存时, 计算值可能仍停在 -100%(面板滑不进来, 只见遮罩)。 */}
      <aside
        onMouseLeave={() => { if (overlayPreview) setOverlayPreview(false) }}
        style={(!isDesktop || overlayPreview)
          ? { transform: (drawerOpen || overlayPreview) ? 'translateX(0)' : 'translateX(-100%)' }
          : undefined}
        className={cn(
          'flex min-h-0 flex-col overflow-hidden',
          isDesktop
            ? 'h-full bg-transparent'
            : cn(
                'fixed inset-y-0 left-0 z-[60] w-[80vw] max-w-[320px] border-r border-border bg-surface shadow-2xl',
                'transition-transform duration-200 ease-smooth',
              ),
          overlayPreview && 'fixed inset-y-0 left-0 z-50 w-56 border-r border-border bg-surface shadow-2xl',
        )}
      >
        {/* 品牌已上移到顶栏；侧栏只剩导航。移动端抽屉保留一个关闭按钮 */}
        {!isDesktop && (
          <div className="flex shrink-0 justify-end px-2 pt-1.5">
            <button
              onClick={() => setDrawerOpen(false)}
              className="flex shrink-0 items-center rounded-control p-1.5 text-muted transition-colors duration-150 hover:bg-elevated/70 hover:text-foreground"
              title="关闭菜单"
            >
              <X className="h-4 w-4 shrink-0" />
            </button>
          </div>
        )}

        <nav className="tf-nav-scroll flex-1 min-h-0 overflow-y-auto px-2 pb-3 pt-2 space-y-0.5">
          {visibleLayout.items.length === 0 && (
            <div className="px-2 py-4 text-[11px] leading-5 text-muted">
              当前账号没有被分配任何页面权限，请联系超级管理员调整权限组。
            </div>
          )}
          <NavPanel
            items={visibleLayout.items}
            navById={navById}
            pathname={location.pathname}
            search={location.search}
            railMode={railMode}
            activeGroupId={activeGroupId}
            isGroupOpen={isGroupOpen}
            onToggleGroup={toggleGroup}
            railGroupId={railGroupId}
            onRailGroupChange={setRailGroupId}
            isDataSyncing={isDataSyncing}
            dataSyncJustDone={dataSyncJustDone}
            groupsInNav={groupsInNav}
            watchlistGroups={watchlistGroups}
            watchlistNavExpanded={watchlistNavExpanded}
            onToggleWatchlist={() => setWatchlistNavExpanded(v => !v)}
            navGroupPcts={navGroupPcts}
          />
        </nav>

      </aside>

      <ContentPanel>
        <motion.main
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
          className="min-h-0 flex-1 overflow-auto scrollbar-gutter-stable"
        >
          {streamStatus === 'reconnecting' && (
            <div
              role="status"
              aria-live="polite"
              className="fixed bottom-4 left-1/2 z-[9998] flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-warning/30 bg-warning/10 px-2.5 py-1 text-[11px] font-medium text-warning shadow-lg backdrop-blur-md"
            >
              <WifiOff className="h-3 w-3 shrink-0 animate-pulse" />
              与服务连接已断开 · 正在重连
            </div>
          )}
          <DailyDataHealthNotice />
          <Suspense
            fallback={
              <div className="flex items-center justify-center py-24">
                <Loader2 className="h-5 w-5 animate-spin text-muted" />
              </div>
            }
          >
            <Outlet />
          </Suspense>
        </motion.main>
      </ContentPanel>
      <ToastContainer />
      <AlertToastContainer />
      <AiAnalysisHost />
      <AiReportBubble />
      <StockAnalysisHost />
      <StockAnalysisBubble />
      <StockPreviewHost />

      {/* 开启实时行情 + 排队中的挖掘任务 → 冲突确认 */}
      {miningQueuedWarning != null && (
        <div {...miningWarnBackdrop} className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-4">
          <div onClick={e => e.stopPropagation()} className="w-full max-w-sm rounded-2xl border border-border bg-surface p-5 shadow-2xl">
            <div className="text-sm font-semibold text-foreground">
              有挖掘任务正在排队
            </div>
            <p className="mt-2 text-xs leading-5 text-secondary">
              当前有 {miningQueuedWarning} 个挖掘任务排队等待执行。开启实时行情后盘中数据会持续落盘，
              排队中的任务启动时可能因数据更新校验而失败（需重新开始挖掘）；已开始运行的任务不受影响。
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" onClick={() => setMiningQueuedWarning(null)} className="h-8 rounded-btn border border-border px-3 text-xs text-secondary hover:bg-elevated">取消</button>
              <button type="button" onClick={() => { setMiningQueuedWarning(null); void doEnableRealtime() }} className="h-8 rounded-btn bg-accent px-3 text-xs font-semibold text-white hover:opacity-90">仍要开启</button>
            </div>
          </div>
        </div>
      )}
      </div>
      {/* 底部状态栏：指数 + 实时行情 + 数据源 + 同步态 */}
      <StatusBar
        rows={sidebarIndexQuotes?.rows}
        items={sidebarIndexes}
        realtime={realtimeActive ? 'active' : isPaused ? 'paused' : 'off'}
        realtimeLabel={realtimeActive ? realtimeModeLabel : isPaused ? '已暂停' : '未开启'}
        sourceName={realtimeProviderName}
        syncing={isDataSyncing}
      />
    </div>
  )
}
