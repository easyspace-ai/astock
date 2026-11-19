import type { ReactNode } from 'react'
import { Menu, PanelLeft, PanelLeftClose } from 'lucide-react'

/**
 * 全局顶栏 — 44px，纸面色，跨在导航与内容面板之上（Craft AppShell 的 TopBar 位置）。
 * 铺画布色: 与左侧导航栏同色连成一片(header/rail 同一块底), 内容纸面才是唯一亮块。
 * 只做壳层装配：左侧导航开关、品牌；中间指数条；右侧全局动作（主题/同步）。
 * 页面级操作仍归各页 PageHeader，不进这里。
 */
interface TopBarProps {
  brand: ReactNode
  center?: ReactNode
  actions?: ReactNode
  isDesktop: boolean
  navRail: boolean
  onToggleNav: () => void
  navTitle: string
}

export function TopBar({ brand, center, actions, isDesktop, navRail, onToggleNav, navTitle }: TopBarProps) {
  return (
    <header className="flex h-11 shrink-0 items-center gap-0.5 bg-base px-2">
      <button
        type="button"
        onClick={onToggleNav}
        title={navTitle}
        aria-label={navTitle}
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-control text-secondary transition-colors duration-150 ease-smooth hover:bg-elevated/70 hover:text-foreground"
      >
        {!isDesktop
          ? <Menu className="h-4 w-4 shrink-0" />
          : navRail
            ? <PanelLeft className="h-4 w-4 shrink-0" />
            : <PanelLeftClose className="h-4 w-4 shrink-0" />}
      </button>

      {brand}

      <div className="flex min-w-0 flex-1 items-center justify-center px-1">{center}</div>

      <div className="flex shrink-0 items-center gap-0.5">{actions}</div>
    </header>
  )
}
