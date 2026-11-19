import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

/**
 * 内容面板 — 页面路由出口的纸面容器。
 * 12px 圆角 + 1px 发丝环（用 inset box-shadow，避免 border 吃掉 1px 内容宽）。
 * 滚动条仍在内部 main 上，面板本身 overflow-hidden 负责裁圆角。
 */
export function ContentPanel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        'relative flex h-full min-h-0 flex-col overflow-hidden rounded-panel bg-surface',
        // 亮色纸面与画布只差一档, 靠发丝环 + 一层极浅投影立起图底关系; 暗色不需要投影
        'shadow-[0_1px_2px_rgba(16,24,40,0.04),0_12px_28px_-20px_rgba(16,24,40,0.18)]',
        'dark:shadow-none',
        className,
      )}
    >
      {/* 发丝环单独一层叠在内容之上: 页面自己铺 bg-surface 时会把容器上的 inset 阴影盖掉,
          表现为面板上沿/左沿的界线时有时无。用覆盖层就不受子元素背景影响。 */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 z-30 rounded-panel shadow-[inset_0_0_0_1px_hsl(var(--border-default))]"
      />
      {children}
    </div>
  )
}
