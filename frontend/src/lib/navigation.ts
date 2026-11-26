import type { ReactNode } from 'react'
import { BarChart3, Rocket, Sparkles, Tags } from 'lucide-react'
import {
  IconDashboard, IconWatchlist, IconStrategy, IconFactors, IconBacktest,
  IconRegime, IconIndices, type BrandIconProps,
} from '@/components/BrandIcons'
import type { AnalysisMenu, MenuNode } from './api'
import { ROUTE_PERMISSION, analysisPagePermission, extensionPagePermission } from './permissions'
import { getFrontendExtensionNavigation } from '@/extensions/registry'
import type { NavGroup, NavLayout, NavPage } from './navLayout'

export type NavigationEntry = {
  id: string; to: string; label: string; icon: (props: BrandIconProps) => ReactNode
  type: 'builtin' | 'analysis' | 'extension'
  visible: boolean; arch?: 'core' | 'ext'; badge?: string
  badgeTone?: 'accent' | 'neutral' | 'warning' | 'danger'; extensionId?: string
  /** 页面权限码：没有该权限的页面不进导航 */
  perm?: string
}

export const CORE_NAVIGATION: NavigationEntry[] = [
  { id: '/', label: '看板', icon: IconDashboard },
  { id: '/watchlist', label: '自选', icon: IconWatchlist },
  { id: '/screener', label: '策略', icon: IconStrategy },
  { id: '/factors', label: '因子', icon: IconFactors },
  { id: '/growth', label: '三年五倍', icon: Rocket },
  { id: '/stock-analysis', label: '个股分析', icon: Sparkles },
  { id: '/backtest', label: '回测', icon: IconBacktest },
  { id: '/regime', label: '市场环境', icon: IconRegime },
  { id: '/indices', label: '指数', icon: IconIndices },
].map(x => ({
  ...x, to: x.id, type: 'builtin', visible: true, arch: 'core' as const,
  perm: ROUTE_PERMISSION[x.id],
}))

/** 宿主与管理页共用来源及 ID，分析页历史裸 ID 只在兼容读取时转换。 */
export function getNavigationEntries(
  menus: readonly AnalysisMenu[] = [],
  menuNodes: readonly MenuNode[] = [],
): NavigationEntry[] {
  const entries: NavigationEntry[] = [
    ...CORE_NAVIGATION,
    ...menus.map(m => ({
      id: `/analysis/${m.id}`, to: `/analysis/${m.id}`, label: m.label,
      type: 'analysis' as const, visible: m.visible,
      icon: m.icon === 'tags' ? Tags : BarChart3, arch: 'ext' as const,
      perm: analysisPagePermission(m.id),
    })),
    ...getFrontendExtensionNavigation().map(item => ({
      id: item.route.path, to: item.route.path, label: item.label, icon: item.icon,
      type: 'extension' as const, visible: true, arch: 'ext' as const,
      badge: item.badge, badgeTone: item.badgeTone, extensionId: item.extensionId,
      perm: extensionPagePermission(item.route.path),
    })),
  ]
  const merged = [...new Map(entries.map(x => [x.id, x])).values()]
  if (menuNodes.length === 0) return merged
  // 后端菜单树是标签/顺序/权限的权威来源；前端只保留图标与页面组件映射。
  const byRoute = new Map(menuNodes
    .filter(node => node.kind === 'permission' && node.route)
    .map(node => [node.route as string, node]))
  for (const entry of merged) {
    const node = byRoute.get(entry.to)
    if (!node) continue
    entry.label = node.label || entry.label
    entry.perm = node.key
    if (node.visible === false) entry.visible = false
  }
  return merged
}

/**
 * 后端菜单树 -> 前端 NavLayout。
 * 只映射当前已注册的页面（扩展页/分析页/核心页），纯分组节点保留层级。
 */
export function menuNodesToLayout(
  menuNodes: readonly MenuNode[],
  entries: readonly NavigationEntry[],
  options: { includeEmptyGroups?: boolean } = {},
): NavLayout {
  const pageByRoute = new Map(entries.map(entry => [entry.to, entry]))
  const childrenByParent = new Map<string, MenuNode[]>()
  for (const node of menuNodes) {
    if (node.visible === false) continue
    const parent = node.parent || ''
    const list = childrenByParent.get(parent) ?? []
    list.push(node)
    childrenByParent.set(parent, list)
  }

  const build = (node: MenuNode): NavPage | NavGroup | null => {
    if (node.kind === 'group') {
      const children = (childrenByParent.get(node.key) ?? [])
        .map(build)
        .filter((child): child is NavPage => child?.type === 'page')
      if (children.length === 0 && !options.includeEmptyGroups) return null
      return { type: 'group', id: node.key, label: node.label, children }
    }
    if (!node.route || !pageByRoute.has(node.route)) return null
    return { type: 'page', id: node.route }
  }

  const roots = (childrenByParent.get('') ?? [])
    .map(build)
    .filter((node): node is NavPage | NavGroup => node !== null)
  // 看板固定在首位；其余保持后端 order。
  const home = roots.find(node => node.type === 'page' && node.id === '/')
  const items = home ? [home, ...roots.filter(node => node !== home)] : roots
  return { v: 1, items }
}
