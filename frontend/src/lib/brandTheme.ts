import { useEffect, useState } from 'react'
import { hydrateUiPrefs, setUiPref } from '@/lib/uiPrefs'

/**
 * 品牌主题包 — 只覆盖强调色三令牌（solid/text/raw），表面色、文字色、
 * 边框与 A 股语义色都不动，因此切换品牌色不会影响页面可读性。
 *
 * 色值定义在 src/index.css 的 `html[data-brand=...]` 规则里：CSS 声明式的好处是
 * 亮/暗切换（html.dark）自动跟随，无需 JS 再算一遍；本模块只负责选哪一套 +
 * 持久化（localStorage 即时 + 后端副本跨端口）。
 *
 * 首屏由 index.html 内联脚本设置 data-brand，避免刷新时闪一下默认紫。
 */

export type BrandThemeId = 'craft' | 'ocean' | 'jade' | 'amber' | 'rose'

export interface BrandThemeDef {
  id: BrandThemeId
  label: string
  /** 暗色态强调色 — 设置面板色块预览与浏览器 theme-color 共用 */
  swatch: string
  /** 亮色态强调色 — 只给 theme-color 用, 界面仍走 CSS 变量 */
  lightSwatch: string
}

const KEY = 'tf-brand-theme'
const EVENT = 'tf-brand-theme-change'
const THEME_COLOR_SELECTOR = 'meta[name="theme-color"]'

export const BRAND_THEMES: readonly BrandThemeDef[] = [
  { id: 'craft', label: '雾紫（默认）', swatch: '#8B78D8', lightSwatch: '#6F49CA' },
  { id: 'ocean', label: '电光蓝', swatch: '#3B82F6', lightSwatch: '#2563EB' },
  { id: 'jade', label: '青玉', swatch: '#0E9E7C', lightSwatch: '#0B8467' },
  { id: 'amber', label: '琥珀', swatch: '#C2701A', lightSwatch: '#A85A07' },
  { id: 'rose', label: '胭脂', swatch: '#DB5A82', lightSwatch: '#BE2E5C' },
] as const

const VALID = new Set<string>(BRAND_THEMES.map(t => t.id))

function normalize(value: string | null | undefined): BrandThemeId {
  return value && VALID.has(value) ? (value as BrandThemeId) : 'craft'
}

export function getBrandTheme(): BrandThemeId {
  try {
    return normalize(localStorage.getItem(KEY))
  } catch {
    return 'craft'
  }
}

/** 浏览器/桌面壳的 theme-color 跟随品牌包与明暗 — 不读 CSS 变量, 单独同步。 */
export function syncThemeColor() {
  const def = BRAND_THEMES.find(item => item.id === getBrandTheme()) ?? BRAND_THEMES[0]
  const dark = document.documentElement.classList.contains('dark')
  let meta = document.querySelector(THEME_COLOR_SELECTOR)
  if (!meta) {
    meta = document.createElement('meta')
    meta.setAttribute('name', 'theme-color')
    document.head.appendChild(meta)
  }
  meta.setAttribute('content', dark ? def.swatch : def.lightSwatch)
}

/** 把品牌包写到 <html data-brand>；craft 是默认，移除属性即可。 */
export function applyBrandTheme(id: BrandThemeId = getBrandTheme()) {
  const theme = normalize(id)
  if (theme === 'craft') document.documentElement.removeAttribute('data-brand')
  else document.documentElement.setAttribute('data-brand', theme)
  syncThemeColor()
}

export function setBrandTheme(id: BrandThemeId) {
  setUiPref(KEY, id)
  applyBrandTheme(id)
  window.dispatchEvent(new CustomEvent(EVENT, { detail: id }))
}

/** 订阅当前品牌包；跨标签页与后端回填后都会重新应用。 */
export function useBrandTheme(): BrandThemeId {
  const [theme, setTheme] = useState<BrandThemeId>(getBrandTheme)
  useEffect(() => {
    let active = true
    const sync = () => {
      if (!active) return
      const value = getBrandTheme()
      applyBrandTheme(value)
      setTheme(value)
    }
    applyBrandTheme()
    window.addEventListener(EVENT, sync)
    window.addEventListener('storage', sync)
    window.addEventListener('tf-theme-change', syncThemeColor)  // 明暗切换时重算 theme-color
    // 桌面端换端口后 localStorage 可能没有这枚键，回填后要再应用一次
    void hydrateUiPrefs().then(sync)
    return () => {
      active = false
      window.removeEventListener(EVENT, sync)
      window.removeEventListener('storage', sync)
    }
  }, [])
  return theme
}
