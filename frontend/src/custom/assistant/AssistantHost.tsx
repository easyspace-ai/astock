/**
 * AI 助手常驻宿主 — 挂载于 layout.navigation.extra 插槽(全站常驻渲染点),
 * 本身不渲染任何可见 UI。职责:
 * ① 按当前路由上报页面上下文(仅影响后续轮次提示);
 * ② 全局快捷键 ⌘K/Ctrl+K 跳转到助手页面。
 *
 * 悬浮球与右滑抽屉已下线, 对话入口由侧栏导航「AI 助手」菜单项承载。
 */
import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import type { FrontendSlotContextMap } from '@/extensions/types'
import { setPageContext } from './store'

type NavigationContext = FrontendSlotContextMap['layout.navigation.extra']
type AnySlotContext = FrontendSlotContextMap[keyof FrontendSlotContextMap]

// 与核心侧栏导航文案保持一致的轻量映射(仅用于上下文提示, 展示不走这里)。
const PAGE_LABELS: Record<string, string> = {
  '/': '看板',
  '/watchlist': '自选',
  '/screener': '策略',
  '/factors': '因子',
  '/backtest': '回测',
  '/stock-analysis': '个股分析',
  '/limit-ladder': '连板梯队',
  '/concept-analysis': '概念板块',
  '/industry-analysis': '行业板块',
  '/financials': '财务概况',
  '/monitor': '监控中心',
  '/regime': '市场环境',
  '/abnormal': '异动监控',
  '/lots': '持仓提醒',
  '/signals': '信号库',
  '/review': '复盘',
  '/indices': '指数',
  '/admin': '管理后台',
  '/admin/data': '数据管理',
}

export function AssistantHost(props: AnySlotContext) {
  const { pathname } = props as NavigationContext
  const navigate = useNavigate()

  useEffect(() => {
    setPageContext({ page: PAGE_LABELS[pathname] ?? '' })
  }, [pathname])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        navigate('/assistant')
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [navigate])

  return null
}
