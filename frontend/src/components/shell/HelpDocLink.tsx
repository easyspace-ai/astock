import { BookOpen } from 'lucide-react'

/**
 * 顶栏「帮助文档」入口 — 打开交付说明书静态站。
 *
 * 说明书由后端挂在 /manual（backend/app/main.py → ManualStaticFiles），
 * 与主应用同源、独立静态产物，所以走普通链接另开标签页，不进前端路由，
 * 也不需要在扩展系统里注册。
 */
export function HelpDocLink() {
  return (
    <a
      href="/manual/"
      target="_blank"
      rel="noopener noreferrer"
      title="帮助文档"
      aria-label="帮助文档"
      className="flex items-center justify-center rounded-btn p-2 text-foreground/80 transition-colors duration-150 ease-smooth hover:bg-elevated hover:text-foreground"
    >
      <BookOpen className="h-4 w-4 shrink-0" />
    </a>
  )
}
