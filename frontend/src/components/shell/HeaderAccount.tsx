import { useEffect, useRef, useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { LogOut, Settings, UserRound } from 'lucide-react'
import { useAccountUser } from '@/lib/useAccount'
import { usePreferences } from '@/lib/useSharedQueries'
import { api } from '@/lib/api'
import { cn } from '@/lib/cn'

const ROLE_LABEL: Record<string, string> = { admin: '管理员', pro: '专业版', free: '免费版' }
const ROLE_CLASS: Record<string, string> = {
  admin: 'bg-purple-500/15 text-purple-400',
  pro: 'bg-amber-500/15 text-amber-400',
  free: 'bg-muted/20 text-muted',
}

/**
 * 顶栏右侧账户区 — 由原侧边栏底部用户卡片与后台入口上移。
 * 邮箱账号: 头像按钮 + 下拉(个人中心 / 管理后台 / 退出登录);
 * 账号菜单: 个人中心、管理后台入口与退出登录。
 */
export function HeaderAccount({ isAdmin }: { isAdmin: boolean }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data } = useAccountUser()
  const { data: prefs } = usePreferences()
  const [open, setOpen] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onPointer = (event: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const logout = async () => {
    try { await api.accountLogout() } catch { /* 会话可能已失效 */ }
    qc.clear()
    navigate('/login', { replace: true })
  }

  const adminHref = prefs?.nav_layout ? '/admin?tab=pages&section=menus' : '/admin'

  if (!data) return null

  const initial = data.email.slice(0, 1).toUpperCase()

  return (
    <div ref={boxRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        title={`${data.email} · ${ROLE_LABEL[data.role] ?? data.role}`}
        aria-label="账户菜单"
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex h-7 w-7 items-center justify-center rounded-full bg-accent/15 text-[11px] font-semibold text-accent transition-colors hover:bg-accent/25"
      >
        {initial}
      </button>

      {open && (
        <div
          role="menu"
          aria-label="账户菜单"
          className="absolute right-0 top-full z-50 mt-1 w-56 rounded-panel border border-border bg-floating p-1 shadow-2xl"
        >
          <div className="flex items-center gap-2 px-2 py-2">
            <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-accent/15 text-xs font-semibold text-accent">
              {initial}
            </span>
            <div className="min-w-0">
              <div className="truncate text-xs text-foreground">{data.email}</div>
              <span className={cn('mt-0.5 inline-flex rounded px-1 py-px text-[9px] font-medium', ROLE_CLASS[data.role] ?? ROLE_CLASS.free)}>
                {ROLE_LABEL[data.role] ?? data.role}
              </span>
            </div>
          </div>

          <div className="my-1 border-t border-border/60" />

          <NavMenuItem
            icon={<UserRound className="h-3.5 w-3.5" />}
            label="个人中心"
            to="/account"
            onClick={() => setOpen(false)}
          />
          {isAdmin && (
            <NavMenuItem
              icon={<Settings className="h-3.5 w-3.5" />}
              label="管理后台"
              to={adminHref}
              onClick={() => setOpen(false)}
            />
          )}
          <div className="my-1 border-t border-border/60" />
          <button
            type="button"
            role="menuitem"
            onClick={() => { setOpen(false); void logout() }}
            className="flex w-full items-center gap-2 rounded-control px-2 py-1.5 text-left text-xs text-danger transition-colors duration-150 hover:bg-danger/10"
          >
            <LogOut className="h-3.5 w-3.5" />
            <span className="flex-1 whitespace-nowrap">退出登录</span>
          </button>
        </div>
      )}
    </div>
  )
}

function NavMenuItem({
  icon, label, to, onClick,
}: {
  icon: React.ReactNode
  label: string
  to: string
  onClick: () => void
}) {
  return (
    <NavLink
      to={to}
      role="menuitem"
      onClick={onClick}
      className="flex w-full items-center gap-2 rounded-control px-2 py-1.5 text-left text-xs text-secondary transition-colors duration-150 hover:bg-elevated/70 hover:text-foreground"
    >
      {icon}
      <span className="flex-1 whitespace-nowrap">{label}</span>
    </NavLink>
  )
}
