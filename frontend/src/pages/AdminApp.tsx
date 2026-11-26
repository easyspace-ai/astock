import { useNavigate, Link } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Database, LogOut, MessageSquareText, ShieldCheck, SlidersHorizontal, Users } from 'lucide-react'
import { Logo } from '@/components/Logo'
import { ContentPanel } from '@/components/shell/ContentPanel'
import { ThemeToggle } from '@/components/shell/ThemeToggle'
import { Settings } from '@/pages/Settings'
import { Data } from '@/pages/Data'
import { PromptAdmin } from '@/pages/PromptAdmin'
import { PermissionAdmin } from '@/pages/admin/PermissionAdmin'
import { api } from '@/lib/api'
import { useAccountUser, usePermissions } from '@/lib/useAccount'
import { PERM } from '@/lib/permissions'
import { NoPermission } from '@/components/NoPermission'
import { cn } from '@/lib/cn'

const ROLE_LABEL: Record<string, string> = { admin: '管理员', pro: '专业版', free: '免费版' }

export type AdminSection = 'settings' | 'data' | 'prompts' | 'permissions'

const SECTIONS: Array<{
  id: AdminSection
  label: string
  href: string
  icon: typeof Database
  allowed: (can: (perm: string) => boolean) => boolean
}> = [
  { id: 'settings', label: '系统配置', href: '/admin', icon: SlidersHorizontal, allowed: can => can(PERM.settingsWrite) },
  { id: 'data', label: '数据管理', href: '/admin/data', icon: Database, allowed: can => can(PERM.dataManage) },
  { id: 'prompts', label: '提示词管理', href: '/admin/prompts', icon: MessageSquareText, allowed: can => can(PERM.promptsManage) },
  {
    id: 'permissions',
    label: '用户与权限',
    href: '/admin/permissions',
    icon: Users,
    allowed: can => can(PERM.usersManage) || can(PERM.rolesManage),
  },
]

/**
 * 独立管理后台 — 不套前台 Layout（没有行情侧边栏/自选/实时行情）。
 * 只承载系统配置；前台功能页全部在 `/` 下，两边互不混用。
 * 各分区按权限码显隐：看不到的分区不进顶栏，直访时由路由守卫给出「无权限」态。
 */
export function AdminApp({ section = 'settings' }: { section?: AdminSection }) {
  const me = useAccountUser()
  const { can, loading: permsLoading } = usePermissions()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const logout = async () => {
    try { await api.accountLogout() } catch { /* 会话可能已失效 */ }
    qc.clear()
    navigate('/login', { replace: true })
  }

  const sections = SECTIONS.filter(item => item.allowed(can))
  const current = SECTIONS.find(item => item.id === section)
  const sectionAllowed = !current || current.allowed(can)
  // 顶栏品牌回落到当前账号能进的分区，避免普通管理员点 logo 撞上无权限页
  const homeHref = sections[0]?.href ?? '/'

  return (
    <div className="flex h-full flex-col overflow-hidden bg-base text-foreground">
      {/* 与新壳同一套几何: 44px 顶栏铺纸面色, 画布灰只留四周, 内容在 12px 圆角纸面内 */}
      <header className="flex h-11 shrink-0 items-center gap-2 bg-base px-2.5">
        <Link to={homeHref} className="flex min-w-0 shrink-0 items-center gap-2">
          <Logo size={18} className="shrink-0" style={{ color: 'hsl(var(--accent-solid))' }} />
          <span className="hidden whitespace-nowrap text-[11px] font-semibold tracking-[0.02em] text-foreground/80 sm:inline">
            财富引擎
          </span>
          <span className="hidden shrink-0 items-center gap-1 rounded-full border border-border bg-elevated/60 px-1.5 py-0.5 text-[9px] text-secondary sm:inline-flex">
            <ShieldCheck className="h-2.5 w-2.5" />
            管理后台
          </span>
        </Link>

        <div className="ml-1 flex min-w-0 items-center gap-0.5 overflow-x-auto sm:ml-3">
          {sections.map(({ id, label, href, icon: Icon }) => (
            <Link
              key={id}
              to={href}
              className={cn(
                'inline-flex shrink-0 items-center gap-1.5 rounded-btn px-2 py-1.5 text-[12px] transition-colors duration-150 ease-smooth',
                section === id
                  ? 'bg-elevated text-foreground font-medium'
                  : 'text-foreground/60 hover:bg-elevated/70 hover:text-foreground',
              )}
            >
              <Icon className={cn('h-3.5 w-3.5', section === id ? 'text-accent' : 'text-foreground/50')} />
              {label}
            </Link>
          ))}
        </div>

        <div className="ml-auto flex min-w-0 shrink-0 items-center gap-1.5">
          {me.data && (
            <span className="hidden max-w-[180px] truncate text-[11px] text-muted lg:inline" title={me.data.email}>
              {me.data.email}
            </span>
          )}
          {me.data && (
            <span className="hidden items-center gap-1 rounded-full border border-border bg-elevated/60 px-1.5 py-0.5 text-[9px] text-secondary sm:inline-flex">
              <ShieldCheck className="h-2.5 w-2.5" />
              {me.data.perm_role_name ?? ROLE_LABEL[me.data.role] ?? me.data.role}
            </span>
          )}
          <ThemeToggle />
          <Link
            to="/"
            className="inline-flex items-center gap-1 rounded-btn border border-border bg-elevated/40 px-2 py-1.5 text-[11px] text-secondary transition-colors hover:border-accent/40 hover:text-foreground"
          >
            <ArrowLeft className="h-3 w-3" />
            <span className="hidden sm:inline">返回前台</span>
          </Link>
          <button
            onClick={logout}
            title="退出登录"
            className="rounded-btn p-1.5 text-muted transition-colors hover:bg-danger/10 hover:text-danger"
          >
            <LogOut className="h-3.5 w-3.5" />
          </button>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 p-1.5 pt-0">
        <ContentPanel>
          <main className="min-h-0 flex-1 overflow-auto scrollbar-gutter-stable">
            {!permsLoading && !sectionAllowed && <NoPermission title="无该管理分区权限" />}
            {sectionAllowed && section === 'settings' && <Settings embedded />}
            {sectionAllowed && section === 'data' && <Data embedded />}
            {sectionAllowed && section === 'prompts' && <PromptAdmin />}
            {sectionAllowed && section === 'permissions' && (
              <PermissionAdmin canUsers={can(PERM.usersManage)} canRoles={can(PERM.rolesManage)} canAudit={can(PERM.auditView)} />
            )}
          </main>
        </ContentPanel>
      </div>
    </div>
  )
}
