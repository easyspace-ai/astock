/**
 * 权限码常量与页面路由映射（前端只做可见性，真正的边界在后端）。
 *
 * 权限码清单的权威来源是后端 `services/menu_registry.py`；这里只保留前端需要的
 * 稳定路由映射与扩展页/分析页的动态权限码生成规则，不复制权限文案和分组。
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
  /** 旧版分析中心总权限；仅用于兼容旧权限组，新数据已落到具体页面 */
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

export const PAGE_ANALYSIS_PREFIX = 'page.analysis.'
export const PAGE_EXT_PREFIX = 'page.ext.'

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

/** 分析菜单 ID -> 叶子权限码；与后端 `page.analysis.<menu_id>` 一致。 */
export function analysisPagePermission(menuId: string): string {
  return `${PAGE_ANALYSIS_PREFIX}${menuId}`
}

/** 扩展页路由 -> 叶子权限码；路径段双下划线连接，避免 /info/news 与 /info-news 撞码。 */
export function extensionPagePermission(route: string): string {
  const parts = route.split('/').filter(Boolean)
    .map(seg => seg.replace(/[^a-zA-Z0-9]+/g, '_').replace(/^_+|_+$/g, ''))
    .filter(Boolean)
  return `${PAGE_EXT_PREFIX}route.${parts.join('__')}`
}

/** 取路径对应的权限码。核心页按固定映射，分析页/扩展页按路由生成。 */
export function permissionForRoute(pathname: string): string | undefined {
  if (pathname.startsWith('/analysis/')) {
    const menuId = pathname.split('/')[2]
    return menuId ? analysisPagePermission(menuId) : undefined
  }
  if (ROUTE_PERMISSION[pathname]) return ROUTE_PERMISSION[pathname]
  if (pathname.startsWith('/')) return extensionPagePermission(pathname)
  return undefined
}

/** 单个权限码是否匹配（兼容旧 page.analysis 总权限与通配检查）。 */
export function permissionMatches(codes: readonly string[], key: string): boolean {
  if (codes.includes(key)) return true
  if (key.endsWith('.*')) {
    const prefix = key.slice(0, -1)
    return codes.some(code => code.startsWith(prefix))
  }
  if (codes.includes(PERM.analysis)
    && (key.startsWith(PAGE_ANALYSIS_PREFIX) || key.startsWith(PAGE_EXT_PREFIX))) {
    return true
  }
  return false
}

/** 管理后台分区 -> 权限码（「用户与权限」分区需 users_manage 或 roles_manage，见 AdminApp） */
export const ADMIN_SECTION_PERMISSION = {
  settings: PERM.settingsWrite,
  data: PERM.dataManage,
  prompts: PERM.promptsManage,
  permissions: PERM.usersManage,
} as const
