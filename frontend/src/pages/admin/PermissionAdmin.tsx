import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, History, RefreshCw, Search, ShieldCheck, Users } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { toast } from '@/components/Toast'
import { api, type AdminUserRow, type PermissionItem, type RoleGroup } from '@/lib/api'
import { useAccountUser } from '@/lib/useAccount'
import { PERM } from '@/lib/permissions'
import { cn } from '@/lib/cn'

const CATALOG_QK = ['rbac', 'catalog'] as const
const USERS_QK = ['rbac', 'users'] as const
const AUDIT_QK = ['rbac', 'audit'] as const

const GROUP_TITLE: Record<string, string> = {
  page: '页面可见性',
  feature: '功能操作',
  admin: '后台管理',
}

const GROUP_HINT: Record<string, string> = {
  page: '没有勾选的页面不会出现在侧栏菜单，直接访问对应路由会显示「无权限」。',
  feature: '写操作与高成本操作的开关（策略编辑、回测运行、AI、导出）。',
  admin: '管理后台分区与接口的硬边界，后端会强制校验。',
}

type Tab = 'users' | 'groups' | 'audit'

/** 超级管理员组不可取消的后台核心权限（与后端 rbac.SUPER_ADMIN_REQUIRED 保持一致） */
const SUPER_ADMIN_FORCED: string[] = [PERM.adminAccess, PERM.usersManage, PERM.rolesManage]

/** 超级管理员的草稿始终带上核心权限，避免保存时被后端以「缺少必需权限」拒绝 */
function draftOf(group: RoleGroup) {
  const base = new Set(group.permissions)
  if (group.key === 'super_admin') for (const key of SUPER_ADMIN_FORCED) base.add(key)
  return base
}

function fmtTime(value: number | null | undefined) {
  if (!value) return '—'
  const d = new Date(value * 1000)
  return d.toLocaleString('zh-CN', { hour12: false })
}

const cellCls = 'px-3 py-2 text-[12px]'
const thCls = 'px-3 py-2 text-left text-[11px] font-medium text-muted whitespace-nowrap'

function SectionCard({ title, extra, children }: { title: React.ReactNode; extra?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rounded-card border border-border bg-elevated/30">
      <div className="flex items-center justify-between gap-3 border-b border-border px-3 py-2">
        <div className="text-[12px] font-medium text-foreground">{title}</div>
        {extra}
      </div>
      {children}
    </section>
  )
}

export function PermissionAdmin({
  canUsers,
  canRoles,
  canAudit,
}: {
  canUsers: boolean
  canRoles: boolean
  canAudit: boolean
}) {
  const qc = useQueryClient()
  const me = useAccountUser()
  const catalog = useQuery({ queryKey: CATALOG_QK, queryFn: api.permissionCatalog })
  const [tab, setTab] = useState<Tab>(canUsers ? 'users' : 'groups')

  const tabs = useMemo(() => {
    const list: Array<{ id: Tab; label: string; icon: typeof Users; enabled: boolean }> = [
      { id: 'users', label: '用户账号', icon: Users, enabled: canUsers },
      { id: 'groups', label: '权限组', icon: ShieldCheck, enabled: canRoles },
      { id: 'audit', label: '变更记录', icon: History, enabled: canAudit },
    ]
    return list.filter(x => x.enabled)
  }, [canUsers, canRoles, canAudit])

  const activeTab: Tab = tabs.some(t => t.id === tab) ? tab : (tabs[0]?.id ?? 'users')

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        title="用户与权限"
        subtitle="账号分组 + 权限矩阵；页面权限决定菜单可见性，后台权限由接口强制校验"
        icon={<ShieldCheck className="h-4 w-4" />}
        right={
          <button
            onClick={() => { qc.invalidateQueries({ queryKey: CATALOG_QK }); qc.invalidateQueries({ queryKey: USERS_QK }) }}
            className="inline-flex items-center gap-1 rounded-btn border border-border bg-elevated/50 px-2.5 py-1.5 text-[11px] text-secondary hover:text-foreground"
          >
            <RefreshCw className="h-3 w-3" />刷新
          </button>
        }
      />

      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto p-3">
        <div className="flex items-center gap-1">
          {tabs.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={cn(
                'inline-flex items-center gap-1.5 rounded-btn px-2.5 py-1.5 text-[12px] transition-colors',
                activeTab === id ? 'bg-elevated text-foreground font-medium' : 'text-foreground/60 hover:bg-elevated/60 hover:text-foreground',
              )}
            >
              <Icon className="h-3.5 w-3.5" />{label}
            </button>
          ))}
        </div>

        {catalog.isLoading && <div className="p-6 text-center text-xs text-muted">加载权限目录…</div>}
        {catalog.error != null && <div className="p-6 text-center text-xs text-danger">权限目录加载失败，请稍后重试</div>}

        {activeTab === 'users' && canUsers && <UsersPanel meId={me.data?.id} />}

        {activeTab === 'groups' && canRoles && catalog.data && (
          <div className="grid gap-3 lg:grid-cols-3">
            {catalog.data.perm_roles.map(role => {
              const group = catalog.data!.role_groups.find(g => g.key === role.key)
              return group ? (
                <RoleGroupCard
                  key={group.key}
                  group={group}
                  permissions={catalog.data!.permissions}
                  canEdit={canRoles}
                />
              ) : null
            })}
          </div>
        )}

        {activeTab === 'groups' && !catalog.data && !catalog.isLoading && (
          <div className="p-6 text-center text-xs text-muted">尚未初始化权限组</div>
        )}

        {activeTab === 'audit' && canAudit && <AuditPanel />}
      </div>
    </div>
  )
}

// ---------------- 用户账号 ----------------

function UsersPanel({ meId }: { meId?: number }) {
  const qc = useQueryClient()
  const [q, setQ] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const pageSize = 20

  const users = useQuery({
    queryKey: [...USERS_QK, query, page],
    queryFn: () => api.adminUsers({ q: query, page, pageSize }),
  })
  const catalog = useQuery({ queryKey: CATALOG_QK, queryFn: api.permissionCatalog })
  const roleOptions = catalog.data?.perm_roles ?? []

  const mutate = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: { perm_role?: string; is_active?: boolean } }) =>
      api.adminUpdateUser(id, patch),
    onSuccess: () => {
      toast('账号权限已更新', 'success')
      qc.invalidateQueries({ queryKey: USERS_QK })
      // 被改的可能是当前登录账号, 同步刷新自身会话
      qc.invalidateQueries({ queryKey: ['account-me'] })
      qc.invalidateQueries({ queryKey: ['account-status'] })
    },
    onError: (err) => toast(err instanceof Error ? err.message : '更新失败', 'error'),
  })

  const total = users.data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / pageSize))

  return (
    <SectionCard
      title={`账号列表${users.data ? ` · ${total}` : ''}`}
      extra={
        <form
          onSubmit={(e) => { e.preventDefault(); setPage(1); setQuery(q.trim()) }}
          className="flex items-center gap-1.5"
        >
          <div className="flex items-center gap-1 rounded-btn border border-border bg-base px-2 py-1">
            <Search className="h-3 w-3 text-muted" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="按邮箱搜索"
              className="w-40 bg-transparent text-[11px] text-foreground outline-none placeholder:text-muted"
            />
          </div>
          <button className="rounded-btn border border-border px-2 py-1 text-[11px] text-secondary hover:text-foreground">搜索</button>
        </form>
      }
    >
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-border">
              <th className={thCls}>邮箱</th>
              <th className={thCls}>账号等级</th>
              <th className={thCls}>权限组</th>
              <th className={thCls}>状态</th>
              <th className={thCls}>最近登录</th>
            </tr>
          </thead>
          <tbody>
            {(users.data?.items ?? []).map((u: AdminUserRow) => {
              const self = meId === u.id
              return (
                <tr key={u.id} className="border-b border-border/60 last:border-0">
                  <td className={cn(cellCls, 'text-foreground')}>
                    <div className="flex items-center gap-1.5">
                      <span className="max-w-[220px] truncate">{u.email}</span>
                      {self && <span className="rounded-full bg-elevated px-1.5 py-0.5 text-[10px] text-muted">我</span>}
                      {u.email_verified && <span className="text-[10px] text-success">已验证</span>}
                    </div>
                  </td>
                  <td className={cn(cellCls, 'text-secondary')}>{u.role}</td>
                  <td className={cellCls}>
                    <select
                      value={u.perm_role}
                      disabled={self || mutate.isPending}
                      onChange={(e) => mutate.mutate({ id: u.id, patch: { perm_role: e.target.value } })}
                      className="rounded-btn border border-border bg-base px-2 py-1 text-[11px] text-foreground disabled:opacity-50"
                    >
                      {roleOptions.map(r => <option key={r.key} value={r.key}>{r.label}</option>)}
                      {!roleOptions.some(r => r.key === u.perm_role) && <option value={u.perm_role}>{u.perm_role_name || u.perm_role}</option>}
                    </select>
                  </td>
                  <td className={cellCls}>
                    <button
                      disabled={self || mutate.isPending}
                      onClick={() => mutate.mutate({ id: u.id, patch: { is_active: !u.is_active } })}
                      className={cn(
                        'rounded-full border px-2 py-0.5 text-[11px] disabled:opacity-50',
                        u.is_active ? 'border-success/40 text-success' : 'border-border text-muted',
                      )}
                    >
                      {u.is_active ? '启用中' : '已禁用'}
                    </button>
                  </td>
                  <td className={cn(cellCls, 'text-muted')}>{fmtTime(u.last_login_at)}</td>
                </tr>
              )
            })}
            {(users.data?.items ?? []).length === 0 && !users.isLoading && (
              <tr><td colSpan={5} className="px-3 py-8 text-center text-xs text-muted">没有匹配的账号</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="flex items-center justify-between border-t border-border px-3 py-1.5 text-[11px] text-muted">
        <span>第 {page} / {pages} 页</span>
        <div className="flex items-center gap-1">
          <button disabled={page <= 1} onClick={() => setPage(p => Math.max(1, p - 1))} className="rounded-btn border border-border p-1 disabled:opacity-40"><ChevronLeft className="h-3 w-3" /></button>
          <button disabled={page >= pages} onClick={() => setPage(p => Math.min(pages, p + 1))} className="rounded-btn border border-border p-1 disabled:opacity-40"><ChevronRight className="h-3 w-3" /></button>
        </div>
      </div>
    </SectionCard>
  )
}

// ---------------- 权限组矩阵 ----------------

function RoleGroupCard({
  group,
  permissions,
  canEdit,
}: {
  group: RoleGroup
  permissions: PermissionItem[]
  canEdit: boolean
}) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState<Set<string>>(() => draftOf(group))
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    if (!dirty) setDraft(draftOf(group))
  }, [group.permissions, group.updated_at, dirty])

  const save = useMutation({
    mutationFn: () => api.updateRoleGroup(group.key, { permissions: [...draft] }),
    onSuccess: () => {
      setDirty(false)
      toast(`「${group.name}」权限已保存`, 'success')
      qc.invalidateQueries({ queryKey: CATALOG_QK })
      qc.invalidateQueries({ queryKey: ['account-me'] })
      qc.invalidateQueries({ queryKey: ['account-status'] })
    },
    onError: (err) => toast(err instanceof Error ? err.message : '保存失败', 'error'),
  })

  const toggle = (key: string) => {
    setDraft(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
    setDirty(true)
  }

  const byGroup = useMemo(() => {
    const map = new Map<string, PermissionItem[]>()
    for (const p of permissions) {
      const list = map.get(p.group) ?? []
      list.push(p)
      map.set(p.group, list)
    }
    return map
  }, [permissions])

  return (
    <section className="flex min-h-0 flex-col rounded-card border border-border bg-elevated/30">
      <div className="border-b border-border px-3 py-2">
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="truncate text-[12px] font-medium text-foreground">{group.name}</div>
            <div className="truncate text-[10px] text-muted">{group.description || group.key}</div>
          </div>
          <span className="shrink-0 rounded-full border border-border px-1.5 py-0.5 text-[10px] text-muted">
            {draft.size} 项
          </span>
        </div>
      </div>

      <div className="flex-1 space-y-3 overflow-auto p-3">
        {['page', 'feature', 'admin'].map(gkey => {
          const items = byGroup.get(gkey) ?? []
          if (items.length === 0) return null
          return (
            <div key={gkey}>
              <div className="mb-0.5 text-[11px] font-medium text-secondary">{GROUP_TITLE[gkey] ?? gkey}</div>
              {GROUP_HINT[gkey] && (
                <div className="mb-1 text-[10px] leading-4 text-muted">{GROUP_HINT[gkey]}</div>
              )}
              <div className="space-y-0.5">
                {items.map(p => {
                  const forced = group.key === 'super_admin'
                    && SUPER_ADMIN_FORCED.includes(p.key)
                  return (
                    <label
                      key={p.key}
                      title={p.description}
                      className={cn(
                        'flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-[11px] hover:bg-elevated/60',
                        !canEdit && 'cursor-not-allowed opacity-70',
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={draft.has(p.key) || forced}
                        disabled={!canEdit || forced}
                        onChange={() => toggle(p.key)}
                        className="h-3 w-3 accent-[hsl(var(--accent-solid))]"
                      />
                      <span className={cn('min-w-0 flex-1 truncate', draft.has(p.key) ? 'text-foreground' : 'text-secondary')}>
                        {p.label}
                      </span>
                    </label>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>

      <div className="flex items-center justify-between border-t border-border px-3 py-1.5">
        <span className="text-[10px] text-muted">
          {group.key === 'super_admin'
            ? '超级管理员保留后台核心权限，不可取消'
            : dirty ? '有未保存修改' : '已保存'}
        </span>
        <button
          disabled={!canEdit || !dirty || save.isPending}
          onClick={() => save.mutate()}
          className="rounded-btn border border-border bg-elevated px-2.5 py-1 text-[11px] text-foreground disabled:opacity-50"
        >
          {save.isPending ? '保存中…' : '保存'}
        </button>
      </div>
    </section>
  )
}

// ---------------- 变更记录 ----------------

function AuditPanel() {
  const audit = useQuery({
    queryKey: AUDIT_QK,
    queryFn: () => api.permissionAudit(100),
  })
  const items = audit.data?.items ?? []
  return (
    <SectionCard title={`权限变更记录${items.length ? ` · ${items.length}` : ''}`}>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-border">
              <th className={thCls}>时间</th>
              <th className={thCls}>操作人</th>
              <th className={thCls}>动作</th>
              <th className={thCls}>对象</th>
            </tr>
          </thead>
          <tbody>
            {items.map((row) => (
              <tr key={String(row.id)} className="border-b border-border/60 last:border-0">
                <td className={cn(cellCls, 'whitespace-nowrap text-muted')}>{fmtTime(Number(row.created_at))}</td>
                <td className={cn(cellCls, 'text-secondary')}>{String(row.actor_email ?? '—')}</td>
                <td className={cn(cellCls, 'text-foreground')}>{String(row.action ?? '')}</td>
                <td className={cn(cellCls, 'text-secondary')}>{String(row.target ?? '')}</td>
              </tr>
            ))}
            {items.length === 0 && (
              <tr><td colSpan={4} className="px-3 py-8 text-center text-xs text-muted">
                {audit.isLoading ? '加载中…' : '暂无权限变更记录'}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </SectionCard>
  )
}
