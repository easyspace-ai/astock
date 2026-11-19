import { cn } from '@/lib/cn'

interface Props {
  title: string
  subtitle?: React.ReactNode
  /** 标题右侧、subtitle 之前的额外节点(如状态徽标) */
  titleExtra?: React.ReactNode
  /** 标题左侧图标 — 传 lucide 图标, 自动套 accent 色与 16px 盒 */
  icon?: React.ReactNode
  right?: React.ReactNode
  className?: string
}

/**
 * 统一页头 — 25 个页面共用同一条「图标 + 标题 / 说明 / 页面操作」结构。
 * 壳层顶栏只负责全局动作, 页面级按钮一律留在这一行, 避免两层争夺顶部空间。
 * 默认高 44px(与顶栏对齐), 页面可用 className 覆盖内衬/背景/sticky。
 */
export function PageHeader({ title, subtitle, titleExtra, icon, right, className }: Props) {
  return (
    <header
      className={cn(
        'flex min-h-11 shrink-0 flex-wrap items-center justify-between gap-x-4 gap-y-1 border-b border-border px-4 py-1.5',
        className,
      )}
    >
      <div className="flex min-w-0 flex-1 items-center gap-2">
        {icon && (
          <span className="flex h-4 w-4 shrink-0 items-center justify-center text-accent" aria-hidden="true">
            {icon}
          </span>
        )}
        <div className="flex min-w-0 flex-col justify-center">
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            <h1 className="min-w-0 truncate text-[15px] leading-5 font-semibold text-foreground">{title}</h1>
            {titleExtra}
          </div>
          {subtitle && <span className="truncate text-[11px] leading-4 text-muted">{subtitle}</span>}
        </div>
      </div>
      {right && <div className="flex min-w-0 items-center max-sm:w-full max-sm:overflow-x-auto">{right}</div>}
    </header>
  )
}
