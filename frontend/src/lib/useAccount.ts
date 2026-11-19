import { useQuery } from '@tanstack/react-query'
import { api, type AccountUser } from './api'

/**
 * 当前邮箱账号用户（多用户账号体系）。
 *
 * 旧「访问密码」会话下 /api/accounts/me 会返回 401；这里把 401 吞成 null，
 * 避免全局 401 拦截器把兼容模式的用户反复踢回登录页。
 * 调用方按 `data` 是否为空区分「邮箱账号 / 旧密码模式」。
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
 * 是否管理员：邮箱账号看 role；旧访问密码模式（无账号会话）后端视为管理员。
 * 用于前台隐藏「去配置 AI/数据源」这类普通用户点不开的入口。
 */
export function useIsAdmin() {
  const me = useAccountUser()
  return me.data === null || me.data?.role === 'admin'
}

/**
 * 会话状态: 同时覆盖多用户邮箱账号与旧「访问密码」单用户模式。
 *
 * 只靠 useAccountUser 无法区分「没登录」与「旧密码模式(无账号会话)」——
 * 两者都返回 null。这里再查 `/api/accounts/status` 与 `/api/auth/status`
 * 的 authenticated 字段, 未登录时 authenticated=false。
 */
export function useSession() {
  // 以服务端会话为准, 不依赖可能被缓存的 accountMe 结果:
  // 退出登录后即使本地还留着旧 user 缓存, accountStatus.authenticated 也会是 false。
  const account = useQuery({
    queryKey: ['account-status'],
    queryFn: () => api.accountStatus(),
    retry: false,
    staleTime: 0,
  })
  const panel = useQuery({
    queryKey: ['auth-status'],
    queryFn: () => api.authStatus(),
    retry: false,
    staleTime: 0,
  })
  const user = account.data?.user ?? null
  const accountAuthenticated = account.data?.authenticated === true
  const panelAuthenticated = panel.data?.authenticated === true
  // isPending 覆盖「已挂载但尚未完成首次请求」(pending + idle), 防止守卫在会话落定前误判未登录
  const loading = account.isPending || panel.isPending
  const authenticated = accountAuthenticated || panelAuthenticated
  // 邮箱账号看角色; 没有账号会话时, 旧访问密码会话(panelAuthenticated)视为管理员
  const isAdmin = authenticated && (user ? user.role === 'admin' : panelAuthenticated)
  return { loading, authenticated, isAdmin, user }
}
