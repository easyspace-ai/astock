import { NavLink } from 'react-router-dom'
import { CheckCircle2, ChevronDown, ChevronRight, Loader2 } from 'lucide-react'
import type { WatchlistGroup } from '@/lib/api'
import type { GroupPctMap } from '@/lib/watchlistGroupStats'
import { computeGroupPcts as _computeGroupPcts, groupPctColor, groupPctTitle } from '@/lib/watchlistGroupStats'
import { fmtPct } from '@/lib/format'
import { resolveWatchlistGroupColor } from '@/lib/watchlist-group-colors'
import { navPageIds, type NavGroup, type NavNode } from '@/lib/navLayout'
import type { NavigationEntry } from '@/lib/navigation'
import { NavFolder } from '@/components/navigation/NavFolder'
import { MonitorBadge } from './MonitorBadge'
import { ExtensionSlot } from '@/extensions/ExtensionSlot'
import { cn } from '@/lib/cn'

// 导航徽标色阶 — 默认 warning 保持既有琥珀, 扩展可按语义选色
const BADGE_TONE_CLASS: Record<NonNullable<NavigationEntry['badgeTone']>, string> = {
  warning: 'border-amber-400/30 bg-amber-400/10 text-amber-400',
  accent: 'border-accent-solid/30 bg-accent-solid/10 text-accent',
  neutral: 'border-border bg-elevated/60 text-secondary',
  danger: 'border-danger/30 bg-danger/10 text-danger',
}

// 仅供类型推导, 避免调用方再引一次模块
export type NavPctMap = ReturnType<typeof _computeGroupPcts>

export interface NavPanelProps {
  items: NavNode[]
  navById: Map<string, NavigationEntry>
  pathname: string
  search: string
  railMode: boolean
  activeGroupId?: string
  isGroupOpen: (group: NavGroup) => boolean
  onToggleGroup: (group: NavGroup) => void
  railGroupId: string | null
  onRailGroupChange: (id: string | null) => void
  isDataSyncing: boolean
  dataSyncJustDone: boolean
  groupsInNav: boolean
  watchlistGroups: WatchlistGroup[]
  watchlistNavExpanded: boolean
  onToggleWatchlist: () => void
  navGroupPcts: GroupPctMap
}

/**
 * 侧栏导航树 — 页面项 / 可展开分组 / 图标条弹层 / 自选二级分组。
 * 纯展示 + 回调: 状态与查询都留在 Layout, 这里只负责把树画出来。
 */
export function NavPanel({
  items, navById, pathname, search, railMode, activeGroupId,
  isGroupOpen, onToggleGroup, railGroupId, onRailGroupChange,
  isDataSyncing, dataSyncJustDone, groupsInNav, watchlistGroups,
  watchlistNavExpanded, onToggleWatchlist, navGroupPcts,
}: NavPanelProps) {
  const renderNavItem = (entry: NavigationEntry, nested = false) => {
    const itemRail = railMode && !nested
    const { to, label, icon: Icon, badge, badgeTone = 'warning' } = entry
    // 「自选」项 — 开启分组侧栏且未整体收起时, 渲染为可展开父项 + 二级分组
    const isWatchlistExpandable = to === '/watchlist' && groupsInNav && !itemRail && watchlistGroups.length > 0
    return (
      <div key={to}>
        {isWatchlistExpandable ? (
          /* 可展开的自选父项 — 点击切换展开, 不直接跳页 */
          <button
            aria-expanded={watchlistNavExpanded}
            onClick={onToggleWatchlist}
            className={cn(
              'group relative flex w-full items-center gap-3 rounded-btn px-3 py-2 text-sm transition-all duration-150 ease-smooth',
              pathname === '/watchlist'
                ? 'bg-elevated text-foreground font-medium'
                : 'text-foreground/75 hover:bg-elevated/70 hover:text-foreground',
            )}
          >
            <span
              className={cn(
                'pointer-events-none absolute inset-y-1 left-0 w-[2px] rounded-full bg-accent transition-opacity duration-150',
                pathname === '/watchlist' ? 'opacity-100' : 'opacity-0',
              )}
            />
            <Icon className={cn('h-4 w-4 shrink-0 transition-colors', pathname === '/watchlist' ? 'text-accent' : 'text-foreground/60 group-hover:text-foreground/85')} />
            <span className="flex-1 text-left">{label}</span>
            {watchlistNavExpanded
              ? <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted" />
              : <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted" />
            }
          </button>
        ) : (
          /* 普通菜单项 */
          <NavLink
            to={to}
            title={itemRail ? label : undefined}
            className={({ isActive }) =>
              cn(
                'group relative flex items-center rounded-btn text-sm transition-all duration-150 ease-smooth',
                itemRail ? 'justify-center px-0 py-2' : 'gap-3 px-3 py-2',
                isActive
                  ? 'bg-elevated text-foreground font-medium'
                  : 'text-foreground/75 hover:bg-elevated/70 hover:text-foreground',
              )
            }
          >
            {({ isActive }) => (
              <>
                {/* active 左侧 accent 竖条指示 */}
                <span
                  className={cn(
                    'pointer-events-none absolute inset-y-1 left-0 w-[2px] rounded-full bg-accent transition-opacity duration-150',
                    isActive ? 'opacity-100' : 'opacity-0',
                  )}
                />
                <Icon className={cn('h-4 w-4 shrink-0 transition-colors', isActive ? 'text-accent' : 'text-foreground/60 group-hover:text-foreground/85')} />
                {!itemRail && (
                  <span className="flex min-w-0 flex-1 items-center gap-1.5">
                    <span className="truncate">{label}</span>
                  </span>
                )}
                {!itemRail && badge && (
                  <span className={cn(
                    'ml-auto inline-flex items-center rounded-full border px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider shrink-0',
                    BADGE_TONE_CLASS[badgeTone],
                  )}>
                    {badge}
                  </span>
                )}
                {/* 数据同步状态: 同步中转圈, 刚完成显示绿色对勾闪烁 3 秒 */}
                {to === '/data' && isDataSyncing && !itemRail && (
                  <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-accent" />
                )}
                {to === '/data' && !isDataSyncing && dataSyncJustDone && !itemRail && (
                  <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-bull animate-pulse" />
                )}
                {/* 监控中心徽标: 仅非监控页且有未读时显示 */}
                {to === '/monitor' && !itemRail && <MonitorBadge active={isActive} />}
              </>
            )}
          </NavLink>
        )}

        {/* 自选分组二级子菜单 — 展开时显示 */}
        {isWatchlistExpandable && watchlistNavExpanded && (
          <div className="mt-0.5 space-y-0.5">
            <NavLink
              to="/watchlist"
              className={({ isActive }) => cn(
                'flex items-center gap-3 rounded-btn py-1.5 pl-9 pr-3 text-[12px] transition-colors duration-150 ease-smooth',
                isActive && !search
                  ? 'text-accent font-medium'
                  : 'text-foreground/60 hover:text-foreground hover:bg-elevated/50',
              )}
            >
              <span className="flex h-4 w-4 shrink-0 items-center justify-center"><span className="h-1.5 w-1.5 rounded-full bg-muted" /></span>
              <span>全部</span>
              {(() => {
                const info = navGroupPcts['all']
                return info && info.pct != null ? (
                  <span className={`ml-auto font-mono text-[10px] tabular-nums ${groupPctColor(info.pct)}`} title={groupPctTitle(info)}>
                    {fmtPct(info.pct)}
                  </span>
                ) : null
              })()}
            </NavLink>
            {watchlistGroups.map(group => {
              const color = resolveWatchlistGroupColor(group.color)
              const groupPath = `/watchlist?group=${group.id}`
              const isGroupActive = pathname === '/watchlist' && search === `?group=${group.id}`
              const pctInfo = navGroupPcts[group.id]
              return (
                <NavLink
                  key={group.id}
                  to={groupPath}
                  className={cn(
                    'flex items-center gap-3 rounded-btn py-1.5 pl-9 pr-3 text-[12px] transition-colors duration-150 ease-smooth',
                    isGroupActive
                      ? 'text-accent font-medium'
                      : 'text-foreground/60 hover:text-foreground hover:bg-elevated/50',
                  )}
                >
                  <span className="flex h-4 w-4 shrink-0 items-center justify-center"><span className={`h-1.5 w-1.5 rounded-full ${color.dot}`} /></span>
                  <span className="truncate">{group.name}</span>
                  {pctInfo && pctInfo.pct != null && (
                    <span className={`ml-auto font-mono text-[10px] tabular-nums ${groupPctColor(pctInfo.pct)}`} title={groupPctTitle(pctInfo)}>
                      {fmtPct(pctInfo.pct)}
                    </span>
                  )}
                </NavLink>
              )
            })}
          </div>
        )}
      </div>
    )
  }

  return (
    <>
      {items.map(node => {
        if (node.type === 'page') {
          const entry = navById.get(node.id)
          return entry ? renderNavItem(entry) : null
        }
        const ids = navPageIds(node)
        return <NavFolder key={node.id} label={node.label} active={node.id === activeGroupId}
          expanded={isGroupOpen(node)} rail={railMode} popupOpen={railGroupId === node.id}
          onToggle={() => onToggleGroup(node)} onPopupChange={open => onRailGroupChange(open ? node.id : null)}
          indicators={<>
            {ids.includes('/data') && isDataSyncing && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {ids.includes('/monitor') && <MonitorBadge active={pathname === '/monitor'} />}
          </>}>
          {node.children.length === 0 && <p className="px-3 py-2 text-xs leading-5 text-muted">暂无子菜单，请在菜单管理中移入页面。</p>}
          {node.children.map(page => {
            const entry = navById.get(page.id)
            return entry ? renderNavItem(entry, true) : null
          })}
        </NavFolder>
      })}
      <ExtensionSlot
        name="layout.navigation.extra"
        context={{ collapsed: railMode, pathname }}
        compact
      />
    </>
  )
}
