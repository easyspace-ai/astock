/**
 * 权限码常量与页面路由映射（前端只做可见性，真正的边界在后端）。
 *
 * 权限码清单的权威来源是后端 `services/rbac.py`；这里只保留前端需要的常量与
 * 「路由 -> 页面权限码」映射，不复制权限文案/分组。
 */
export const PERM = {
  dashboard: 'page.dashboard',
  watchlist: 'page.watchlist',
  screener: 'page.screener',
  factors: 'page.factors',
  growth: 'page.growth',
  stockAnalysis: 'page.stock_analysis',
  backtest: 'page.backtest',
  regime: 'page.regime',
  indices: 'page.indices',
  analysis: 'page.analysis',
  dev: 'page.dev',

  strategyEdit: 'feature.strategy_edit',
  backtestRun: 'feature.backtest_run',
  aiUse: 'feature.ai_use',
  dataExport: 'feature.data_export',

  adminAccess: 'admin.access',
  settingsWrite: 'admin.settings_write',
  dataManage: 'admin.data_manage',
  promptsManage: 'admin.prompts_manage',
  pagesManage: 'admin.pages_manage',
  usersManage: 'admin.users_manage',
  rolesManage: 'admin.roles_manage',
  auditView: 'admin.audit_view',
} as const

/** 核心页面路由 -> 页面权限码 */
export const ROUTE_PERMISSION: Record<string, string> = {
  '/': PERM.dashboard,
  '/overview': PERM.dashboard,
  '/account': PERM.dashboard,
  '/watchlist': PERM.watchlist,
  '/screener': PERM.screener,
  '/factors': PERM.factors,
  '/growth': PERM.growth,
  '/stock-analysis': PERM.stockAnalysis,
  '/backtest': PERM.backtest,
  '/regime': PERM.regime,
  '/indices': PERM.indices,
  '/analysis': PERM.analysis,
  '/dev': PERM.dev,
}

/** 取路径对应的权限码；未登记的路由（扩展页）统一按分析中心权限。 */
export function permissionForRoute(pathname: string): string | undefined {
  if (pathname.startsWith('/analysis')) return PERM.analysis
  if (ROUTE_PERMISSION[pathname]) return ROUTE_PERMISSION[pathname]
  return undefined
}

/** 管理后台分区 -> 权限码（「用户与权限」分区需 users_manage 或 roles_manage，见 AdminApp） */
export const ADMIN_SECTION_PERMISSION = {
  settings: PERM.settingsWrite,
  data: PERM.dataManage,
  prompts: PERM.promptsManage,
  permissions: PERM.usersManage,
} as const
