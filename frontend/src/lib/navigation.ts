import type { ReactNode } from 'react'
import { BarChart3, Rocket, Sparkles, Tags } from 'lucide-react'
import {
  IconDashboard, IconWatchlist, IconStrategy, IconFactors, IconBacktest,
  IconRegime, IconIndices, type BrandIconProps,
} from '@/components/BrandIcons'
import type { AnalysisMenu } from './api'
import { PERM, ROUTE_PERMISSION } from './permissions'
import { getFrontendExtensionNavigation } from '@/extensions/registry'

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
export function getNavigationEntries(menus: readonly AnalysisMenu[] = []): NavigationEntry[] {
  const entries: NavigationEntry[] = [
    ...CORE_NAVIGATION,
    ...menus.map(m => ({
      id: `/analysis/${m.id}`, to: `/analysis/${m.id}`, label: m.label,
      type: 'analysis' as const, visible: m.visible,
      icon: m.icon === 'tags' ? Tags : BarChart3, arch: 'ext' as const,
      perm: PERM.analysis,
    })),
    ...getFrontendExtensionNavigation().map(item => ({
      id: item.route.path, to: item.route.path, label: item.label, icon: item.icon,
      type: 'extension' as const, visible: true, arch: 'ext' as const,
      badge: item.badge, badgeTone: item.badgeTone, extensionId: item.extensionId,
      perm: PERM.analysis,
    })),
  ]
  return [...new Map(entries.map(x => [x.id, x])).values()]
}
