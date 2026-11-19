import { useEffect, useRef, useState } from 'react'
import { Check, Palette } from 'lucide-react'
import { cn } from '@/lib/cn'
import { BRAND_THEMES, setBrandTheme, useBrandTheme } from '@/lib/brandTheme'

/**
 * 品牌主题切换 — 顶栏调色盘按钮 + 浮层。
 * 只换强调色（按钮底、选中态、链接），表面与文字不改，所以页面不用跟着调。
 */
export function BrandThemePicker() {
  const current = useBrandTheme()
  const [open, setOpen] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const currentDef = BRAND_THEMES.find(item => item.id === current)

  useEffect(() => {
    if (!open) return
    const onPointer = (event: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false)
        buttonRef.current?.focus()
      }
    }
    document.addEventListener('mousedown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={boxRef} className="relative">
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen(value => !value)}
        title={`品牌主题：${currentDef?.label ?? '雾紫（默认）'}`}
        aria-label="切换品牌主题"
        aria-haspopup="menu"
        aria-expanded={open}
        className="relative flex h-7 w-7 items-center justify-center rounded-control text-secondary transition-colors duration-150 ease-smooth hover:bg-elevated/70 hover:text-foreground"
      >
        <Palette className="h-4 w-4 shrink-0" />
        <span
          className="absolute bottom-0.5 right-0.5 h-1.5 w-1.5 rounded-full ring-1 ring-base"
          style={{ background: currentDef?.swatch }}
        />
      </button>

      {open && (
        <div
          role="menu"
          aria-label="品牌主题"
          className="absolute right-0 top-full z-50 mt-1 w-44 rounded-panel border border-border bg-floating p-1 shadow-2xl"
        >
          {BRAND_THEMES.map(theme => (
            <button
              key={theme.id}
              type="button"
              role="menuitemradio"
              aria-checked={theme.id === current}
              onClick={() => {
                setBrandTheme(theme.id)
                setOpen(false)
                buttonRef.current?.focus()
              }}
              className={cn(
                'flex w-full items-center gap-2 rounded-control px-2 py-1.5 text-left text-[12px] transition-colors duration-150',
                theme.id === current ? 'text-foreground' : 'text-secondary hover:text-foreground',
                'hover:bg-elevated/70',
              )}
            >
              <span
                className="h-3.5 w-3.5 shrink-0 rounded-full ring-1 ring-black/10"
                style={{ background: theme.swatch }}
              />
              <span className="flex-1 whitespace-nowrap">{theme.label}</span>
              {theme.id === current && <Check className="h-3 w-3 shrink-0 text-accent" />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
