// 主题管理 — 暗色(默认) / 亮色切换
//
// 机制:
//   - localStorage('tf-theme') 即时缓存 + 后端偏好副本，默认 dark
//   - 生效方式: html.dark class (index.css 的 CSS variables + Tailwind darkMode:class)
//   - index.html 里有预渲染内联脚本, 首屏前就设好 class, 避免闪烁 (FOUC)
//   - UI token (bg-surface/text-foreground 等) 自动跟随;
//     图表画布不吃 CSS 变量, 统一走 useChartTheme() 取调色板
import { useEffect, useMemo, useState } from 'react'
import { hydrateUiPrefs, setUiPref } from '@/lib/uiPrefs'
import { syncThemeColor } from '@/lib/brandTheme'

const KEY = 'tf-theme'
const EVENT = 'tf-theme-change'

export type Theme = 'dark' | 'light'

export function getTheme(): Theme {
  try {
    return localStorage.getItem(KEY) === 'light' ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

/** 恢复后端偏好时只应用主题，不把默认值重新写回后端。 */
export function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle('dark', theme === 'dark')
  syncThemeColor()  // 浏览器 chrome 色跟随品牌包, 不读 CSS 变量
  window.dispatchEvent(new CustomEvent(EVENT, { detail: theme }))
}

export function setTheme(theme: Theme) {
  setUiPref(KEY, theme)
  applyTheme(theme)
}

export function toggleTheme(): Theme {
  const next: Theme = getTheme() === 'dark' ? 'light' : 'dark'
  setTheme(next)
  return next
}

/** 订阅当前主题 (本页切换 + 其他标签页切换均同步)。 */
export function useTheme(): Theme {
  const [theme, set] = useState<Theme>(getTheme)
  useEffect(() => {
    let active = true
    const onChange = () => {
      if (!active) return
      const value = getTheme()
      document.documentElement.classList.toggle('dark', value === 'dark')
      syncThemeColor()
      set(value)
    }
    window.addEventListener(EVENT, onChange)
    window.addEventListener('storage', onChange)  // 跨标签页同步
    // 登录页在同一 SPA 内跳转，首次回填若为 401，挂载后仍须重新恢复主题。
    void hydrateUiPrefs().then(onChange)
    return () => {
      active = false
      window.removeEventListener(EVENT, onChange)
      window.removeEventListener('storage', onChange)
    }
  }, [])
  return theme
}

// ================================================================
// 图表调色板 — ECharts / lightweight-charts 画布不吃 CSS 变量,
// 所有图表组件统一从这里取色, 主题切换时依赖 useTheme 重建 option。
// bull/bear/accent 等语义色双主题一致, 不在此重复定义。
// ================================================================

export interface ChartTheme {
  /** 轴刻度/图例等常规文字 */
  text: string
  /** 信息条/图例里的强调文字 */
  textStrong: string
  /** 网格线 */
  grid: string
  /** 轴线/边框 */
  border: string
  /** 十字光标线 */
  crosshair: string
  /** 十字光标轴标签背景 */
  crosshairLabelBg: string
  /** tooltip 背景 */
  tooltipBg: string
  /** tooltip 边框 */
  tooltipBorder: string
  /** tooltip 文字 */
  tooltipText: string
  /** 半透明信息条背景 (K线图左上角 OHLC 条) */
  infoBarBg: string
  /** dataZoom 滑块填充 */
  zoomFill: string
  /** 分时图均价线以外的弱填充 */
  fillSubtle: string
}

// 图表调色板的兜底值: 仅用于没有 DOM 的环境 (单测 / SSR)。运行期一律从壳层令牌解析,
// 保证坐标轴、网格、tooltip 与卡片边、文字是同一套灰, 并随明暗自动切换。
const FALLBACK_DARK: ChartTheme = {
  text: '#A1A1AA',
  textStrong: '#E4E4E7',
  grid: 'rgba(255,255,255,0.06)',
  border: '#27272A',
  crosshair: 'rgba(255,255,255,0.25)',
  crosshairLabelBg: '#333',
  tooltipBg: 'rgba(24,24,27,0.95)',
  tooltipBorder: 'rgba(255,255,255,0.1)',
  tooltipText: '#E4E4E7',
  infoBarBg: 'rgba(39,39,42,0.6)',
  zoomFill: 'rgba(255,255,255,0.06)',
  fillSubtle: 'rgba(255,255,255,0.04)',
}

const FALLBACK_LIGHT: ChartTheme = {
  text: '#71717A',
  textStrong: '#27272A',
  grid: 'rgba(0,0,0,0.06)',
  border: '#E4E4E7',
  crosshair: 'rgba(0,0,0,0.3)',
  crosshairLabelBg: '#52525B',
  tooltipBg: 'rgba(255,255,255,0.97)',
  tooltipBorder: 'rgba(0,0,0,0.1)',
  tooltipText: '#27272A',
  infoBarBg: 'rgba(244,244,245,0.85)',
  zoomFill: 'rgba(0,0,0,0.06)',
  fillSubtle: 'rgba(0,0,0,0.04)',
}

/** 读一个 HSL 令牌 (值形如 "210 6% 87%") 并转成 canvas 能吃下的颜色。 */
function token(name: string, fallback: string, alpha?: number): string {
  if (typeof document === 'undefined') return fallback
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  if (!raw) return fallback
  // 令牌可能是别名 (var(--text-primary)): 浏览器已解析, 这里再兜一层原始 var()
  if (raw.startsWith('var(')) return fallback
  return alpha === undefined ? `hsl(${raw})` : `hsl(${raw} / ${alpha})`
}

export function chartTheme(theme: Theme): ChartTheme {
  const fb = theme === 'dark' ? FALLBACK_DARK : FALLBACK_LIGHT
  return {
    text: token('--text-secondary', fb.text),
    textStrong: token('--text-primary', fb.textStrong),
    grid: token('--border-default', fb.grid, 0.55),
    border: token('--border-default', fb.border),
    crosshair: token('--text-primary', fb.crosshair, 0.35),
    crosshairLabelBg: token('--surface-raised', fb.crosshairLabelBg),
    tooltipBg: token('--surface-floating', fb.tooltipBg, 0.96),
    tooltipBorder: token('--border-default', fb.tooltipBorder, 0.9),
    tooltipText: token('--text-primary', fb.tooltipText),
    infoBarBg: token('--surface-sunken', fb.infoBarBg, 0.72),
    zoomFill: token('--text-primary', fb.zoomFill, 0.06),
    fillSubtle: token('--text-primary', fb.fillSubtle, 0.04),
  }
}

/** hook: 当前主题的图表调色板 (主题切换自动触发重渲染)。 */
export function useChartTheme(): ChartTheme {
  const theme = useTheme()
  // 图表 effect 依赖调色板对象: 必须随 theme 稳定，否则每次渲染都会触发一次 setOption
  return useMemo(() => chartTheme(theme), [theme])
}
