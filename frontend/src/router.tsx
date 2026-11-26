import { lazy, Suspense } from 'react'
import { createBrowserRouter, Navigate, useLocation, useSearchParams } from 'react-router-dom'
import { Layout } from './components/Layout'
import { NoPermission } from './components/NoPermission'
import { useSettings } from './lib/useSharedQueries'
import { useSession, usePermissions } from './lib/useAccount'
import { PERM, permissionForRoute } from './lib/permissions'
import { Logo } from './components/Logo'
import { ExtensionBoundary } from './extensions/ExtensionBoundary'
import {
  finalizeFrontendExtensions,
  getFrontendExtensionLoadErrors,
  getFrontendExtensionRoutes,
} from './extensions/registry'

// 代码分割: 页面全部 lazy 加载, 避免首屏打包所有页面 (ECharts / lightweight-charts /
// framer-motion 等重库) → 大幅减小首屏 bundle。命名导出用 .then 映射为 default。
// Layout / Onboarding / Auth 为应用外壳与入口, 保持同步加载。
const Watchlist = lazy(() => import('./pages/Watchlist').then(m => ({ default: m.Watchlist })))
const Screener = lazy(() => import('./pages/Screener').then(m => ({ default: m.Screener })))
const Backtest = lazy(() => import('./pages/Backtest').then(m => ({ default: m.Backtest })))
const Factors = lazy(() => import('./pages/Factors').then(m => ({ default: m.Factors })))
const Growth = lazy(() => import('./pages/Growth').then(m => ({ default: m.Growth })))
const StockAnalysis = lazy(() => import('./pages/StockAnalysis').then(m => ({ default: m.StockAnalysis })))
const Dashboard = lazy(() => import('./pages/Dashboard').then(m => ({ default: m.Dashboard })))
const AnalysisDetail = lazy(() => import('./pages/AnalysisDetail').then(m => ({ default: m.AnalysisDetail })))
const Indices = lazy(() => import('./pages/Indices').then(m => ({ default: m.Indices })))
const PersonalSettings = lazy(() => import('./pages/PersonalSettings').then(m => ({ default: m.PersonalSettings })))
const AdminApp = lazy(() => import('./pages/AdminApp').then(m => ({ default: m.AdminApp })))
const Regime = lazy(() => import('./pages/Regime').then(m => ({ default: m.Regime })))
const Dev = lazy(() => import('./pages/Dev').then(m => ({ default: m.Dev })))
// Onboarding 只在首次使用时进入, Auth 只在登录页用到 — 均改按需加载
const Onboarding = lazy(() => import('./pages/Onboarding').then(m => ({ default: m.Onboarding })))
const Auth = lazy(() => import('./pages/Auth').then(m => ({ default: m.Auth })))

const CORE_ROUTE_PATHS = new Set([
  '/',
  '/onboarding',
  '/login',
  '/overview',
  '/analysis',
  '/analysis/:menuId',
  '/watchlist',
  '/screener',
  '/backtest',
  '/factors',
  '/growth',
  '/stock-analysis',
  '/mining',
  '/data',
  '/indices',
  '/regime',
  '/settings',
  '/admin',
  '/admin/permissions',
  '/dev',
  '/settings/keys',
  '/settings/ai',
  '/settings/queries',
])

finalizeFrontendExtensions(CORE_ROUTE_PATHS)
const frontendExtensionRoutes = getFrontendExtensionRoutes()
const frontendExtensionErrors = getFrontendExtensionLoadErrors()
if (frontendExtensionErrors.length > 0) {
  console.error('部分前端扩展加载失败', frontendExtensionErrors)
}

// 旧链接兼容: 挖掘已并入因子页 (/factors?tab=mining), 保留 run/candidate 等参数重定向
function MiningRedirect() {
  const [searchParams] = useSearchParams()
  const search = searchParams.toString()
  return <Navigate to={`/factors?tab=mining${search ? `&${search}` : ''}`} replace />
}

// 首次使用守卫 —— 仅对「管理员」生效: 未完成向导则重定向到 /onboarding。
// 向导内容(数据源/API Key)属于管理员初始化环境; 普通账号首次登录直接进看板。
// 只挂在根路由上;/onboarding 本身不被守卫,避免循环重定向。
// settings 由 Layout 预取,守卫判定不产生额外请求。
function OnboardingGuard({ children }: { children: React.ReactNode }) {
  const settings = useSettings()
  const session = useSession()
  const location = useLocation()

  // 仅首次加载(本地无缓存)时显示占位。
  // 后台重取 (isFetching) 时本地已有上一份缓存可用, 直接放行, 避免切页时整屏 logo 闪烁。
  // 防误重定向已由 Onboarding/AI 等处 invalidate 前的 setQueryData 同步缓存兜底。
  if (settings.isLoading || session.loading) {
    return (
      <div className="min-h-full bg-base grid place-items-center">
        <div className="flex flex-col items-center gap-3 text-muted">
          <Logo size={28} className="text-foreground" />
          <div className="text-xs">加载中…</div>
        </div>
      </div>
    )
  }

  // 未登录: 不进入业务界面, 先去登录
  if (!session.authenticated) {
    const redirect = `${location.pathname}${location.search}`
    return <Navigate to={`/login?redirect=${encodeURIComponent(redirect)}`} replace />
  }

  // 非管理员: 直接放行进看板, 不展示数据源配置向导
  if (!session.isAdmin) return <>{children}</>

  // 管理员且未完成首次配置: 进向导
  if (settings.data && settings.data.onboarding_completed === false) {
    return <Navigate to="/onboarding" replace />
  }

  return <>{children}</>
}

// 旧设置页链接 → 独立管理后台, 保留 tab/highlight 等查询参数
function SettingsRedirect() {
  const [searchParams] = useSearchParams()
  const search = searchParams.toString()
  return <Navigate to={`/admin${search ? `?${search}` : ''}`} replace />
}

// 管理后台守卫 — 先校验登录态, 再校验 admin.access。
// 未登录(无邮箱账号会话)直接去登录页;
// 已登录但没有后台权限的账号展示「无权限」态, 不静默跳走。
export function AdminGuard({ children }: { children: React.ReactNode }) {
  const session = useSession()
  const location = useLocation()
  if (session.loading) {
    return (
      <div className="min-h-full bg-base grid place-items-center">
        <Logo size={28} className="text-foreground" />
      </div>
    )
  }
  if (!session.authenticated) {
    const redirect = `${location.pathname}${location.search}`
    return <Navigate to={`/login?redirect=${encodeURIComponent(redirect)}`} replace />
  }
  if (!session.isAdmin) return <NoPermission title="无后台管理权限" />
  return <>{children}</>
}

/**
 * 页面级权限守卫（前台路由，登录态由 Layout 的 OnboardingGuard 保证）。
 * 没有对应权限码时渲染「无权限」页，而不是把用户踢回看板。
 * `anyOf` 用于「持有任一权限即可」的场景（如用户与权限分区）。
 */
function PermissionGate({
  perm,
  anyOf,
  children,
}: {
  perm?: string
  anyOf?: readonly string[]
  children: React.ReactNode
}) {
  const { loading, can, canAny } = usePermissions()
  if (loading) return <RouteFallback />
  const allowed = anyOf ? canAny([...anyOf]) : perm ? can(perm) : true
  if (!allowed) return <NoPermission />
  return <>{children}</>
}

// /onboarding 访问守卫: 必须已登录且为管理员
function OnboardingRouteGuard() {
  const session = useSession()
  const location = useLocation()
  if (session.loading) {
    return (
      <div className="min-h-full bg-base grid place-items-center">
        <Logo size={28} className="text-foreground" />
      </div>
    )
  }
  if (!session.authenticated) {
    const redirect = `${location.pathname}${location.search}`
    return <Navigate to={`/login?redirect=${encodeURIComponent(redirect)}`} replace />
  }
  if (!session.isAdmin) return <Navigate to="/" replace />
  return (
    <Suspense fallback={<RouteFallback />}>
      <Onboarding />
    </Suspense>
  )
}

// 按需路由的加载占位 (与 OnboardingGuard 的加载态同风格)
function RouteFallback() {
  return (
    <div className="min-h-full bg-base grid place-items-center">
      <Logo size={28} className="text-foreground" />
    </div>
  )
}

export const router = createBrowserRouter([
  {
    path: '/onboarding',
    element: <OnboardingRouteGuard />,
  },
  {
    path: '/admin',
    element: (
      <AdminGuard>
        <PermissionGate perm={PERM.settingsWrite}>
          <Suspense fallback={<RouteFallback />}>
            <AdminApp />
          </Suspense>
        </PermissionGate>
      </AdminGuard>
    ),
  },
  {
    path: '/admin/data',
    element: (
      <AdminGuard>
        <PermissionGate perm={PERM.dataManage}>
          <Suspense fallback={<RouteFallback />}>
            <AdminApp section="data" />
          </Suspense>
        </PermissionGate>
      </AdminGuard>
    ),
  },
  {
    path: '/admin/prompts',
    element: (
      <AdminGuard>
        <PermissionGate perm={PERM.promptsManage}>
          <Suspense fallback={<RouteFallback />}>
            <AdminApp section="prompts" />
          </Suspense>
        </PermissionGate>
      </AdminGuard>
    ),
  },
  {
    path: '/admin/permissions',
    element: (
      <AdminGuard>
        <PermissionGate anyOf={[PERM.usersManage, PERM.rolesManage]}>
          <Suspense fallback={<RouteFallback />}>
            <AdminApp section="permissions" />
          </Suspense>
        </PermissionGate>
      </AdminGuard>
    ),
  },
  {
    path: '/settings',
    element: <SettingsRedirect />,
  },
  {
    path: '/data',
    element: <Navigate to="/admin/data" replace />,
  },
  {
    path: '/login',
    element: (
      <Suspense fallback={<RouteFallback />}>
        <Auth />
      </Suspense>
    ),
  },
  {
    path: '/',
    element: (
      <OnboardingGuard>
        <Layout />
      </OnboardingGuard>
    ),
    children: [
      { index: true, element: <PermissionGate perm={PERM.dashboard}><Dashboard /></PermissionGate> },
      { path: 'overview', element: <Navigate to="/" replace /> },
      {
        path: 'analysis',
        element: (
          <PermissionGate perm={PERM.pagesManage}>
            <Navigate to="/admin?tab=pages&section=ext-pages" replace />
          </PermissionGate>
        ),
      },
      { path: 'analysis/:menuId', element: <PermissionGate perm={PERM.analysis}><AnalysisDetail /></PermissionGate> },
      { path: 'watchlist', element: <PermissionGate perm={PERM.watchlist}><Watchlist /></PermissionGate> },
      { path: 'screener', element: <PermissionGate perm={PERM.screener}><Screener /></PermissionGate> },
      { path: 'backtest', element: <PermissionGate perm={PERM.backtest}><Backtest /></PermissionGate> },
      { path: 'factors', element: <PermissionGate perm={PERM.factors}><Factors /></PermissionGate> },
      { path: 'growth', element: <PermissionGate perm={PERM.growth}><Growth /></PermissionGate> },
      { path: 'stock-analysis', element: <PermissionGate perm={PERM.stockAnalysis}><StockAnalysis /></PermissionGate> },
      { path: 'mining', element: <MiningRedirect /> },
      { path: 'indices', element: <PermissionGate perm={PERM.indices}><Indices /></PermissionGate> },
      { path: 'regime', element: <PermissionGate perm={PERM.regime}><Regime /></PermissionGate> },
      { path: 'account', element: <Suspense fallback={<RouteFallback />}><PersonalSettings /></Suspense> },
      // 隐藏路由：开发者工具（不暴露在菜单，权限码 page.dev）
      { path: 'dev', element: <PermissionGate perm={PERM.dev}><Dev /></PermissionGate> },
      // 旧路由兼容重定向
      { path: 'settings/keys', element: <Navigate to="/admin?tab=data-sources" replace /> },
      { path: 'settings/ai', element: <Navigate to="/admin?tab=ai" replace /> },
      { path: 'settings/queries', element: <Navigate to="/admin?tab=queries" replace /> },
      ...frontendExtensionRoutes.map(route => {
        const ExtensionPage = route.component
        return {
          path: route.path.slice(1),
          element: (
            <PermissionGate perm={permissionForRoute(route.path) ?? PERM.analysis}>
              <ExtensionBoundary extensionId={route.extensionId}>
                <ExtensionPage />
              </ExtensionBoundary>
            </PermissionGate>
          ),
        }
      }),
    ],
  },
])
