import { cn } from '@/lib/cn'

export interface HeaderRealtimeProps {
  isAdmin: boolean
  pending: boolean
  disabled: boolean
  title: string
  indicatorClass: string
  statusClass: string
  statusLabel: string
  unavailable: boolean
  providerName: string | null
  modeLabel: string
  isPaused: boolean
  onToggle: () => void
}

/**
 * 顶栏右侧的实时行情紧凑控制 — 由原侧边栏底部开关上移。
 * 状态灯 + 短标签为主; 完整状态与提示收敛到 tooltip, 保持顶栏清爽。
 * 后台配置入口统一收敛到账户菜单的「管理后台」, 这里不再重复放设置齿轮。
 */
export function HeaderRealtime({
  isAdmin, pending, disabled, title, indicatorClass, statusClass, statusLabel,
  unavailable, providerName, modeLabel, isPaused, onToggle,
}: HeaderRealtimeProps) {
  const showUnavailable = unavailable && !providerName
  const tooltip = showUnavailable
    ? '当前数据源无实时行情权限'
    : [statusLabel, providerName || modeLabel, isPaused ? '同步期间暂停' : ''].filter(Boolean).join(' · ')

  const statusButton = isAdmin ? (
    <button
      type="button"
      onClick={onToggle}
      disabled={disabled}
      aria-label={title}
      aria-busy={pending}
      title={tooltip}
      className="flex h-7 items-center gap-1.5 rounded-control px-2 text-secondary transition-colors duration-150 ease-smooth hover:bg-elevated/70 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
    >
      <span className={cn('inline-block h-1.5 w-1.5 shrink-0 rounded-full', indicatorClass)} />
      <span className={cn('hidden text-[11px] leading-none sm:inline', statusClass)}>
        {showUnavailable ? '不可用' : statusLabel}
      </span>
    </button>
  ) : (
    <span
      title={tooltip}
      className="flex h-7 cursor-default items-center gap-1.5 rounded-control px-2 text-secondary"
    >
      <span className={cn('inline-block h-1.5 w-1.5 shrink-0 rounded-full', indicatorClass)} />
      <span className={cn('hidden text-[11px] leading-none sm:inline', statusClass)}>
        {showUnavailable ? '不可用' : statusLabel}
      </span>
    </span>
  )

  return (
    <div className="flex shrink-0 items-center gap-0.5">
      {statusButton}
    </div>
  )
}
