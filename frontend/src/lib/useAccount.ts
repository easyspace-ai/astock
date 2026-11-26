import { useQuery } from '@tanstack/react-query'
import { api, type AccountUser } from './api'
import { PERM, permissionMatches } from './permissions'

/**
 * 当前邮箱账号用户（系统唯一认证体系）。
 *
 * 未登录时 /api/accounts/me 返回 401；这里把 401 吞成 null，
 * 避免全局 401 拦截器反复把用户踢回登录页。
 * `null` 表示未登录或账号信息不可用，调用方不得据此授予权限。
 */
export function useAccountUser() {
  return useQuery<AccountUser | null>({
    queryKey: ['account-me'],
    queryFn: async () => {
      try {
        return (await api.accountMe()).user
      } catch {
        return null
      }
    },
    retry: false,
    staleTime: 60_000,
  })
}

/**
 * 单个用户是否持有某权限码。
 * 无账号数据（未登录/加载失败）一律无权限；权限字段缺失时退化为「role=admin」。
 */
export function userHasPermission(user: AccountUser | null | undefined, key: string): boolean {
  if (!user) return false
  if (user.perm_role === 'super_admin') return true
  if (Array.isArray(user.permissions)) return permissionMatches(user.permissions, key)
  return user.role === 'admin'
}

/**
 * 当前会话的权限查询：菜单可见性、路由守卫、后台分区都走这里。
 * 未登录时全部返回 false（登录页由会话守卫负责）。
 */
export function usePermissions() {
  const me = useAccountUser()
  const user = me.data ?? null
  const codes = user?.permissions
  const can = (key: string) => {
    if (me.isPending || !user) return false
    if (user.perm_role === 'super_admin') return true
    if (Array.isArray(codes)) return permissionMatches(codes, key)
    return user.role === 'admin'
  }
  return {
    loading: me.isPending,
    user,
    codes: Array.isArray(codes) ? codes : [],
    can,
    canAny: (keys: string[]) => keys.some((k) => can(k)),
    isSuperAdmin: user?.perm_role === 'super_admin',
    canAdmin: can(PERM.adminAccess),
  }
}

/**
 * 是否管理员：以 admin.access 权限码为准。
 * 用于前台隐藏「去配置 AI/数据源」这类普通用户点不开的入口。
 */
export function useIsAdmin() {
  const me = useAccountUser()
  if (me.isPending) return false
  return userHasPermission(me.data, PERM.adminAccess)
}

/**
 * 会话状态: 以邮箱账号会话为唯一依据。
 *
 * 以服务端 `/api/accounts/status` 为准, 不依赖可能被缓存的 accountMe 结果:
 * 退出登录后即使本地还留着旧 user 缓存, accountStatus.authenticated 也会是 false。
 */
export function useSession() {
  const account = useQuery({
    queryKey: ['account-status'],
    queryFn: () => api.accountStatus(),
    retry: false,
    staleTime: 0,
  })
  const user = account.data?.user ?? null
  const authenticated = account.data?.authenticated === true
  // isPending 覆盖「已挂载但尚未完成首次请求」(pending + idle), 防止守卫在会话落定前误判未登录
  const loading = account.isPending
  // 管理后台入口以 admin.access 权限码为准
  const isAdmin = authenticated && userHasPermission(user, PERM.adminAccess)
  return { loading, authenticated, isAdmin, user }
}
