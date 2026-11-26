import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { BadgeCheck, KeyRound, LogOut, MailCheck, ShieldCheck } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { api } from '@/lib/api'
import { useAccountUser } from '@/lib/useAccount'
import { cn } from '@/lib/cn'

const ROLE_LABEL: Record<string, string> = { admin: '管理员', pro: 'Pro', free: '免费' }
const ROLE_CLASS: Record<string, string> = {
  admin: 'bg-accent/15 text-accent',
  pro: 'bg-amber-500/15 text-amber-500',
  free: 'bg-elevated text-muted',
}

function errText(err: unknown): string {
  if (err instanceof Error && err.message) return err.message
  return '操作失败，请稍后重试'
}

/** 个人中心 — 所有登录用户可用：账号信息 / 邮箱验证 / 修改密码 / 退出。 */
export function PersonalSettings() {
  const me = useAccountUser()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const [oldPwd, setOldPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [pwdMsg, setPwdMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [savingPwd, setSavingPwd] = useState(false)

  const [code, setCode] = useState('')
  const [verifyMsg, setVerifyMsg] = useState<string | null>(null)
  const [sendingCode, setSendingCode] = useState(false)
  const [verifying, setVerifying] = useState(false)

  const logout = async () => {
    try { await api.accountLogout() } catch { /* 会话可能已失效 */ }
    qc.clear()
    navigate('/login', { replace: true })
  }

  if (me.isLoading) {
    return <div className="px-5 py-10 text-sm text-muted">加载中…</div>
  }

  // 账号信息读取失败: 不猜测身份, 只提供退出。
  if (!me.data) {
    return (
      <>
        <PageHeader title="个人中心" subtitle="无法读取当前账号信息。" className="pl-14 md:pl-5" />
        <div className="px-4 py-6 sm:px-8">
          <div className="flex max-w-xl items-center justify-between gap-4 rounded-card border border-border bg-surface p-5 text-sm text-secondary">
            <span>请重新登录后再试；若持续失败，请联系管理员检查账号服务。</span>
            <button type="button" onClick={logout}
              className="shrink-0 rounded-btn bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent/90">
              退出登录
            </button>
          </div>
        </div>
      </>
    )
  }

  const user = me.data

  const changePassword = async (e: React.FormEvent) => {
    e.preventDefault()
    setPwdMsg(null)
    if (newPwd.length < 6) {
      setPwdMsg({ ok: false, text: '新密码至少 6 位' })
      return
    }
    setSavingPwd(true)
    try {
      await api.accountChangePassword(oldPwd, newPwd)
      setOldPwd('')
      setNewPwd('')
      setPwdMsg({ ok: true, text: '密码已更新' })
    } catch (err) {
      setPwdMsg({ ok: false, text: errText(err) })
    } finally {
      setSavingPwd(false)
    }
  }

  const sendCode = async () => {
    setVerifyMsg(null)
    setSendingCode(true)
    try {
      const res = await api.accountSendVerify()
      if (res.already_verified) {
        setVerifyMsg('邮箱已验证，无需重复操作。')
      } else if (res.dev_code) {
        setVerifyMsg(`开发模式验证码：${res.dev_code}`)
      } else {
        setVerifyMsg('验证码已发送，请查收邮箱。')
      }
    } catch (err) {
      setVerifyMsg(errText(err))
    } finally {
      setSendingCode(false)
    }
  }

  const verifyEmail = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!code.trim()) return
    setVerifying(true)
    setVerifyMsg(null)
    try {
      await api.accountVerifyEmail(code.trim())
      await qc.invalidateQueries({ queryKey: ['account-me'] })
      setCode('')
      setVerifyMsg('邮箱验证成功。')
    } catch (err) {
      setVerifyMsg(errText(err))
    } finally {
      setVerifying(false)
    }
  }

  return (
    <>
      <PageHeader
        title="个人中心"
        subtitle="管理你的账号、邮箱验证与登录密码；系统配置请由管理员在管理后台操作。"
        className="pl-14 md:pl-5"
        right={
          <button
            onClick={logout}
            className="inline-flex items-center gap-1.5 rounded-btn border border-border px-3 py-1.5 text-xs text-secondary transition-colors hover:border-danger/40 hover:text-danger"
          >
            <LogOut className="h-3.5 w-3.5" />
            退出登录
          </button>
        }
      />

      <div className="px-4 py-5 sm:px-8 sm:py-6">
        <div className="mx-auto flex max-w-3xl flex-col gap-4">
          {/* 账号信息 */}
          <section className="rounded-card border border-border bg-surface p-5">
            <div className="flex items-center gap-2 text-sm font-medium text-foreground">
              <ShieldCheck className="h-4 w-4 text-accent" />
              账号信息
            </div>
            <div className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
              <div>
                <div className="text-[11px] text-muted">邮箱</div>
                <div className="mt-1 flex items-center gap-2 text-foreground">
                  {user.email}
                  {user.email_verified ? (
                    <BadgeCheck className="h-3.5 w-3.5 text-bear" />
                  ) : (
                    <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-500">未验证</span>
                  )}
                </div>
              </div>
              <div>
                <div className="text-[11px] text-muted">角色</div>
                <div className="mt-1">
                  <span className={cn('rounded px-1.5 py-0.5 text-[11px] font-medium', ROLE_CLASS[user.role] ?? ROLE_CLASS.free)}>
                    {ROLE_LABEL[user.role] ?? user.role}
                  </span>
                </div>
              </div>
              <div>
                <div className="text-[11px] text-muted">AI 每日额度</div>
                <div className="mt-1 font-mono text-xs text-secondary">
                  {user.quotas?.ai_daily?.unlimited ? '不限' : `${user.quotas?.ai_daily?.used ?? 0} / ${user.quotas?.ai_daily?.limit ?? 0}`}
                </div>
              </div>
              <div>
                <div className="text-[11px] text-muted">回测每月额度</div>
                <div className="mt-1 font-mono text-xs text-secondary">
                  {user.quotas?.backtest_monthly?.unlimited ? '不限' : `${user.quotas?.backtest_monthly?.used ?? 0} / ${user.quotas?.backtest_monthly?.limit ?? 0}`}
                </div>
              </div>
            </div>
            {user.is_admin && (
              <div className="mt-4 border-t border-border/60 pt-3 text-xs">
                <button onClick={() => navigate('/admin')} className="text-accent hover:underline">
                  进入管理后台 →
                </button>
              </div>
            )}
          </section>

          {/* 邮箱验证 */}
          {!user.email_verified && (
            <section className="rounded-card border border-border bg-surface p-5">
              <div className="flex items-center gap-2 text-sm font-medium text-foreground">
                <MailCheck className="h-4 w-4 text-accent" />
                验证邮箱
              </div>
              <p className="mt-2 text-xs leading-relaxed text-muted">
                验证后可启用「忘记密码」找回账号。验证码有效期有限，请及时提交。
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <button
                  onClick={sendCode}
                  disabled={sendingCode}
                  className="rounded-btn border border-border px-3 py-1.5 text-xs text-secondary transition-colors hover:border-accent/40 hover:text-foreground disabled:opacity-50"
                >
                  {sendingCode ? '发送中…' : '发送验证码'}
                </button>
                <form onSubmit={verifyEmail} className="flex items-center gap-2">
                  <input
                    value={code}
                    onChange={e => setCode(e.target.value)}
                    placeholder="6 位验证码"
                    inputMode="numeric"
                    className="w-32 rounded-input border border-border bg-base px-2.5 py-1.5 text-sm text-foreground outline-none focus:border-accent/50"
                  />
                  <button
                    type="submit"
                    disabled={verifying || !code.trim()}
                    className="rounded-btn bg-accent/15 px-3 py-1.5 text-xs font-medium text-accent transition-colors hover:bg-accent/25 disabled:opacity-50"
                  >
                    {verifying ? '验证中…' : '验证'}
                  </button>
                </form>
              </div>
              {verifyMsg && <div className="mt-2 text-xs text-secondary">{verifyMsg}</div>}
            </section>
          )}

          {/* 修改密码 */}
          <section className="rounded-card border border-border bg-surface p-5">
            <div className="flex items-center gap-2 text-sm font-medium text-foreground">
              <KeyRound className="h-4 w-4 text-accent" />
              修改密码
            </div>
            <form onSubmit={changePassword} className="mt-4 grid gap-3 sm:grid-cols-2">
              <label className="text-xs text-secondary">
                当前密码
                <input
                  type="password"
                  value={oldPwd}
                  onChange={e => setOldPwd(e.target.value)}
                  autoComplete="current-password"
                  className="mt-1 w-full rounded-input border border-border bg-base px-3 py-2 text-sm text-foreground outline-none focus:border-accent/50"
                />
              </label>
              <label className="text-xs text-secondary">
                新密码（至少 6 位）
                <input
                  type="password"
                  value={newPwd}
                  onChange={e => setNewPwd(e.target.value)}
                  autoComplete="new-password"
                  className="mt-1 w-full rounded-input border border-border bg-base px-3 py-2 text-sm text-foreground outline-none focus:border-accent/50"
                />
              </label>
              <div className="sm:col-span-2 flex items-center gap-3">
                <button
                  type="submit"
                  disabled={savingPwd || !oldPwd || !newPwd}
                  className="rounded-btn bg-accent px-4 py-2 text-xs font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
                >
                  {savingPwd ? '保存中…' : '保存新密码'}
                </button>
                {pwdMsg && (
                  <span className={cn('text-xs', pwdMsg.ok ? 'text-bear' : 'text-danger')}>{pwdMsg.text}</span>
                )}
              </div>
            </form>
          </section>
        </div>
      </div>
    </>
  )
}
