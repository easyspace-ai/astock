/**
 * 登录页 — 邮箱注册 / 登录 / 邮箱验证 / 忘记密码（邮箱账号体系）。
 *
 * 五态（由 /api/accounts/status 驱动）:
 *   - initialized=false → 显示「注册」（系统首个账号自动成为超级管理员）
 *   - initialized=true, authenticated=false → 登录页（可切换注册 / 忘记密码）
 *   - authenticated=true → 直接进入面板
 *   - register 成功后 → 「验证邮箱」步骤（可跳过）
 *   - forgot / reset → 忘记密码自助重置流程
 */
import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { ArrowLeft, BadgeCheck, Eye, EyeOff, Loader2, Lock, Mail, MailCheck, ShieldAlert, Sparkles, UserPlus } from 'lucide-react'
import { api, type AccountUser } from '@/lib/api'
import { Logo } from '@/components/Logo'
import { cn } from '@/lib/cn'

type Mode = 'login' | 'register' | 'verify' | 'forgot' | 'reset'

/**
 * 登录后的落地地址 — 守卫重定向会带上 `?redirect=/indices?...`, 登录页必须回到原目标。
 * 只接受站内绝对路径: 拒绝 `//evil.com` 这类协议相对写法, 避免开放重定向。
 */
export function redirectTarget(): string {
  const raw = new URLSearchParams(window.location.search).get('redirect') || '/'
  if (!raw.startsWith('/') || raw.startsWith('//')) return '/'
  return raw
}

type AccountStatus = { initialized: boolean; authenticated: boolean; user: AccountUser | null }

export function Auth({ initialMode = 'login' }: { initialMode?: Mode }) {
  const navigate = useNavigate()
  // 挂载时一次性取定落地地址: 之后再读 window.search 已被上一跳清空, 会退回 '/' 造成二次弹回
  const [redirectTo] = useState(redirectTarget)
  const [mode, setMode] = useState<Mode>(initialMode)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [code, setCode] = useState('')
  const [showPwd, setShowPwd] = useState(false)
  const [localError, setLocalError] = useState('')
  const [notice, setNotice] = useState('')
  const [devCode, setDevCode] = useState('')
  const [status, setStatus] = useState<AccountStatus | null>(null)
  const [statusError, setStatusError] = useState('')

  useEffect(() => {
    api.accountStatus()
      .then(s => {
        setStatus(s)
        if (s.authenticated) {
          navigate(redirectTo, { replace: true })
        } else if (!s.initialized) {
          setMode('register') // 系统未初始化 → 注册首个管理员
        }
      })
      .catch(() => setStatusError('无法读取账号状态，请确认服务已启动后重试'))
  }, [navigate])

  const go = (m: Mode) => { setMode(m); setLocalError(''); setNotice('') }

  const submitMut = useMutation({
    mutationFn: async () => {
      if (mode === 'register') return api.accountRegister(email, password)
      if (mode === 'login') return api.accountLogin(email, password)
      if (mode === 'verify') return api.accountVerifyEmail(code)
      if (mode === 'forgot') return api.accountForgotPassword(email)
      return api.accountResetPassword(email, code, password) // reset
    },
    onSuccess: (data: any) => {
      if (mode === 'register') {
        // 注册成功 → 进入邮箱验证步骤（自动发送验证码）
        go('verify')
        setDevCode('')
        api.accountSendVerify()
          .then(r => {
            if (r.sent === false && r.dev_code) setDevCode(r.dev_code)
            setNotice(r.sent === false
              ? '未配置邮件服务，当前为开发模式：验证码见下方（生产环境请在「设置 → 邮件推送」配置 SMTP）'
              : '验证码已发送至你的邮箱（15 分钟内有效）')
          })
          .catch(() => setNotice('验证码发送失败，可稍后在个人菜单重新发送'))
        return
      }
      if (mode === 'verify') {
        navigate(redirectTo, { replace: true })
        return
      }
      if (mode === 'forgot') {
        if (data?.dev_code) {
          setDevCode(data.dev_code)
          setNotice('开发模式：重置验证码见下方（生产环境将发送至你的邮箱）')
          go('reset')
        } else {
          setNotice('若该邮箱已注册并已验证，重置验证码已发送（15 分钟内有效）。未收到？请先登录并在「邮箱验证」中完成验证。')
          go('reset')
        }
        return
      }
      if (mode === 'reset') {
        setNotice('密码已重置，请使用新密码登录')
        go('login')
        return
      }
      // login
      navigate(redirectTo, { replace: true })
    },
    onError: (err: any) => {
      setLocalError(err?.message || '操作失败')
    },
  })

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    setLocalError('')
    const emailOk = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/.test(email.trim())
    if (mode !== 'login' && mode !== 'verify' && mode !== 'reset' && mode !== 'forgot' && !emailOk) {
      setLocalError('请输入正确的邮箱地址')
      return
    }
    if (mode === 'register' || mode === 'reset') {
      if (password.length < 6) { setLocalError('密码至少 6 位'); return }
      if (password !== confirmPassword) { setLocalError('两次密码不一致'); return }
    }
    if ((mode === 'verify' || mode === 'reset') && !code.trim()) { setLocalError('请输入验证码'); return }
    submitMut.mutate()
  }

  if (statusError) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-base px-4 text-center">
        <ShieldAlert className="h-6 w-6 text-danger" />
        <div className="text-sm text-foreground">{statusError}</div>
        <button type="button" onClick={() => window.location.reload()}
          className="h-9 rounded-btn bg-accent px-4 text-sm font-medium text-white hover:bg-accent/90">
          重新加载
        </button>
      </div>
    )
  }

  if (status === null) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-base">
        <Loader2 className="h-6 w-6 animate-spin text-muted" />
      </div>
    )
  }

  const title = ({ login: '登录', register: '创建账号', verify: '验证邮箱', forgot: '忘记密码', reset: '重置密码' } as const)[mode]
  const subtitle = ({
    login: '请输入邮箱与密码以继续',
    register: status.initialized === false ? '首个注册账号自动成为超级管理员' : '创建账号，立即可用（免费版，可随时升级）',
    verify: '输入验证码完成邮箱验证（可跳过）',
    forgot: '输入注册邮箱，验证码将发送至已验证邮箱',
    reset: '输入验证码并设置新密码',
  } as const)[mode]

  const submitLabel = ({
    login: '登录', register: '注册并进入', verify: '完成验证并进入', forgot: '发送验证码', reset: '重置密码',
  } as const)[mode]

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-base px-4">
      {/* 背景辉光(与 Onboarding 风格一致) */}
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_30%_20%,rgba(139,92,246,0.15),transparent_40%),radial-gradient(circle_at_70%_80%,rgba(59,130,246,0.12),transparent_40%)]" />

      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
        className="relative w-full max-w-sm"
      >
        {/* Logo */}
        <div className="mb-6 flex flex-col items-center gap-2">
          <Logo className="h-10 w-10" />
          <h1 className="text-lg font-semibold text-foreground">财富引擎</h1>
        </div>

        <div className="rounded-card border border-border bg-surface/90 p-6 shadow-2xl backdrop-blur">
          {/* 标题区: 图标 + 文案随模式切换 */}
          <div className="mb-5 flex items-center gap-2.5">
            <div className={cn(
              'grid h-9 w-9 place-items-center rounded-lg',
              mode === 'register' ? 'bg-accent/15 text-accent'
                : (mode === 'verify' || mode === 'forgot' || mode === 'reset') ? 'bg-emerald-500/15 text-emerald-400'
                : 'bg-purple-500/15 text-purple-400',
            )}>
              {mode === 'register' ? <UserPlus className="h-5 w-5" />
                : (mode === 'verify' || mode === 'forgot' || mode === 'reset') ? <MailCheck className="h-5 w-5" />
                : <Lock className="h-5 w-5" />}
            </div>
            <div>
              <div className="text-sm font-medium text-foreground">{title}</div>
              <div className="text-[11px] text-muted">{subtitle}</div>
            </div>
          </div>

          <form onSubmit={handleSubmit} className="space-y-3">
            {/* 邮箱(非 verify) */}
            {mode !== 'verify' && (
              <div className="relative">
                <input
                  type="email"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  placeholder="邮箱地址"
                  autoFocus={mode === 'login' || mode === 'register' || mode === 'forgot'}
                  autoComplete="email"
                  className="h-10 w-full rounded-btn border border-border bg-base pl-9 pr-3 text-sm text-foreground outline-none transition-colors focus:border-accent/50"
                />
                <Mail className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" />
              </div>
            )}

            {/* 验证码(verify / reset) */}
            {(mode === 'verify' || mode === 'reset') && (
              <div className="relative">
                <input
                  type="text"
                  value={code}
                  onChange={e => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                  placeholder={mode === 'verify' ? '6 位邮箱验证码' : '6 位重置验证码'}
                  autoFocus
                  inputMode="numeric"
                  className="h-10 w-full rounded-btn border border-border bg-base pl-3 pr-3 text-center text-base tracking-[0.5em] text-foreground outline-none transition-colors focus:border-accent/50"
                />
              </div>
            )}

            {/* 密码输入 */}
            {(mode === 'login' || mode === 'register' || mode === 'reset') && (
              <div className="relative">
                <input
                  type={showPwd ? 'text' : 'password'}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder={mode === 'reset' ? '新密码 (至少 6 位)' : '密码 (至少 6 位)'}
                  autoFocus={mode === 'reset'}
                  autoComplete={mode === 'register' || mode === 'reset' ? 'new-password' : 'current-password'}
                  className="h-10 w-full rounded-btn border border-border bg-base px-3 pr-9 text-sm text-foreground outline-none transition-colors focus:border-accent/50"
                />
                <button
                  type="button"
                  onClick={() => setShowPwd(s => !s)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-muted hover:text-foreground"
                  tabIndex={-1}
                >
                  {showPwd ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            )}

            {/* 确认密码(注册 / 重置) */}
            {(mode === 'register' || mode === 'reset') && (
              <input
                type={showPwd ? 'text' : 'password'}
                value={confirmPassword}
                onChange={e => setConfirmPassword(e.target.value)}
                placeholder="再次输入新密码"
                autoComplete="new-password"
                className="h-10 w-full rounded-btn border border-border bg-base px-3 text-sm text-foreground outline-none transition-colors focus:border-accent/50"
              />
            )}

            {/* 开发模式验证码 / 提示 */}
            {devCode && (mode === 'verify' || mode === 'reset') && (
              <div className="flex items-center justify-center gap-2 rounded-btn bg-emerald-500/10 px-3 py-2.5">
                <BadgeCheck className="h-4 w-4 text-emerald-400" />
                <span className="text-base font-semibold tracking-[0.3em] text-emerald-400">{devCode}</span>
              </div>
            )}

            {/* 提示 / 错误 */}
            {notice && !localError && (
              <div className="flex items-start gap-1.5 rounded-btn bg-accent/10 px-3 py-2 text-[11px] leading-relaxed text-accent">
                <MailCheck className="mt-px h-3.5 w-3.5 shrink-0" />
                <span>{notice}</span>
              </div>
            )}
            {(localError) && (
              <div className="flex items-start gap-1.5 rounded-btn bg-danger/10 px-3 py-2 text-[11px] text-danger">
                <ShieldAlert className="mt-px h-3.5 w-3.5 shrink-0" />
                <span>{localError}</span>
              </div>
            )}

            <button
              type="submit"
              disabled={submitMut.isPending || (mode !== 'verify' && mode !== 'forgot' && !email) || (mode === 'verify' && !code) || (mode === 'reset' && (!code || !password))}
              className="inline-flex h-10 w-full items-center justify-center gap-1.5 rounded-btn bg-accent text-sm font-medium text-white transition-colors hover:bg-accent/90 disabled:opacity-50"
            >
              {submitMut.isPending ? (
                <><Loader2 className="h-4 w-4 animate-spin" />处理中…</>
              ) : submitLabel}
            </button>
          </form>

          {/* 底部导航 */}
          <div className="mt-4 flex items-center justify-between text-[11px] text-muted">
            {mode === 'login' ? (
              <>
                <span>
                  还没有账号？{' '}
                  <button type="button" onClick={() => go('register')} className="text-accent underline-offset-2 hover:underline">
                    免费注册
                  </button>
                </span>
                <button type="button" onClick={() => go('forgot')} className="underline-offset-2 hover:underline hover:text-foreground">忘记密码？</button>
              </>
            ) : mode === 'register' || mode === 'verify' ? (
              <button
                type="button"
                onClick={() => (mode === 'verify' ? navigate('/', { replace: true }) : go('login'))}
                className="inline-flex items-center gap-1 text-accent underline-offset-2 hover:underline"
              >
                <ArrowLeft className="h-3 w-3" />{mode === 'verify' ? '跳过，直接进入（稍后验证）' : '已有账号？去登录'}
              </button>
            ) : (
              <button type="button" onClick={() => go('login')} className="inline-flex items-center gap-1 text-accent underline-offset-2 hover:underline">
                <ArrowLeft className="h-3 w-3" />返回登录
              </button>
            )}
          </div>
        </div>

        <div className="mt-4 flex items-center justify-center gap-1.5 text-[10px] text-muted/60">
          <Sparkles className="h-3 w-3" />
          自托管量化工作台 · 数据完全掌握在自己手里
        </div>
      </motion.div>
    </div>
  )
}
