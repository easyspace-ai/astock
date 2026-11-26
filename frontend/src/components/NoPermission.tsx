import { Link } from 'react-router-dom'
import { Lock } from 'lucide-react'
import { useAccountUser } from '@/lib/useAccount'

/**
 * 无权限态：直接访问没有权限的页面时展示，不静默跳转。
 * 同时用于前台路由守卫与管理后台分区守卫。
 */
export function NoPermission({
  title = '无权限访问',
  detail,
}: {
  title?: string
  detail?: string
}) {
  const me = useAccountUser()
  const roleName = me.data?.perm_role_name
  return (
    <div className="grid min-h-[60vh] place-items-center px-6 py-10">
      <div className="w-full max-w-md rounded-card border border-border bg-elevated/40 p-6 text-center">
        <div className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-full border border-border bg-base text-muted">
          <Lock className="h-4 w-4" />
        </div>
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
        <p className="mt-1.5 text-xs leading-5 text-muted">
          {detail ?? '当前账号没有该页面的访问权限。如需使用，请联系超级管理员调整权限组。'}
        </p>
        {me.data && (
          <p className="mt-2 text-[11px] text-secondary">
            {me.data.email} · {roleName ?? me.data.perm_role ?? '未分配权限组'}
          </p>
        )}
        <div className="mt-4 flex items-center justify-center gap-2">
          <Link
            to="/"
            className="rounded-btn border border-border bg-elevated/60 px-3 py-1.5 text-[12px] text-foreground transition-colors hover:border-accent/40"
          >
            返回看板
          </Link>
          <Link
            to="/account"
            className="rounded-btn border border-border px-3 py-1.5 text-[12px] text-secondary transition-colors hover:text-foreground"
          >
            账号设置
          </Link>
        </div>
      </div>
    </div>
  )
}
